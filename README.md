# IBKR Portfolio Dashboard

A real-time, private portfolio dashboard for Interactive Brokers — built with Python and Plotly Dash. Connects directly to TWS via the `ib_async` API. No third-party data providers for live prices, no delays, read-only.

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat&logo=python&logoColor=white)
![Dash](https://img.shields.io/badge/Plotly_Dash-2.x-119DFF?style=flat&logo=plotly&logoColor=white)
![ib_async](https://img.shields.io/badge/ib__async-latest-orange?style=flat)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?style=flat&logo=docker&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-22c55e?style=flat)

---

## Features

- **Holdings table** — ticker, quantity, average cost, live price, market value (USD + EUR), unrealised P&L (value + %), portfolio weight, daily change, 52-week range position, spread, VWAP, volume
- **Summary cards** — total value, unrealised P&L, today's P&L, cash (EUR + % of portfolio)
- **Allocation donut chart** — visual portfolio weights
- **Live EUR/USD rate** — fetched directly from IBKR, not a third-party API
- **Market Valuation** — Buffett Indicator (Wilshire 5000 / US GDP), S&P 500 trailing P/E, Shiller CAPE with 50-year chart; each metric colour-coded by valuation zone
- **Market Intelligence** — correlation heatmap, sector & geography exposure, earnings calendar, historical scenario analysis, efficient frontier
- **Dividends tracker** — yield per position, projected annual income, upcoming payment schedule
- **AI analysis** — Claude-powered portfolio review (requires `ANTHROPIC_API_KEY`)
- **PDF export** — one-click portfolio snapshot download
- **Auto-reconnect** — exponential back-off with passive heartbeat; dashboard keeps working while TWS is restarting

---

## Quickest start — Docker Hub (no code required)

No Python, no git clone. You just need [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed.

### 1 — Create two files anywhere on your machine

**`docker-compose.yml`**
```yaml
services:
  dashboard:
    image: gamemaster301/ibkrdash:latest
    container_name: ibkrdash
    ports:
      - "8050:8050"
    environment:
      IBKR_HOST: ${IBKR_HOST:-host.docker.internal}
      IBKR_PORT: ${IBKR_PORT:-7497}
      IBKR_CLIENT_ID: ${IBKR_CLIENT_ID:-10}
      IBKR_READONLY: ${IBKR_READONLY:-true}
      DASH_HOST: 0.0.0.0
      DASH_PORT: 8050
      OPEN_BROWSER: "0"
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
    restart: unless-stopped
    # Linux only — uncomment if host.docker.internal doesn't resolve:
    # extra_hosts:
    #   - "host.docker.internal:host-gateway"
```

**`.env`**
```env
IBKR_PORT=7497
# ANTHROPIC_API_KEY=sk-ant-...   ← uncomment and fill in for AI analysis
```

### 2 — Start

```bash
docker compose up -d
```

Open **http://localhost:8050**. The image is pulled automatically on first run.

### Run on every boot automatically

In **Docker Desktop → Settings → General**, enable **"Start Docker Desktop when you log in"**.

Combined with `restart: unless-stopped`, the dashboard will be live at `localhost:8050` automatically whenever your machine starts.

### Update to the latest version

```bash
docker compose pull && docker compose up -d
```

---

## Quick start — local Python

### Requirements

- Python 3.12+
- Interactive Brokers account (paper or live)
- TWS or IB Gateway running locally

### Install

```bash
git clone https://github.com/GameMaster301/IBKR-TWS-Web-Portfolio-Dashboard.git
cd IBKR-TWS-Web-Portfolio-Dashboard
pip install -r requirements.txt
```

### TWS setup

1. Open TWS and log in
2. **Edit → Global Configuration → API → Settings**
3. Check **Enable ActiveX and Socket Clients**
4. Set socket port to **7497** (paper) or **7496** (live)
5. Optionally check **Read-Only API** — the dashboard never places orders
6. Click OK and restart TWS

### Run

```bash
python main.py
```

Open **http://localhost:8050** in your browser. The dashboard auto-refreshes every 60 seconds.

### Optional: AI analysis

```bash
# Windows
set ANTHROPIC_API_KEY=sk-ant-...
python main.py

# macOS / Linux
ANTHROPIC_API_KEY=sk-ant-... python main.py
```

---

## Docker — build from source

If you want to build the image yourself instead of pulling from Docker Hub:

### Prerequisites

- Docker Desktop (Mac / Windows) or Docker Engine + Compose v2 (Linux)
- TWS or IB Gateway running on your host machine with API enabled (see above)

### 1 — Configure

```bash
cp .env.example .env
# Edit .env — at minimum set IBKR_PORT and ANTHROPIC_API_KEY if you want AI
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

## Publishing a new image (maintainers)

Every push to `main` automatically builds and pushes a new `latest` image to Docker Hub via GitHub Actions. To enable this on a fork:

1. Go to **GitHub → repository → Settings → Secrets and variables → Actions**
2. Add two repository secrets:

| Secret | Value |
|---|---|
| `DOCKERHUB_USERNAME` | Your Docker Hub username |
| `DOCKERHUB_TOKEN` | A Docker Hub [access token](https://app.docker.com/settings/personal-access-tokens) (read/write) |

After that, every merge to `main` triggers a new build for `linux/amd64` and `linux/arm64` (Apple Silicon).

---

## Configuration reference

All settings can be set via `config.yaml` **or** environment variables (env wins).

| Env var | Default | Description |
|---|---|---|
| `IBKR_HOST` | `127.0.0.1` | TWS / IB Gateway host |
| `IBKR_PORT` | `7497` | API port (7497 paper, 7496 live) |
| `IBKR_CLIENT_ID` | `1` | Must be unique per simultaneous API client |
| `IBKR_READONLY` | `true` | Read-only API (recommended) |
| `IBKR_RECONNECT_DELAY` | `5` | Base reconnect delay in seconds (exponential back-off) |
| `DASH_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` in Docker) |
| `DASH_PORT` | `8050` | HTTP port |
| `REFRESH_INTERVAL` | `60` | Auto-refresh interval in seconds |
| `OPEN_BROWSER` | `1` | Set to `0` to skip browser launch |
| `CONFIG_PATH` | `config.yaml` | Path to YAML config file |
| `ANTHROPIC_API_KEY` | _(none)_ | Required for AI analysis |

---

## Troubleshooting

### Dashboard shows "Not connected to TWS"

- Make sure TWS is open and logged in.
- In TWS: **Edit → Global Configuration → API → Settings** — confirm the port matches `IBKR_PORT`.
- Check **Enable ActiveX and Socket Clients** is ticked.
- If running in Docker on Linux, set `IBKR_HOST` to your machine's LAN IP, not `host.docker.internal`.

### Docker: "Connection refused" to TWS

1. Confirm TWS is running on the **host** machine (not inside Docker).
2. On macOS/Windows Docker Desktop, `host.docker.internal` resolves automatically.
3. On Linux, add to `docker-compose.yml`:
   ```yaml
   extra_hosts:
     - "host.docker.internal:host-gateway"
   ```
   Or set `IBKR_HOST` to your LAN IP.

### "Client ID already in use"

Each IB API client needs a unique `IBKR_CLIENT_ID`. If TWS says the ID is taken, change it in `.env` (e.g. `IBKR_CLIENT_ID=10`).

### Market Intelligence sections show yellow "temporarily unavailable"

This means a yfinance network call failed. It will retry automatically on the next 60-second refresh. If it persists, check your internet connection from inside the container:
```bash
docker exec -it ibkrdash python -c "import yfinance as yf; print(yf.Ticker('AAPL').info['regularMarketPrice'])"
```

### AI analysis button does nothing / shows error

Ensure `ANTHROPIC_API_KEY` is set in your environment or `.env` file. The key never leaves your machine.

---

## Project structure

```
├── main.py               Entry point — starts IBKR thread and Dash server
├── config.py             Config loader (YAML + env var overrides)
├── ibkr_client.py        Persistent TWS connection with exponential back-off & heartbeat
├── dashboard.py          Dash layout, all callbacks, graceful error handling
├── data_processor.py     Position calculations and enrichment
├── analytics.py          Dividend data helpers (yfinance)
├── market_intel.py       Correlation, sector/geo, earnings, efficient frontier (yfinance)
├── market_valuation.py   Macro indicators — Buffett, S&P 500 P/E, Shiller CAPE
├── ai_analyst.py         Claude AI portfolio analysis
├── config.yaml           Default configuration
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .github/
│   └── workflows/
│       └── docker-publish.yml   CI/CD — builds & pushes image on every push to main
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
| AI analysis | [Anthropic Claude API](https://console.anthropic.com/) |
| PDF export | [reportlab](https://www.reportlab.com/) |
| Containers | Docker + Compose |

---

## Notes

- The dashboard connects in **read-only mode** — it cannot place, modify, or cancel orders.
- Paper trading uses port `7497`; live accounts use `7496`.
- yfinance data (market intelligence, valuation indicators) is cached in memory for 4 hours; IBKR market data refreshes every 60 seconds.

---

## License

MIT
