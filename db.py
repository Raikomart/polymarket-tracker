"""
db.py — SQLite initialisation and all database queries.
File: tracker.db (persisted across restarts on Render disk).
"""

import sqlite3
import json
import os
import threading
from datetime import datetime, timezone

DB_PATH = os.getenv("DB_PATH", "tracker.db")

# One connection per thread — SQLite is not thread-safe by default.
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA busy_timeout=5000")
    return _local.conn


def init_db():
    """Create all tables if they do not exist."""
    conn = _get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS markets (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            condition_id    TEXT UNIQUE,
            question        TEXT,
            category        TEXT,
            tags            TEXT,
            end_date        TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS wallet_scores (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet_address  TEXT,
            insider_score   REAL,
            account_age_days INTEGER,
            unique_markets  INTEGER,
            concentration_pct REAL,
            max_bet_usd     REAL,
            scan_timestamp  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS cluster_alerts (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            market_question         TEXT,
            outcome_token           TEXT,
            wallet_count            INTEGER,
            wallet_addresses_json   TEXT,
            severity                TEXT,
            triggered_at            TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS coordinated_wallets (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet_a        TEXT,
            wallet_b        TEXT,
            shared_funder   TEXT,
            market_question TEXT,
            outcome_token   TEXT,
            detected_at     TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS scan_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_timestamp  TEXT DEFAULT (datetime('now')),
            markets_scanned INTEGER,
            alerts_triggered INTEGER,
            duration_seconds REAL
        );

        CREATE TABLE IF NOT EXISTS hourly_volume (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            hour_bucket TEXT,
            volume_usd  REAL,
            recorded_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()


# ── Market helpers ──────────────────────────────────────────────

def upsert_market(condition_id: str, question: str, category: str,
                  tags: str, end_date: str):
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO markets (condition_id, question, category, tags, end_date)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(condition_id) DO UPDATE SET
            question = excluded.question,
            category = excluded.category,
            tags     = excluded.tags,
            end_date = excluded.end_date
        """,
        (condition_id, question, category, tags, end_date),
    )
    conn.commit()


def get_markets():
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM markets ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


# ── Alert helpers ───────────────────────────────────────────────

def insert_cluster_alert(market_question, outcome_token, wallet_count,
                         wallet_addresses: list, severity):
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO cluster_alerts
            (market_question, outcome_token, wallet_count,
             wallet_addresses_json, severity)
        VALUES (?, ?, ?, ?, ?)
        """,
        (market_question, outcome_token, wallet_count,
         json.dumps(wallet_addresses), severity),
    )
    conn.commit()


def insert_coordinated_pair(wallet_a, wallet_b, shared_funder,
                            market_question, outcome_token):
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO coordinated_wallets
            (wallet_a, wallet_b, shared_funder, market_question, outcome_token)
        VALUES (?, ?, ?, ?, ?)
        """,
        (wallet_a, wallet_b, shared_funder, market_question, outcome_token),
    )
    conn.commit()


def upsert_wallet_score(wallet_address, insider_score, account_age_days,
                        unique_markets, concentration_pct, max_bet_usd):
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO wallet_scores
            (wallet_address, insider_score, account_age_days,
             unique_markets, concentration_pct, max_bet_usd)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (wallet_address, insider_score, account_age_days,
         unique_markets, concentration_pct, max_bet_usd),
    )
    conn.commit()


def insert_scan_log(markets_scanned, alerts_triggered, duration_seconds):
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO scan_log (markets_scanned, alerts_triggered, duration_seconds)
        VALUES (?, ?, ?)
        """,
        (markets_scanned, alerts_triggered, duration_seconds),
    )
    conn.commit()


def insert_hourly_volume(hour_bucket: str, volume_usd: float):
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO hourly_volume (hour_bucket, volume_usd)
        VALUES (?, ?)
        """,
        (hour_bucket, volume_usd),
    )
    conn.commit()


# ── Read helpers for API routes ─────────────────────────────────

def get_recent_alerts(limit=50):
    """Combined alerts from cluster_alerts + coordinated_wallets."""
    conn = _get_conn()
    clusters = conn.execute(
        """
        SELECT id, market_question, outcome_token, wallet_count,
               wallet_addresses_json, severity, triggered_at,
               'cluster' AS signal_type
        FROM cluster_alerts
        ORDER BY triggered_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    coordinated = conn.execute(
        """
        SELECT id, market_question, outcome_token,
               wallet_a, wallet_b, shared_funder, detected_at,
               'coordinated' AS signal_type
        FROM coordinated_wallets
        ORDER BY detected_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    results = []
    for r in clusters:
        d = dict(r)
        d["wallets_involved"] = json.loads(d.pop("wallet_addresses_json", "[]"))
        d["score"] = _severity_to_score(d["severity"])
        results.append(d)
    for r in coordinated:
        d = dict(r)
        d["wallets_involved"] = [d.get("wallet_a", ""), d.get("wallet_b", "")]
        d["wallet_count"] = 2
        d["severity"] = "coordinated"
        d["score"] = 80
        results.append(d)

    results.sort(key=lambda x: x.get("triggered_at") or x.get("detected_at") or "", reverse=True)
    return results[:limit]


def get_top_wallets(limit=20):
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT wallet_address, insider_score, account_age_days,
               unique_markets, concentration_pct, max_bet_usd,
               scan_timestamp
        FROM wallet_scores
        ORDER BY insider_score DESC, scan_timestamp DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_alerts_since(hours=24):
    conn = _get_conn()
    row = conn.execute(
        """
        SELECT COUNT(*) as cnt FROM cluster_alerts
        WHERE triggered_at >= datetime('now', ?)
        """,
        (f"-{hours} hours",),
    ).fetchone()
    return row["cnt"] if row else 0


def get_coordinated_count_since(hours=24):
    conn = _get_conn()
    row = conn.execute(
        """
        SELECT COUNT(*) as cnt FROM coordinated_wallets
        WHERE detected_at >= datetime('now', ?)
        """,
        (f"-{hours} hours",),
    ).fetchone()
    return row["cnt"] if row else 0


def get_highest_score_today():
    conn = _get_conn()
    row = conn.execute(
        """
        SELECT MAX(insider_score) as max_score FROM wallet_scores
        WHERE scan_timestamp >= datetime('now', '-24 hours')
        """
    ).fetchone()
    return row["max_score"] if row and row["max_score"] else 0


def get_last_scan():
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM scan_log ORDER BY scan_timestamp DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def get_hourly_volumes(hours=48):
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT hour_bucket, volume_usd FROM hourly_volume
        WHERE recorded_at >= datetime('now', ?)
        ORDER BY hour_bucket ASC
        """,
        (f"-{hours} hours",),
    ).fetchall()
    return [dict(r) for r in rows]


def get_coordinated_pairs(limit=50):
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT wallet_a, wallet_b, shared_funder, market_question,
               outcome_token, detected_at
        FROM coordinated_wallets
        ORDER BY detected_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_alerts_for_report(hours=12):
    """All cluster alerts from the last N hours."""
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT * FROM cluster_alerts
        WHERE triggered_at >= datetime('now', ?)
        ORDER BY triggered_at DESC
        """,
        (f"-{hours} hours",),
    ).fetchall()
    return [dict(r) for r in rows]


def get_coordinated_for_report(hours=12):
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT * FROM coordinated_wallets
        WHERE detected_at >= datetime('now', ?)
        ORDER BY detected_at DESC
        """,
        (f"-{hours} hours",),
    ).fetchall()
    return [dict(r) for r in rows]


def get_top_wallets_for_report(limit=10):
    return get_top_wallets(limit)


def get_alert_timestamps_48h():
    """Return timestamps of alerts in last 48h for chart overlay."""
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT triggered_at FROM cluster_alerts
        WHERE triggered_at >= datetime('now', '-48 hours')
        ORDER BY triggered_at ASC
        """
    ).fetchall()
    return [r["triggered_at"] for r in rows]


# ── Helpers ─────────────────────────────────────────────────────

def _severity_to_score(severity: str) -> int:
    mapping = {"medium": 60, "high": 75, "critical": 90}
    return mapping.get(severity, 50)
