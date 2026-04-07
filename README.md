# IBKR Portfolio Dashboard

A real-time portfolio dashboard for Interactive Brokers, built with Python and Plotly Dash. Connects directly to TWS via the `ib_insync` API — no third-party data providers, no delays.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)
![Dash](https://img.shields.io/badge/Plotly_Dash-2.x-119DFF?style=flat&logo=plotly&logoColor=white)
![ib_insync](https://img.shields.io/badge/ib__insync-0.9.x-orange?style=flat)
![SQLite](https://img.shields.io/badge/SQLite-3-003B57?style=flat&logo=sqlite&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-22c55e?style=flat)

---

## Features

- **Holdings table** — ticker, quantity, average cost, live price, market value (USD + EUR), unrealised P&L with colour-coded percentage pills, and portfolio weight
- **Summary cards** — total portfolio value, unrealised P&L, today's P&L, and cash balance in both EUR and USD
- **Allocation donut chart** — visual breakdown of portfolio weights by position
- **Live EUR/USD rate** — fetched directly from IBKR, not a third-party API
- **Market data enrichment** — daily change vs previous close, bid/ask spread, 52-week range position, VWAP, and volume per position
- **Portfolio snapshots** — SQLite database stores daily value and P&L for historical tracking
- **Connection status** — live badge showing TWS connection state with friendly, actionable error messages
- **Auto-refresh** — data reloads every 60 seconds automatically

---

## Requirements

- Interactive Brokers account (paper or live)
- TWS (Trader Workstation) or IB Gateway running locally
- Python 3.10+

---

## Installation

```bash
git clone https://github.com/GameMaster301/IBKR-TWS-Web-Portfolio-Dashboard.git
cd IBKR-TWS-Web-Portfolio-Dashboard
pip install -r requirements.txt
```

---

## TWS Setup

1. Open TWS and log in
2. Go to **Edit → Global Configuration → API → Settings**
3. Check **Enable ActiveX and Socket Clients**
4. Set socket port to **7497** (paper trading) or **7496** (live account)
5. Optionally check **Read-Only API** for safety — the dashboard never places orders
6. Click OK and restart TWS

---

## Usage

```bash
python main.py
```

Then open your browser at **http://localhost:8050**. The dashboard connects automatically and refreshes every 60 seconds.

---

## Project Structure

```
├── main.py            # Entry point — starts the server and opens the browser
├── dashboard.py       # Dash layout and all UI callbacks
├── ibkr_client.py     # TWS connection, data fetching (positions, market data, account)
├── data_processor.py  # Portfolio calculations and enrichment
├── database.py        # SQLite persistence for daily portfolio snapshots
└── requirements.txt
```

---

## Tech Stack

| Layer | Library |
|---|---|
| IBKR API | [ib_insync](https://github.com/erdewit/ib_insync) |
| Data processing | [pandas](https://pandas.pydata.org/) |
| Dashboard & charts | [Plotly Dash](https://dash.plotly.com/) + [Plotly](https://plotly.com/python/) |
| Persistence | SQLite (stdlib) |
| Concurrency | Python `threading` |

---

## Notes

- The dashboard connects in **read-only mode** (`readonly=True`) — it cannot place, modify, or cancel orders
- Each refresh opens a new TWS connection and closes it cleanly; a threading lock prevents concurrent fetches
- Daily portfolio snapshots are stored in `portfolio.db` (auto-created on first run)
- Paper trading uses port `7497`; live accounts use `7496` — double-check your TWS API settings before connecting

---

## License

MIT
