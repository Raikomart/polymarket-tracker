"""
app.py — Flask application, API routes, APScheduler initialisation.
Polymarket Insider Tracker.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta

from flask import Flask, jsonify, render_template, request
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

import db
from detector import run_scan_sync
from email_report import send_report

# ── Config ──────────────────────────────────────────────────────

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

SCAN_INTERVAL_HOURS = int(os.getenv("SCAN_INTERVAL_HOURS", "6"))

# ── Database init ───────────────────────────────────────────────

db.init_db()
logger.info("SQLite database initialised at %s", db.DB_PATH)

# ── Scan state (thread-safe) ───────────────────────────────────

_scan_lock = threading.Lock()
_scan_status = {"running": False, "last_result": None}


def _run_scan_background():
    """Run scan in a background thread; update shared status."""
    with _scan_lock:
        if _scan_status["running"]:
            return  # already running
        _scan_status["running"] = True

    try:
        result = run_scan_sync()
        _scan_status["last_result"] = result
        logger.info("Background scan finished: %s", result)
    except Exception as exc:
        logger.exception("Background scan failed")
        _scan_status["last_result"] = {"error": str(exc)}
    finally:
        _scan_status["running"] = False


# ── APScheduler ─────────────────────────────────────────────────

scheduler = BackgroundScheduler(daemon=True)

# Detection scan every N hours
scheduler.add_job(
    _run_scan_background,
    "interval",
    hours=SCAN_INTERVAL_HOURS,
    id="detection_scan",
    name=f"Detection scan every {SCAN_INTERVAL_HOURS}h",
    replace_existing=True,
    max_instances=1,
)

# Email report every 12 hours
scheduler.add_job(
    send_report,
    "interval",
    hours=12,
    id="email_report",
    name="Email report every 12h",
    replace_existing=True,
    max_instances=1,
)

scheduler.start()
logger.info("APScheduler started — scan every %dh, email every 12h",
            SCAN_INTERVAL_HOURS)
for job in scheduler.get_jobs():
    logger.info("  Registered job: %s [next run: %s]", job.name, job.next_run_time)


# ── Routes ──────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    """Serve the single-page dashboard."""
    return render_template("dashboard.html")


@app.route("/health")
def health():
    """Health check for Render."""
    return jsonify({"status": "ok"})


@app.route("/api/alerts")
def api_alerts():
    """Last 50 alerts (cluster + coordinated combined)."""
    alerts = db.get_recent_alerts(limit=50)
    return jsonify(alerts)


@app.route("/api/markets")
def api_markets():
    """Currently monitored markets."""
    markets = db.get_markets()
    return jsonify(markets)


@app.route("/api/scores")
def api_scores():
    """Top 20 wallets by insider score."""
    wallets = db.get_top_wallets(limit=20)
    return jsonify(wallets)


@app.route("/api/kpis")
def api_kpis():
    """KPI summary data for dashboard cards."""
    markets = db.get_markets()
    active_alerts = db.get_alerts_since(hours=24)
    coord_pairs = db.get_coordinated_count_since(hours=24)
    highest_score = db.get_highest_score_today()
    last_scan = db.get_last_scan()

    return jsonify({
        "markets_monitored": len(markets),
        "active_alerts_24h": active_alerts,
        "coordinated_pairs_24h": coord_pairs,
        "highest_score_today": highest_score,
        "last_scan": last_scan,
    })


@app.route("/api/volume")
def api_volume():
    """Hourly volume data + alert timestamps for chart."""
    volumes = db.get_hourly_volumes(hours=48)
    alert_times = db.get_alert_timestamps_48h()
    return jsonify({
        "volumes": volumes,
        "alert_timestamps": alert_times,
    })


@app.route("/api/coordinated")
def api_coordinated():
    """Coordinated wallet pairs."""
    pairs = db.get_coordinated_pairs(limit=50)
    return jsonify(pairs)


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Manually trigger a full scan.

    The scan runs in a background thread so the HTTP request returns
    immediately.  The dashboard polls /api/scan-status to track progress.
    """
    if _scan_status["running"]:
        return jsonify({"status": "already_running"}), 202

    logger.info("Manual refresh triggered")
    t = threading.Thread(target=_run_scan_background, daemon=True)
    t.start()
    return jsonify({"status": "started"}), 202


@app.route("/api/scan-status")
def api_scan_status():
    """Poll this to check if a manual scan has finished."""
    return jsonify({
        "running": _scan_status["running"],
        "last_result": _scan_status["last_result"],
    })


# ── Main ────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
