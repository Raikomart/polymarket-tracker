"""
detector.py — Three detection signals for insider activity.

Signal 1: Outcome Bucket Clustering
Signal 2: Coordinated Wallet Detection (shared Polygon funder)
Signal 3: Fresh Wallet + Concentrated Bet scoring

All API field names match the live Polymarket response schemas
(verified April 2026).
"""

import asyncio
import logging
import time
import json
from datetime import datetime, timedelta, timezone
from itertools import combinations

import httpx

import db

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────

# Tag slugs / labels that match our 5 target categories.
# Gamma API returns tags as objects with {label, slug}.
TARGET_SLUGS = {
    "politics", "economics", "economy", "military", "elections",
    "election", "policy", "government", "inflation", "gdp",
    "federal-reserve", "federal reserve", "nato", "war",
    "sanctions", "geopolitics", "interest-rate", "interest rate",
}

GAMMA_API = "https://gamma-api.polymarket.com/events"
DATA_API_TRADES = "https://data-api.polymarket.com/trades"
DATA_API_ACTIVITY = "https://data-api.polymarket.com/activity"
POLYGONSCAN_API = "https://api.polygonscan.com/api"

CLUSTER_WINDOW_HOURS = 48
MIN_CLUSTER_WALLETS = 3

MAX_RETRIES = 3
RETRY_BACKOFF = 2

# Concurrency: how many API calls to run in parallel.
# Polymarket data-api enforces ~5 req/s — keep this low to avoid 429s.
MAX_CONCURRENT_REQUESTS = 4

# Maximum events to paginate through from Gamma API.
MAX_EVENT_PAGES = 10           # 10 pages × 100 = up to 1 000 events

# Top N markets per category (by 1-week volume), keeping the scan tight.
TOP_PER_CATEGORY = 20

# Hard caps to guarantee the scan finishes in ~60 s.
MAX_WALLETS_TO_INVESTIGATE = 50

# 5 canonical category buckets → map every TARGET_SLUG into one.
CATEGORY_MAP = {
    "politics": "politics", "government": "politics",
    "economics": "economics", "economy": "economics",
    "inflation": "economics", "gdp": "economics",
    "federal-reserve": "economics", "federal reserve": "economics",
    "interest-rate": "economics", "interest rate": "economics",
    "military": "military", "nato": "military",
    "war": "military", "sanctions": "military",
    "geopolitics": "military",
    "elections": "elections", "election": "elections",
    "policy": "policies",
}


# ── HTTP helpers ────────────────────────────────────────────────

