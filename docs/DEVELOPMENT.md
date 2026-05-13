# Development

## Requirements

- Python 3.12+
- Interactive Brokers account (paper or live)
- **IB Gateway** or **TWS** running locally with API enabled (see below)

## Install from source

```bash
git clone https://github.com/GameMaster301/IBKR-Web-Portfolio-Dashboard.git
cd IBKR-Web-Portfolio-Dashboard
pip install -r requirements.txt
python main.py
```

Open **http://localhost:8050**. The dashboard auto-refreshes every 60 seconds.

For demo mode without an IBKR connection: `python main.py --demo`.

## Enabling the API in IB Gateway / TWS

The dashboard works with both. Pick whichever you already have running.

### Option A — IB Gateway (recommended for dashboard use)

[Download IB Gateway](https://www.interactivebrokers.com/en/trading/ibgateway-stable.php) — headless, ~100 MB RAM, designed for API connections.

1. Open IB Gateway and log in
2. **Configure → Settings → API → Settings**
3. Tick **Enable ActiveX and Socket Clients**
4. Set the socket port: **4002** for paper trading, **4001** for live
5. Tick **Read-Only API** (the dashboard never places orders)
6. Click **OK**

Set `IBKR_PORT=4002` (paper) or `IBKR_PORT=4001` (live) when running the dashboard.

### Option B — TWS (if you already have it open for manual trading)

1. In TWS: **Edit → Global Configuration → API → Settings**
2. Tick **Enable ActiveX and Socket Clients**
3. Set the socket port: **7497** for paper trading, **7496** for live
4. Tick **Read-Only API**
5. Click **OK** and restart TWS if prompted

Set `IBKR_PORT=7497` (paper) or `IBKR_PORT=7496` (live) when running the dashboard.

|  | IB Gateway | TWS |
|---|---|---|
| Paper port | **4002** | **7497** |
| Live port | **4001** | **7496** |
| RAM usage | ~100 MB | ~1 GB |
| Needs a GUI | No | Yes |
| Best for | Always-on API dashboards | Active manual trading |

## Docker — build from source

If you want to build the image yourself instead of pulling from Docker Hub:

```bash
cp .env.example .env       # edit IBKR_PORT
docker compose up --build -d
docker compose logs -f dashboard
```

> **Linux only:** In `.env` replace `IBKR_HOST=host.docker.internal` with your LAN IP (e.g. `IBKR_HOST=192.168.1.100`) and uncomment the `extra_hosts` block in `docker-compose.yml`.

Useful commands:

| Goal | Command |
|---|---|
| Rebuild after code changes | `docker compose up --build -d` |
| Tail live logs | `docker compose logs -f dashboard` |
| Check health | `docker inspect --format='{{.State.Health.Status}}' ibkrdash` |
| Open a shell inside | `docker exec -it ibkrdash bash` |
| Stop | `docker compose down` |

## Project structure

```
├── main.py               Entry point — starts IBKR thread and Dash server
├── config.py             Config loader (YAML + env var overrides)
├── ibkr_client.py        Persistent IB Gateway / TWS connection with exponential back-off & heartbeat
├── demo_data.py          Deterministic sample portfolio used in demo mode
├── dashboard.py          ~30-line orchestrator: creates app, wires layout + all module callbacks
├── dashboard_core/
│   ├── layout.py         Full app.layout HTML/dcc tree — build_layout(refresh_ms)
│   ├── data_callbacks.py fetch_data, update_status, retry/demo toggles, keyboard shortcuts
│   ├── summary.py        Summary cards, holdings table, allocation donut, dividends panel
│   ├── detail.py         Position detail slide-out: stats, sparkline, trade CSV upload
│   ├── intel.py          Toast, populate_market_intel store, sector/geo charts, earnings calendar
│   ├── valuation.py      populate_valuation_data store, Buffett/CAPE/P-E/Treasury render
│   ├── coach_ui.py       AI Coach panel: threads, chat, preset scenarios, LLM integration
│   ├── export.py         PDF export callback
│   └── helpers.py        section_label, make_table, badge, status_banner, to_eur, EURUSD_FALLBACK
├── data_processor.py     Position calculations and enrichment
├── analytics.py          Dividend data helpers (yfinance)
├── market_intel.py       Sector/geo exposure and earnings calendar (yfinance)
├── market_valuation.py   Macro indicators — Buffett, S&P 500 P/E, Shiller CAPE, 10-yr Treasury
├── trade_history.py      CSV upload path for historical trades
├── coach.py              Rules-based Portfolio Coach scenarios (no network)
├── ai_provider.py        Optional LLM layer — Anthropic / xAI / OpenAI (BYO key)
├── schemas.py            TypedDict definitions for all dcc.Store payloads
├── decorators.py         @safe_render decorator and NotReadyError for callback error handling
├── styles.py             Centralised colour palette and shared style dicts (CARD, badges, tables)
├── cache_util.py         cached_fetch() — diskcache-backed TTL cache with in-memory fallback
├── assets/custom.css     Dashboard CSS overrides
├── config.yaml           Default configuration
├── Dockerfile / docker-compose.yml
├── install.ps1 / install.sh           One-liner installers
├── start.* / stop.* / update.*        Helper scripts bundled in the release zip
└── .github/workflows/                 CI: checks.yml, docker-publish.yml, release.yml
```

## Tech stack

| Layer | Library |
|---|---|
| IBKR API | [ib_async](https://github.com/ib-api-reloaded/ib_async) |
| Data processing | [pandas](https://pandas.pydata.org/), [numpy](https://numpy.org/) |
| Market data | [yfinance](https://github.com/ranaroussi/yfinance) |
| Dashboard & charts | [Plotly Dash](https://dash.plotly.com/) + Plotly |
| PDF export | [reportlab](https://www.reportlab.com/) |
| Containers | Docker + Compose |

## CI / Docker Hub publishing

**On every push to `main` and on pull requests:** `.github/workflows/checks.yml` installs deps, runs `ruff check .`, and runs `smoke_test.py`.

**On every push to `main`:** `.github/workflows/docker-publish.yml` builds a multi-platform image (`linux/amd64` + `linux/arm64`) and pushes it to Docker Hub as `gmarinos/ibkrdash:latest`.

### Creating a release

Tag a commit to trigger a GitHub Release with the user-facing setup zip attached automatically:

```bash
git tag v1.2.0
git push origin v1.2.0
```

The release workflow (`.github/workflows/release.yml`) will:
1. Build and push the Docker image tagged `:latest` and `:v1.2.0`
2. Package `ibkrdash-setup.zip` (contains `docker-compose.yml`, `.env`, start/stop/update scripts, `SETUP.txt`)
3. Create a GitHub Release with the zip and install instructions

### Required GitHub repository secrets

| Secret | Value |
|---|---|
| `DOCKERHUB_USERNAME` | Docker Hub username |
| `DOCKERHUB_TOKEN` | Docker Hub [access token](https://app.docker.com/settings/personal-access-tokens) (read/write) |

> Pushing changes to `.github/workflows/` requires a GitHub Personal Access Token with the `workflow` scope.
