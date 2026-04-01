"""
email_report.py — Build and send the 12-hour HTML insider report via SendGrid.
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Content

import db

logger = logging.getLogger(__name__)

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
REPORT_EMAIL_TO = os.getenv("REPORT_EMAIL_TO", "")
REPORT_EMAIL_FROM = os.getenv("REPORT_EMAIL_FROM", "")

# CEST = UTC+2
CEST = timezone(timedelta(hours=2))


def send_report():
    """Build and send the 12-hour insider report."""
    if not SENDGRID_API_KEY or not REPORT_EMAIL_TO or not REPORT_EMAIL_FROM:
        logger.warning("Email not configured — skipping report send.")
        return

    now_cest = datetime.now(CEST)
    date_str = now_cest.strftime("%Y-%m-%d")
    time_str = now_cest.strftime("%H:%M")

    # Gather data
    alerts = db.get_alerts_for_report(hours=12)
    coordinated = db.get_coordinated_for_report(hours=12)
    top_wallets = db.get_top_wallets_for_report(limit=10)
    last_scan = db.get_last_scan()

    n_alerts = len(alerts)
    n_coord = len(coordinated)
    markets_scanned = last_scan["markets_scanned"] if last_scan else 0
    highest_score = max((w["insider_score"] for w in top_wallets), default=0)

    critical_alerts = [a for a in alerts
                       if a.get("severity") in ("high", "critical")]

    subject = (
        f"\U0001f6a8 Polymarket Insider Report — {date_str} {time_str} CEST | "
        f"{n_alerts} alerts, {n_coord} coordinated pairs"
    )

    html = _build_html(
        date_str, time_str, markets_scanned, n_alerts, n_coord,
        highest_score, critical_alerts, coordinated, top_wallets,
    )

    message = Mail(
        from_email=REPORT_EMAIL_FROM,
        to_emails=REPORT_EMAIL_TO,
        subject=subject,
        html_content=Content("text/html", html),
    )

    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        logger.info("Report email sent — status %s", response.status_code)
    except Exception as exc:
        logger.error("Failed to send report email: %s", exc)


def _build_html(date_str, time_str, markets_scanned, n_alerts,
                n_coord, highest_score, critical_alerts,
                coordinated, top_wallets) -> str:
    """Pure inline-CSS HTML email body."""

    # ── Styles ──
    body_style = (
        "font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', "
        "Roboto, sans-serif; background: #0f1117; color: #e1e4e8; "
        "margin: 0; padding: 20px;"
    )
    card_style = (
        "background: #161b22; border: 1px solid #30363d; "
        "border-radius: 8px; padding: 16px; margin-bottom: 16px;"
    )
    table_style = (
        "width: 100%; border-collapse: collapse; font-size: 13px;"
    )
    th_style = (
        "text-align: left; padding: 8px 12px; border-bottom: 2px solid #30363d; "
        "color: #8b949e; font-weight: 600; font-size: 12px; "
        "text-transform: uppercase; letter-spacing: 0.5px;"
    )
    td_style = (
        "padding: 8px 12px; border-bottom: 1px solid #21262d; "
        "color: #c9d1d9;"
    )
    h2_style = "color: #58a6ff; margin: 0 0 12px 0; font-size: 18px;"
    kpi_style = (
        "display: inline-block; width: 23%; background: #161b22; "
        "border: 1px solid #30363d; border-radius: 8px; "
        "padding: 12px; margin-right: 1%; text-align: center; "
        "vertical-align: top;"
    )
    kpi_num = "font-size: 28px; font-weight: 700; color: #58a6ff;"
    kpi_label = "font-size: 11px; color: #8b949e; margin-top: 4px;"

    # ── Critical alerts table rows ──
    if critical_alerts:
        critical_rows = ""
        for a in critical_alerts:
            wallets_raw = a.get("wallet_addresses_json", "[]")
            try:
                wallets = json.loads(wallets_raw) if isinstance(wallets_raw, str) else wallets_raw
            except json.JSONDecodeError:
                wallets = []
            wallet_str = ", ".join(w[:10] + "..." for w in wallets[:5])
            sev = a.get("severity", "")
            sev_color = "#f85149" if sev == "critical" else "#d29922"
            critical_rows += f"""
            <tr>
                <td style="{td_style}">{a.get('market_question', '')[:60]}</td>
                <td style="{td_style}">{a.get('outcome_token', '')[:16]}...</td>
                <td style="{td_style}">{wallet_str}</td>
                <td style="{td_style}"><span style="color:{sev_color};font-weight:700;">{sev.upper()}</span></td>
                <td style="{td_style}">{a.get('wallet_count', 0)}</td>
            </tr>
            """
        critical_section = f"""
        <div style="{card_style}">
            <h2 style="{h2_style}">Critical Alerts (score 75+)</h2>
            <table style="{table_style}">
                <tr>
                    <th style="{th_style}">Market</th>
                    <th style="{th_style}">Outcome</th>
                    <th style="{th_style}">Wallets</th>
                    <th style="{th_style}">Severity</th>
                    <th style="{th_style}">Count</th>
                </tr>
                {critical_rows}
            </table>
        </div>
        """
    else:
        critical_section = f"""
        <div style="{card_style}">
            <h2 style="{h2_style}">Critical Alerts</h2>
            <p style="color: #3fb950;">No critical alerts in this period.</p>
        </div>
        """

    # ── Coordinated pairs ──
    if coordinated:
        coord_rows = ""
        for c in coordinated:
            coord_rows += f"""
            <tr>
                <td style="{td_style}">{c.get('wallet_a','')[:14]}...</td>
                <td style="{td_style}">{c.get('wallet_b','')[:14]}...</td>
                <td style="{td_style}">{c.get('shared_funder','')[:14]}...</td>
                <td style="{td_style}">{c.get('market_question','')[:50]}</td>
                <td style="{td_style}">{c.get('detected_at','')}</td>
            </tr>
            """
        coord_section = f"""
        <div style="{card_style}">
            <h2 style="{h2_style}">Coordinated Wallet Pairs</h2>
            <table style="{table_style}">
                <tr>
                    <th style="{th_style}">Wallet A</th>
                    <th style="{th_style}">Wallet B</th>
                    <th style="{th_style}">Shared Funder</th>
                    <th style="{th_style}">Market</th>
                    <th style="{th_style}">Detected</th>
                </tr>
                {coord_rows}
            </table>
        </div>
        """
    else:
        coord_section = f"""
        <div style="{card_style}">
            <h2 style="{h2_style}">Coordinated Wallet Pairs</h2>
            <p style="color: #3fb950;">No coordinated pairs detected.</p>
        </div>
        """

    # ── Top wallets ──
    if top_wallets:
        wallet_rows = ""
        for w in top_wallets:
            score = w.get("insider_score", 0)
            score_color = (
                "#f85149" if score >= 75 else
                "#d29922" if score >= 50 else
                "#3fb950"
            )
            risk_factors = []
            if w.get("account_age_days", 999) < 14:
                risk_factors.append("Fresh account")
            if w.get("unique_markets", 999) < 4:
                risk_factors.append("Low diversity")
            if w.get("concentration_pct", 0) > 80:
                risk_factors.append("High concentration")
            if w.get("max_bet_usd", 0) > 10000:
                risk_factors.append("Large single bet")
            wallet_rows += f"""
            <tr>
                <td style="{td_style}">{w.get('wallet_address','')[:14]}...</td>
                <td style="{td_style}"><span style="color:{score_color};font-weight:700;">{score}</span></td>
                <td style="{td_style}">{', '.join(risk_factors) or 'None'}</td>
            </tr>
            """
        wallet_section = f"""
        <div style="{card_style}">
            <h2 style="{h2_style}">Top 10 Wallets by Score</h2>
            <table style="{table_style}">
                <tr>
                    <th style="{th_style}">Address</th>
                    <th style="{th_style}">Score</th>
                    <th style="{th_style}">Key Risk Factors</th>
                </tr>
                {wallet_rows}
            </table>
        </div>
        """
    else:
        wallet_section = f"""
        <div style="{card_style}">
            <h2 style="{h2_style}">Top 10 Wallets by Score</h2>
            <p style="color: #8b949e;">No wallet scores recorded yet.</p>
        </div>
        """

    # ── No activity fallback ──
    no_activity = ""
    if n_alerts == 0 and n_coord == 0:
        no_activity = f"""
        <div style="{card_style}; border-color: #3fb950;">
            <p style="color: #3fb950; font-size: 16px; text-align: center; margin: 12px 0;">
                ✅ No suspicious activity detected in this period.
            </p>
        </div>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="{body_style}">
        <div style="max-width: 720px; margin: 0 auto;">
            <h1 style="color: #f0f6fc; font-size: 22px; margin-bottom: 4px;">
                🚨 Polymarket Insider Report
            </h1>
            <p style="color: #8b949e; margin-top: 0;">
                {date_str} {time_str} CEST — Last 12 hours
            </p>

            {no_activity}

            <!-- KPI Row -->
            <div style="margin-bottom: 16px;">
                <div style="{kpi_style}">
                    <div style="{kpi_num}">{markets_scanned}</div>
                    <div style="{kpi_label}">Markets Scanned</div>
                </div>
                <div style="{kpi_style}">
                    <div style="{kpi_num}">{n_alerts}</div>
                    <div style="{kpi_label}">New Alerts</div>
                </div>
                <div style="{kpi_style}">
                    <div style="{kpi_num}">{n_coord}</div>
                    <div style="{kpi_label}">Coordinated Pairs</div>
                </div>
                <div style="{kpi_style}">
                    <div style="{kpi_num}">{highest_score}</div>
                    <div style="{kpi_label}">Highest Score</div>
                </div>
            </div>

            {critical_section}
            {coord_section}
            {wallet_section}

            <!-- Footer -->
            <div style="margin-top: 24px; padding-top: 16px; border-top: 1px solid #21262d;">
                <p style="color: #484f58; font-size: 12px;">
                    Generated by Polymarket Insider Tracker.<br>
                    Dashboard: <a href="#" style="color: #58a6ff;">your-render-url</a>
                </p>
            </div>
        </div>
    </body>
    </html>
    """
    return html
