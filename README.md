# IBKR Portfolio Dashboard

A real-time, private portfolio dashboard for Interactive Brokers — built with Python and Plotly Dash. Connects directly to **IB Gateway** via the `ib_async` API. No third-party data providers for live prices, no delays, read-only.

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat&logo=python&logoColor=white)
![Dash](https://img.shields.io/badge/Plotly_Dash-2.x-119DFF?style=flat&logo=plotly&logoColor=white)
![ib_async](https://img.shields.io/badge/ib__async-latest-orange?style=flat)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?style=flat&logo=docker&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-22c55e?style=flat)

---

## Download & Install

> **Only requirement:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) (free, ~2 min to install). IB Gateway or TWS must be running on your machine.

### Windows — paste into PowerShell

```powershell
irm https://raw.githubusercontent.com/GameMaster301/IBKR-TWS-Web-Portfolio-Dashboard/main/install.ps1 | iex
```

Creates a desktop shortcut. Double-click it any time to start.

### Mac / Linux — paste into Terminal

```bash
curl -fsSL https://raw.githubusercontent.com/GameMaster301/IBKR-TWS-Web-Portfolio-Dashboard/main/install.sh | bash
```

### Prefer not to use a terminal?

Download the **[latest release zip](https://github.com/GameMaster301/IBKR-TWS-Web-Portfolio-Dashboard/releases/latest)**, extract it anywhere, and follow **SETUP.txt** inside. Then double-click `start.bat` (Windows) or run `./start.sh` (Mac/Linux).

---

## Features

- **Holdings table** — ticker, quantity, average cost, live price, market value (USD + EUR), unrealised P&L (value + %), portfolio weight, daily change, 52-week range position, spread, VWAP, volume
- **Summary cards** — total value, unrealised P&L, today's P&L, cash (EUR + % of portfolio)
- **Allocation donut chart** — visual portfolio weights
- **Live EUR/USD rate** — fetched directly from IBKR, not a third-party API
- **Market Valuation** — Buffett Indicator (Wilshire 5000 / US GDP), S&P 500 trailing P/E, Shiller CAPE with 50-year chart; each metric colour-coded by valuation zone
- **Market Intelligence** — sector & geography exposure, earnings calendar with historical 1-day post-earnings moves
- **Dividends tracker** — yield per position, projected annual income, upcoming payment schedule
- **Historical trades** — click a holding to open its detail panel, then upload a Transaction History CSV (IBKR Client Portal → Performance & Reports → Transaction History). BUY/SELL markers are overlaid on the per-position price chart.
- **PDF export** — one-click portfolio snapshot download
- **Auto-reconnect** — exponential back-off with passive heartbeat; dashboard keeps working while IB Gateway or TWS is restarting

---

## For developers — run from source

### Requirements

- Python 3.12+
- Interactive Brokers account (paper or live)
- **IB Gateway** or **TWS** running locally with API enabled

### Install

```bash
git clone https://github.com/GameMaster301/IBKR-TWS-Web-Portfolio-Dashboard.git
cd IBKR-TWS-Web-Portfolio-Dashboard
pip install -r requirements.txt
```

### Step 1 — Enable the API in IB Gateway or TWS

The dashboard works with both. Pick whichever you already have running.

---

#### Option A — IB Gateway (recommended for dashboard use)

[Download IB Gateway](https://www.interactivebrokers.com/en/trading/ibgateway-stable.php) — headless, ~100 MB RAM, designed for API connections.

1. Open IB Gateway and log in
2. **Configure → Settings → API → Settings**
3. Tick **Enable ActiveX and Socket Clients**
4. Set the socket port: **4002** for paper trading, **4001** for live
5. Tick **Read-Only API** (the dashboard never places orders)
6. Click **OK**

Set `IBKR_PORT=4002` (paper) or `IBKR_PORT=4001` (live) when running the dashboard.

---

#### Option B — TWS (if you already have it open for manual trading)

1. In TWS: **Edit → Global Configuration → API → Settings**
2. Tick **Enable ActiveX and Socket Clients**
3. Set the socket port: **7497** for paper trading, **7496** for live
4. Tick **Read-Only API**
5. Click **OK** and restart TWS if prompted

Set `IBKR_PORT=7497` (paper) or `IBKR_PORT=7496` (live) when running the dashboard.

---

| | IB Gateway | TWS |
|---|---|---|
| Paper port | **4002** | **7497** |
| Live port | **4001** | **7496** |
| RAM usage | ~100 MB | ~1 GB |
| Needs a GUI | No | Yes |
| Best for | Always-on API dashboards | Active manual trading |

### Run

```bash
python main.py
```

Open **http://localhost:8050** in your browser. The dashboard auto-refreshes every 60 seconds.

---

## Docker — build from source

If you want to build the image yourself instead of pulling from Docker Hub:

### Prerequisites

- Docker Desktop (Mac / Windows) or Docker Engine + Compose v2 (Linux)
- TWS or IB Gateway running on your host machine with API enabled (see above)

### 1 — Configure

```bash
cp .env.example .env
# Edit .env — at minimum set IBKR_PORT
```

> **Linux only:** In `.env` replace `IBKR_HOST=host.docker.internal` with your
> LAN IP (e.g. `IBKR_HOST=192.168.1.100`) and uncomment the `extra_hosts`
> block in `docker-compose.yml`.

### 2 — Build & start

```bash
docker compose up --build -d
```

Open **http://localhost:8050**.

### 3 — View logs

```bash
docker compose logs -f dashboard
```

### 4 — Stop

```bash
docker compose down
```

### Useful commands

| Goal | Command |
|---|---|
| Rebuild after code changes | `docker compose up --build -d` |
| Tail live logs | `docker compose logs -f dashboard` |
| Check health | `docker inspect --format='{{.State.Health.Status}}' ibkrdash` |
| Open a shell inside | `docker exec -it ibkrdash bash` |

---

## Publishing (maintainers)

### Continuous Docker builds

Every push to `main` automatically builds and pushes `:latest` to Docker Hub via GitHub Actions.

### Creating a release

Tag a commit to trigger a GitHub Release with the user-facing setup zip attached automatically:

```bash
git tag v1.2.0
git push origin v1.2.0
```

The release workflow (`.github/workflows/release.yml`) will:
1. Build and push the Docker image tagged `:latest` and `:v1.2.0`
2. Package `ibkrdash-setup.zip` (contains `docker-compose.yml`, `.env`, all start/stop/update scripts, and `SETUP.txt`)
3. Create a GitHub Release with the zip and install instructions

### Required GitHub repository secrets

| Secret | Value |
|---|---|
| `DOCKERHUB_USERNAME` | Docker Hub username |
| `DOCKERHUB_TOKEN` | A Docker Hub [access token](https://app.docker.com/settings/personal-access-tokens) (read/write) |

> **Note:** pushing to `.github/workflows/` requires a GitHub Personal Access Token with the `workflow` scope.

---

## Configuration reference

All settings can be set via `config.yaml` **or** environment variables (env wins).

| Env var | Default | Description |
|---|---|---|
| `IBKR_HOST` | `127.0.0.1` | IB Gateway / TWS host |
| `IBKR_PORT` | `4002` | API port — IB Gateway: 4002 paper / 4001 live; TWS: 7497 paper / 7496 live |
| `IBKR_CLIENT_ID` | `1` | Must be unique per simultaneous API client |
| `IBKR_READONLY` | `true` | Read-only API (recommended) |
| `IBKR_RECONNECT_DELAY` | `5` | Base reconnect delay in seconds (exponential back-off) |
| `DASH_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` in Docker) |
| `DASH_PORT` | `8050` | HTTP port |
| `REFRESH_INTERVAL` | `60` | Auto-refresh interval in seconds |
| `OPEN_BROWSER` | `1` | Set to `0` to skip browser launch |
| `CONFIG_PATH` | `config.yaml` | Path to YAML config file |

---

## Troubleshooting

### Dashboard shows "Not connected"

- Make sure **IB Gateway** is open and logged in.
- In IB Gateway: **Configure → Settings → API → Settings** — confirm the port matches `IBKR_PORT` (default `4002` for paper, `4001` for live).
- Check **Enable ActiveX and Socket Clients** is ticked.
- If running in Docker on Linux, set `IBKR_HOST` to your machine's LAN IP, not `host.docker.internal`.

> **Using TWS instead?** The same steps apply — just use port **7497** (paper) or **7496** (live) and navigate via **Edit → Global Configuration → API → Settings**.

### Docker: "Connection refused" to IB Gateway

1. Confirm IB Gateway is running on the **host** machine (not inside Docker).
2. On macOS/Windows Docker Desktop, `host.docker.internal` resolves automatically.
3. On Linux, add to `docker-compose.yml`:
   ```yaml
   extra_hosts:
     - "host.docker.internal:host-gateway"
   ```
   Or set `IBKR_HOST` to your LAN IP.

### "Client ID already in use"

Each IB API client needs a unique `IBKR_CLIENT_ID`. If IB Gateway or TWS reports the ID is taken, change it in `.env` (e.g. `IBKR_CLIENT_ID=10`).

### Market Intelligence sections show yellow "temporarily unavailable"

This means a yfinance network call failed. It will retry automatically on the next 60-second refresh. If it persists, check your internet connection from inside the container:
```bash
docker exec -it ibkrdash python -c "import yfinance as yf; print(yf.Ticker('AAPL').info['regularMarketPrice'])"
```

---

## Project structure

```
├── main.py               Entry point — starts IBKR thread and Dash server
├── config.py             Config loader (YAML + env var overrides)
├── ibkr_client.py        Persistent IB Gateway / TWS connection with exponential back-off & heartbeat
├── dashboard.py          Dash layout, all callbacks, graceful error handling
├── data_processor.py     Position calculations and enrichment
├── analytics.py          Dividend data helpers (yfinance)
├── market_intel.py       Sector/geo exposure and earnings calendar (yfinance)
├── market_valuation.py   Macro indicators — Buffett, S&P 500 P/E, Shiller CAPE, 10-yr Treasury
├── trade_history.py      CSV upload path for historical trades
├── assets/
│   └── custom.css        Dashboard CSS overrides
├── config.yaml           Default configuration
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
│
├── install.ps1           Windows one-liner installer (creates desktop shortcut)
├── install.sh            Mac / Linux one-liner installer
├── start.bat / start.sh  Start the dashboard and open browser
├── stop.bat  / stop.sh   Stop the dashboard
├── update.bat/ update.sh Pull latest image and restart
├── SETUP.txt             Plain-English setup guide (included in release zip)
│
├── .github/
│   └── workflows/
│       ├── docker-publish.yml   Builds & pushes :latest on every push to main
│       └── release.yml          Builds image + creates GitHub Release zip on git tag
├── .dockerignore
└── .env.example
```

---

## Tech stack

| Layer | Library |
|---|---|
| IBKR API | [ib_async](https://github.com/ib-api-reloaded/ib_async) |
| Data processing | [pandas](https://pandas.pydata.org/), [numpy](https://numpy.org/) |
| Market data | [yfinance](https://github.com/ranaroussi/yfinance) |
| Dashboard & charts | [Plotly Dash](https://dash.plotly.com/) + Plotly |
| PDF export | [reportlab](https://www.reportlab.com/) |
| Containers | Docker + Compose |

---

## Notes

- The dashboard connects in **read-only mode** — it cannot place, modify, or cancel orders.
- IB Gateway ports: `4002` (paper) / `4001` (live). TWS ports: `7497` (paper) / `7496` (live).
- yfinance data (market intelligence, valuation indicators) is cached in memory for 4 hours; IBKR market data refreshes every 60 seconds.

---

## License

MIT
