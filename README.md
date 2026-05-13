# IBKR Portfolio Dashboard

A real-time, private portfolio dashboard for Interactive Brokers — built with Python and Plotly Dash. Connects directly to **IB Gateway or TWS** via the `ib_async` API. No third-party data providers for live prices, no delays, read-only.

[![checks](https://github.com/GameMaster301/IBKR-Web-Portfolio-Dashboard/actions/workflows/checks.yml/badge.svg)](https://github.com/GameMaster301/IBKR-Web-Portfolio-Dashboard/actions/workflows/checks.yml)
[![Docker Hub](https://img.shields.io/docker/pulls/gmarinos/ibkrdash?logo=docker&logoColor=white&label=Docker+Hub)](https://hub.docker.com/r/gmarinos/ibkrdash)
![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat&logo=python&logoColor=white)
![Dash](https://img.shields.io/badge/Plotly_Dash-2.x-119DFF?style=flat&logo=plotly&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?style=flat&logo=docker&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-22c55e?style=flat)

---

## Try it instantly — no IBKR account needed

```bash
docker run --rm -e DEMO_MODE=1 -p 8050:8050 gmarinos/ibkrdash:latest
```

Open **http://localhost:8050**. Loads a realistic sample portfolio with live yfinance prices — every feature works, no brokerage connection required.

## Screenshots

<img width="1905" height="915" alt="screenshot" src="https://github.com/user-attachments/assets/3f49e696-01fe-41e5-a990-1e7b57e3e112" />
<img width="1903" height="914" alt="screenshot2" src="https://github.com/user-attachments/assets/21f82bd5-6179-43e7-99dc-ec8265fe59a3" />

## Install

> **Requires** [Docker Desktop](https://www.docker.com/products/docker-desktop/) (free, ~2 min). IB Gateway or TWS must be running on your machine for live data — see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#enabling-the-api-in-ib-gateway--tws).

**Windows** — paste into PowerShell:
```powershell
irm https://raw.githubusercontent.com/GameMaster301/IBKR-Web-Portfolio-Dashboard/main/install.ps1 | iex
```

**Mac / Linux** — paste into Terminal:
```bash
curl -fsSL https://raw.githubusercontent.com/GameMaster301/IBKR-Web-Portfolio-Dashboard/main/install.sh | bash
```

**No terminal?** Download the [latest release zip](https://github.com/GameMaster301/IBKR-Web-Portfolio-Dashboard/releases/latest), extract it, and follow `SETUP.txt`.

Each installer creates a desktop shortcut so double-clicking starts the dashboard at **http://localhost:8050**.

## Features

- **Holdings table** — quantity, avg cost, live price, market value (USD + EUR), unrealised P&L, weight, daily change, 52-week range, spread, VWAP, volume
- **Summary cards** — total value, unrealised P&L, today's P&L, cash
- **Allocation donut** + live EUR/USD rate from IBKR
- **Market Valuation** — Buffett Indicator, S&P 500 P/E, Shiller CAPE (50-yr chart), 10-yr Treasury yield, colour-coded by zone
- **Market Intelligence** — sector & geography exposure, earnings calendar with post-earnings moves
- **Dividends** — yield per position, projected 12-month income, payment schedule
- **Historical trades** — upload IBKR Transaction History CSV per position; BUY/SELL markers overlaid on price chart
- **Portfolio Coach** — chat panel with 5 rules-based scenarios out of the box; optionally paste an Anthropic / xAI / OpenAI key for free-form chat
- **PDF export**, **auto-reconnect**, **demo mode** — see [docs/DEMO.md](docs/DEMO.md)

## Configuration

All settings can be set via `config.yaml` **or** environment variables (env wins).

| Env var | Default | Description |
|---|---|---|
| `IBKR_HOST` | `127.0.0.1` | IB Gateway / TWS host |
| `IBKR_PORT` | `4002` | API port — IB Gateway: 4002 paper / 4001 live; TWS: 7497 paper / 7496 live. Falls through the other ports if the configured one can't connect. |
| `IBKR_CLIENT_ID` | `1` | Must be unique per simultaneous API client |
| `IBKR_READONLY` | `true` | Read-only API (recommended) |
| `IBKR_RECONNECT_DELAY` | `5` | Base reconnect delay (exponential back-off) |
| `DASH_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` in Docker) |
| `DASH_PORT` | `8050` | HTTP port |
| `REFRESH_INTERVAL` | `60` | Auto-refresh interval in seconds |
| `OPEN_BROWSER` | `1` | Set to `0` to skip browser launch |
| `DEMO_MODE` | `0` | Set to `1` to start in demo mode without an IBKR connection |
| `CONFIG_PATH` | `config.yaml` | Path to YAML config file |

## Troubleshooting

**Dashboard shows "Not connected"**
- Make sure IB Gateway or TWS is open and logged in.
- Confirm the API socket port matches `IBKR_PORT` and **Enable ActiveX and Socket Clients** is ticked.
- On Linux Docker, set `IBKR_HOST` to your LAN IP instead of `host.docker.internal`.

**Docker: "Connection refused" to IB Gateway**
- IB Gateway must run on the **host**, not inside Docker.
- macOS/Windows: `host.docker.internal` resolves automatically.
- Linux: add `extra_hosts: ["host.docker.internal:host-gateway"]` to `docker-compose.yml`, or use your LAN IP.

**"Client ID already in use"** — change `IBKR_CLIENT_ID` in `.env` (e.g. `IBKR_CLIENT_ID=10`).

**Market Intelligence shows yellow "temporarily unavailable"** — a yfinance call failed; it retries on the next 60-second refresh.

---

## Notes

- The dashboard connects in **read-only mode** — it cannot place, modify, or cancel orders.
- yfinance data is cached for 4 hours; IBKR market data refreshes every 60 seconds.

## More

- [docs/DEMO.md](docs/DEMO.md) — Demo mode in depth (entry methods, what works, exiting)
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — Source install, IB Gateway setup, Docker build, project structure, CI / releases

## License

MIT