async def _fetch_json(client: httpx.AsyncClient, url: str,
                      params: dict | None = None) -> dict | list | None:
    """GET with retries and exponential backoff."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = RETRY_BACKOFF * (2 ** attempt)
                logger.warning("Rate limited on %s — waiting %ss", url, wait)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning("Request to %s failed (attempt %d): %s",
                           url, attempt + 1, exc)
            await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
    return None


# ── Step 1: Discover markets ───────────────────────────────────

def _extract_tag_set(tags_raw) -> set[str]:
    """
    Gamma API tags come as a list of objects:
      [{"id": "2", "label": "Politics", "slug": "politics", ...}, ...]
    Extract lowered slugs + labels for matching.
    """
    result = set()
    if not tags_raw:
        return result
    if isinstance(tags_raw, str):
        try:
            tags_raw = json.loads(tags_raw)
        except json.JSONDecodeError:
            return {t.strip().lower() for t in tags_raw.split(",")}

    if isinstance(tags_raw, list):
        for tag in tags_raw:
            if isinstance(tag, dict):
                for key in ("slug", "label"):
                    val = tag.get(key, "")
                    if val:
                        result.add(val.lower().strip())
            elif isinstance(tag, str):
                result.add(tag.lower().strip())
    return result


async def fetch_target_markets(client: httpx.AsyncClient) -> list[dict]:
    """Pull active events from Gamma API filtered to target categories."""
    all_events: list[dict] = []
    offset = 0
    limit = 100
    pages_fetched = 0

    while pages_fetched < MAX_EVENT_PAGES:
        data = await _fetch_json(client, GAMMA_API, {
            "active": "true", "closed": "false",
            "limit": limit, "offset": offset,
        })
        if not data:
            break
        all_events.extend(data)
        pages_fetched += 1
        if len(data) < limit:
            break
        offset += limit
        await asyncio.sleep(0.3)

    # Bucket candidates by canonical category, keeping volume for sorting.
    # Structure: {category: [market_dict, ...]}
    category_buckets: dict[str, list[dict]] = {
        "politics": [], "economics": [], "military": [],
        "elections": [], "policies": [],
    }

    for event in all_events:
        tag_set = _extract_tag_set(event.get("tags"))
        matching = tag_set & TARGET_SLUGS
        if not matching:
            continue

        # Pick the first canonical category this event maps to.
        canon = None
        for slug in matching:
            canon = CATEGORY_MAP.get(slug)
            if canon:
                break
        if not canon:
            continue

        markets = event.get("markets") or []
        for mkt in markets:
            if mkt.get("closed"):
                continue

            cond_id = mkt.get("conditionId") or ""
            question = mkt.get("question") or event.get("title", "")
            end_date = mkt.get("endDate") or event.get("endDate") or ""
            volume = 0.0
            try:
                volume = float(mkt.get("volume1wk") or mkt.get("volumeNum") or 0)
            except (ValueError, TypeError):
                pass

            tokens = mkt.get("clobTokenIds")
            if isinstance(tokens, str):
                try:
                    tokens = json.loads(tokens)
                except json.JSONDecodeError:
                    tokens = []

            outcomes = mkt.get("outcomes")
            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except json.JSONDecodeError:
                    outcomes = []

            entry = {
                "condition_id": cond_id,
                "question": question,
                "category": canon,
                "tags": json.dumps(list(matching)),
                "end_date": end_date,
                "tokens": tokens or [],
                "outcomes": outcomes or [],
                "_volume": volume,
            }
            category_buckets[canon].append(entry)

    # Take top N per category by 1-week volume, then flatten.
    filtered: list[dict] = []
    for cat, entries in category_buckets.items():
        entries.sort(key=lambda e: e["_volume"], reverse=True)
        top = entries[:TOP_PER_CATEGORY]
        for e in top:
            db.upsert_market(e["condition_id"], e["question"],
                             e["category"], e["tags"], e["end_date"])
            e.pop("_volume", None)
            filtered.append(e)
        logger.info("  %s: %d / %d markets selected", cat, len(top), len(entries))

    logger.info("Discovered %d target markets from %d events (top %d per category)",
                len(filtered), len(all_events), TOP_PER_CATEGORY)
    return filtered


# ── Step 2: Fetch trades per market ─────────────────────────────

async def fetch_trades_for_market(client: httpx.AsyncClient,
                                  condition_id: str) -> list[dict]:
    """Pull trades for a market via data-api.polymarket.com/trades."""
    data = await _fetch_json(client, DATA_API_TRADES, {
        "market": condition_id, "limit": 500,
    })
    return data if isinstance(data, list) else []


# ── Signal 1: Outcome Bucket Clustering ─────────────────────────

def detect_clusters(trades: list[dict], market_question: str,
                    now: datetime) -> list[dict]:
    """
    Group trades by outcome token (asset) within 48h window.
    Flag when 3+ distinct wallets BUY the same outcome token.
    """
    cutoff = now - timedelta(hours=CLUSTER_WINDOW_HOURS)
    alerts = []

    # Group by asset (outcome token ID)
    buckets: dict[str, set] = {}
    for t in trades:
        # Timestamp is epoch seconds (int or float)
        raw_ts = t.get("timestamp")
        if raw_ts is None:
            continue
        try:
            ts = datetime.fromtimestamp(float(raw_ts), tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            continue

        if ts < cutoff:
            continue

        side = (t.get("side") or "").upper()
        if side != "BUY":
            continue

        token = t.get("asset") or ""
        wallet = t.get("proxyWallet") or ""
        if not token or not wallet:
            continue

        buckets.setdefault(token, set()).add(wallet.lower())

    for token, wallets in buckets.items():
        count = len(wallets)
        if count >= MIN_CLUSTER_WALLETS:
            if count == 3:
                severity, score = "medium", 60
            elif count == 4:
                severity, score = "high", 75
            else:
                severity, score = "critical", min(90 + (count - 5) * 5, 100)

            alert = {
                "market_question": market_question,
                "outcome_token": token,
                "wallet_count": count,
                "wallet_addresses": list(wallets),
                "severity": severity,
                "score": score,
            }
            alerts.append(alert)
            db.insert_cluster_alert(
                market_question, token, count,
                list(wallets), severity,
            )
            logger.info("CLUSTER ALERT: %s — %d wallets on %s [%s]",
                        market_question[:40], count, token[:12], severity)
    return alerts


# ── Signal 2: Coordinated Wallet Detection ──────────────────────

async def _fetch_wallet_funder(
    client: httpx.AsyncClient, sem: asyncio.Semaphore,
    wallet: str, info: dict,
) -> tuple[str, str, dict] | None:
    """Fetch the first funding address for a wallet from Polygonscan."""
    async with sem:
        data = await _fetch_json(client, POLYGONSCAN_API, {
            "module": "account",
            "action": "txlist",
            "address": wallet,
            "sort": "asc",
            "page": "1",
            "offset": "10",
        })

    txlist = []
    if isinstance(data, dict):
        txlist = data.get("result", [])
    if not isinstance(txlist, list):
        return None

    for tx in txlist:
        to_addr = (tx.get("to") or "").lower()
        if to_addr == wallet.lower():
            funder = (tx.get("from") or "").lower()
            if funder:
                return funder, wallet, info
            break
    return None


async def detect_coordinated(client: httpx.AsyncClient,
                             flagged_wallets: dict[str, dict]) -> list[dict]:
    """
    For each flagged wallet, check Polygonscan for shared funding source.
    flagged_wallets: {wallet_address: {market_question, outcome_token}}
    """
    # Polygonscan free tier: 5 req/s. Use semaphore of 3 to stay safe.
    sem = asyncio.Semaphore(3)

    tasks = [
        _fetch_wallet_funder(client, sem, wallet, info)
        for wallet, info in flagged_wallets.items()
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    funder_map: dict[str, list] = {}
    for res in results:
        if isinstance(res, Exception) or res is None:
            continue
        funder, wallet, info = res
        funder_map.setdefault(funder, []).append({
            "wallet": wallet,
            "market_question": info.get("market_question", ""),
            "outcome_token": info.get("outcome_token", ""),
        })

    pairs_found = []
    for funder, wallets_info in funder_map.items():
        if len(wallets_info) < 2:
            continue
        for a, b in combinations(wallets_info, 2):
            if a["outcome_token"] == b["outcome_token"]:
                pair = {
                    "wallet_a": a["wallet"],
                    "wallet_b": b["wallet"],
                    "shared_funder": funder,
                    "market_question": a["market_question"],
                    "outcome_token": a["outcome_token"],
                }
                pairs_found.append(pair)
                db.insert_coordinated_pair(
                    a["wallet"], b["wallet"], funder,
                    a["market_question"], a["outcome_token"],
                )
                logger.info("COORDINATED: %s <-> %s via %s",
                            a["wallet"][:10], b["wallet"][:10], funder[:10])
    return pairs_found


# ── Signal 3: Fresh Wallet + Concentrated Bet ───────────────────

async def score_wallet(client: httpx.AsyncClient, wallet: str,
                       base_score: int = 0,
                       coordinated_bonus: bool = False) -> dict:
    """Score a single wallet based on freshness and concentration."""
    # Use /activity endpoint with user= param
    data = await _fetch_json(client, DATA_API_ACTIVITY, {
        "user": wallet, "limit": 200,
    })

    if not data or not isinstance(data, list):
        return _empty_score(wallet, base_score, coordinated_bonus)

    now = datetime.now(timezone.utc)
    markets_set: set = set()
    bets_by_market: dict[str, float] = {}
    max_bet = 0.0
    earliest = now

    for trade in data:
        raw_ts = trade.get("timestamp")
        if raw_ts is not None:
            try:
                ts = datetime.fromtimestamp(float(raw_ts), tz=timezone.utc)
                if ts < earliest:
                    earliest = ts
            except (ValueError, TypeError, OSError):
                pass

        mkt = trade.get("conditionId") or ""
        if mkt:
            markets_set.add(mkt)

        usd = 0.0
        try:
            usd = float(trade.get("usdcSize") or trade.get("size") or 0)
        except (ValueError, TypeError):
            pass

        if mkt:
            bets_by_market[mkt] = bets_by_market.get(mkt, 0) + usd
        if usd > max_bet:
            max_bet = usd

    account_age = (now - earliest).days if earliest < now else 0
    unique_markets = len(markets_set)
    total_bet = sum(bets_by_market.values()) or 1
    top_market_bet = max(bets_by_market.values()) if bets_by_market else 0
    concentration = (top_market_bet / total_bet) * 100

    score = base_score
    if account_age < 14:
        score += 25
    if unique_markets < 4:
        score += 15
    if concentration > 80:
        score += 20
    if max_bet > 10000:
        score += 10
    if coordinated_bonus:
        score += 20
    score = min(score, 100)

    result = {
        "wallet_address": wallet,
        "insider_score": score,
        "account_age_days": account_age,
        "unique_markets": unique_markets,
        "concentration_pct": round(concentration, 1),
        "max_bet_usd": round(max_bet, 2),
    }
    db.upsert_wallet_score(**result)
    return result


def _empty_score(wallet, base, coordinated):
    score = min(base + (20 if coordinated else 0), 100)
    result = {
        "wallet_address": wallet,
        "insider_score": score,
        "account_age_days": 0,
        "unique_markets": 0,
        "concentration_pct": 0,
        "max_bet_usd": 0,
    }
    db.upsert_wallet_score(**result)
    return result


# ── Hourly volume tracking ──────────────────────────────────────

def record_hourly_volume(trades: list[dict]):
    """Aggregate trade volume into hourly buckets for the chart."""
    buckets: dict[str, float] = {}
    for t in trades:
        raw_ts = t.get("timestamp")
        if raw_ts is None:
            continue
        try:
            ts = datetime.fromtimestamp(float(raw_ts), tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            continue

        hour_key = ts.strftime("%Y-%m-%dT%H:00:00")
        usd = 0.0
        try:
            usd = float(t.get("usdcSize") or t.get("size") or 0)
        except (ValueError, TypeError):
            pass
        buckets[hour_key] = buckets.get(hour_key, 0) + usd

    for hour_key, vol in buckets.items():
        db.insert_hourly_volume(hour_key, vol)


# ── Full scan orchestrator ──────────────────────────────────────

async def _fetch_and_analyse_market(
    client: httpx.AsyncClient, sem: asyncio.Semaphore,
    mkt: dict, now: datetime,
) -> tuple[list[dict], list[dict], dict[str, dict]]:
    """Fetch trades for one market and run cluster detection. Semaphore-guarded."""
    cond_id = mkt["condition_id"]
    if not cond_id:
        return [], [], {}

    async with sem:
        trades = await fetch_trades_for_market(client, cond_id)

    cluster_alerts = detect_clusters(trades, mkt["question"], now)
    flagged: dict[str, dict] = {}
    for alert in cluster_alerts:
        for w in alert["wallet_addresses"]:
            flagged[w] = {
                "market_question": alert["market_question"],
                "outcome_token": alert["outcome_token"],
            }
    return trades, cluster_alerts, flagged


async def run_full_scan() -> dict:
    """Execute all 3 signals. Returns summary dict."""
    start = time.time()
    logger.info("=== Starting full scan ===")

    all_alerts: list[dict] = []
    all_flagged: dict[str, dict] = {}
    all_trades: list[dict] = []

    async with httpx.AsyncClient() as client:
        # 1. Discover markets
        markets = await fetch_target_markets(client)
        logger.info("Processing %d markets", len(markets))

        # 2. Fetch trades + run Signal 1 concurrently (batched)
        now = datetime.now(timezone.utc)
        sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

        tasks = [
            _fetch_and_analyse_market(client, sem, mkt, now)
            for mkt in markets
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, Exception):
                logger.warning("Market analysis failed: %s", res)
                continue
            trades, cluster_alerts, flagged = res
            all_trades.extend(trades)
            all_alerts.extend(cluster_alerts)
            all_flagged.update(flagged)

        # Record hourly volume for chart
        record_hourly_volume(all_trades)

        # Cap flagged wallets to keep Signal 2+3 fast
        if len(all_flagged) > MAX_WALLETS_TO_INVESTIGATE:
            logger.info("Capping flagged wallets from %d to %d",
                        len(all_flagged), MAX_WALLETS_TO_INVESTIGATE)
            all_flagged = dict(list(all_flagged.items())[:MAX_WALLETS_TO_INVESTIGATE])

        # 3. Signal 2: Coordinated wallet detection
        coordinated_set = set()
        if all_flagged:
            coord_pairs = await detect_coordinated(client, all_flagged)
            for pair in coord_pairs:
                coordinated_set.add(pair["wallet_a"])
                coordinated_set.add(pair["wallet_b"])
            all_alerts.extend([{
                "market_question": p["market_question"],
                "outcome_token": p["outcome_token"],
                "wallet_count": 2,
                "wallet_addresses": [p["wallet_a"], p["wallet_b"]],
                "severity": "coordinated",
                "score": 80,
            } for p in coord_pairs])

        # 4. Signal 3: Score each flagged wallet (also concurrent)
        async def _score_one(w: str) -> None:
            base = 0
            for a in all_alerts:
                if w in a.get("wallet_addresses", []):
                    base = max(base, a.get("score", 0))
            is_coordinated = w in coordinated_set
            async with sem:
                await score_wallet(client, w, base, is_coordinated)

        score_tasks = [_score_one(w) for w in all_flagged]
        await asyncio.gather(*score_tasks, return_exceptions=True)

    duration = round(time.time() - start, 2)
    db.insert_scan_log(len(markets), len(all_alerts), duration)

    summary = {
        "markets_scanned": len(markets),
        "alerts_found": len(all_alerts),
        "duration_seconds": duration,
        "coordinated_pairs": len(coordinated_set) // 2,
    }
    logger.info("=== Scan complete: %s ===", summary)
    return summary


def run_scan_sync() -> dict:
    """Sync wrapper for APScheduler (which doesn't natively run async)."""
    return asyncio.run(run_full_scan())
