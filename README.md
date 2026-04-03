# 🕵️ Polymarket Insider Tracker

> **Monitors suspicious betting activity across politics, economics, military, elections, and policy markets on Polymarket.**

Polymarket Insider Tracker is a Python/Flask web application that continuously scans Polymarket prediction markets for anomalous wallet behaviour — clustering, coordinated entries, and abnormal volume spikes — that may indicate insider knowledge or coordinated manipulation.

---

## 🚀 Features

- **Automated Market Scanning** — polls Polymarket markets on a configurable interval (default every 6 hours) via APScheduler
- **Insider Score Engine** — ranks wallets by a composite suspicion score computed in `detector.py`
- **Coordinated Wallet Detection** — identifies wallet pairs placing simultaneous or patterned bets
- **Alert Feed** — stores and surfaces the last 50 cluster + coordinated alerts via REST API
- **Email Reports** — automated email digests every 12 hours via SendGrid
- **Live Dashboard** — single-page HTML dashboard showing KPIs, volume chart, top wallets, and alert feed
- **Manual Refresh** — trigger a full scan on-demand from the dashboard or via `POST /api/refresh`
- **Health Endpoint** — `/health` for uptime monitoring and Render deployment checks

---

## 🏗️ Architecture

```
app.py            Flask application, API routes, APScheduler jobs
detector.py       Core detection logic — insider scoring, cluster & coordinated analysis
db.py             SQLite persistence layer — markets, alerts, wallets, volumes
email_report.py   SendGrid-based email digest builder and sender
templates/        Jinja2 HTML dashboard template
```

---

## ⚙️ Setup

### Prerequisites

- Python 3.12+ (see `.python-version`)
- A [SendGrid](https://sendgrid.com) API key for email reports
- A Polymarket API access token (see `.env.example`)

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/giorgiocopilot/polymarket-tracker.git
cd polymarket-tracker

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env
# Edit .env with your credentials
```

### Environment Variables

| Variable | Description | Default |
|---|---|---|
| `FLASK_SECRET_KEY` | Flask session secret | `dev-secret-change-me` |
| `SCAN_INTERVAL_HOURS` | Hours between automated scans | `6` |
| `SENDGRID_API_KEY` | SendGrid API key for email reports | — |
| `REPORT_TO_EMAIL` | Recipient email address | — |
| `PORT` | Port for Flask server | `5000` |

See `.env.example` for the full list.

### Run Locally

```bash
python app.py
```

The dashboard is available at `http://localhost:5000`.

---

## 🌐 Deployment (Render)

The repo includes a `render.yaml` and `Procfile` for one-click deploy to [Render](https://render.com).

```bash
# Procfile
web: gunicorn app:app
```

Push to `main` and Render will auto-deploy.

---

## 📡 API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Live dashboard |
| `/health` | GET | Health check |
| `/api/alerts` | GET | Last 50 insider alerts |
| `/api/markets` | GET | Monitored markets list |
| `/api/scores` | GET | Top 20 wallets by insider score |
| `/api/kpis` | GET | Dashboard KPI summary (24h) |
| `/api/volume` | GET | Hourly volume + alert timestamps (48h) |
| `/api/coordinated` | GET | Coordinated wallet pairs |
| `/api/refresh` | POST | Trigger manual scan |
| `/api/scan-status` | GET | Current scan progress |

---

## 🧠 Detection Logic

The `detector.py` engine runs two primary analyses:

1. **Cluster Detection** — groups wallets by time window and market position; flags clusters exceeding statistical thresholds
2. **Coordinated Pair Detection** — correlates wallet entry times and sizing to surface pairs acting in lockstep

Each wallet receives an **Insider Score** — a composite metric weighting frequency, cluster membership, and volume anomalies. Scores are persisted in SQLite and surfaced via `/api/scores`.

---

## 📦 Dependencies

| Package | Purpose |
|---|---|
| `flask` | Web framework and API |
| `apscheduler` | Background job scheduling |
| `httpx` | Async HTTP client for Polymarket API |
| `pandas` / `numpy` / `scipy` | Statistical detection engine |
| `sendgrid` | Email report delivery |
| `python-dotenv` | Environment variable management |
| `gunicorn` | Production WSGI server |

---

## 🤝 Connect

Interested in prediction market analytics, AI-driven signal detection, or building tools like this?

**Let's connect on LinkedIn:** [Giorgio Tsoupis](https://www.linkedin.com/in/giorgiotsoupis/)

I'm happy to discuss the methodology, potential collaborations, or custom implementations for your use case.

---

## 📄 License

MIT — free to use, modify, and distribute with attribution.
