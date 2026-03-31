# IBKR Portfolio Dashboard

A real-time portfolio dashboard for Interactive Brokers, built with Python and Plotly Dash.

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![Dash](https://img.shields.io/badge/Plotly_Dash-latest-blue) ![License](https://img.shields.io/badge/license-MIT-green)

## Features

- **Live holdings table** — positions, average cost, current price, market value, P&L
- **Summary cards** — total portfolio value, unrealized P&L, today's P&L, and cash balance
- **Allocation donut chart** — visual breakdown of portfolio weights
- **EUR/USD conversion** — live exchange rate pulled directly from IBKR
- **Account metrics** — net liquidation, buying power, available funds
- **Connection status** — live badge showing TWS connection state with friendly error messages

## Requirements

- Interactive Brokers account (paper or live)
- TWS (Trader Workstation) running locally
- Python 3.10+

## Installation

```bash
git clone https://github.com/GameMaster301/IBKR-TWR-Web-Portofolio-Dashboard.git
cd IBKR-TWR-Web-Portofolio-Dashboard
pip install -r requirements.txt
```

## TWS Setup

1. Open TWS and log in
2. Go to **Edit → Global Configuration → API → Settings**
3. Check **Enable ActiveX and Socket Clients**
4. Set socket port to **7497** (paper) or **7496** (live)
5. Click OK and restart TWS

## Usage

```bash
python main.py
```

Then open your browser at `http://localhost:8050`. The dashboard connects automatically and refreshes every 60 seconds.

## Project Structure

```
├── main.py            # Entry point
├── dashboard.py       # Dash layout and callbacks
├── ibkr_client.py     # TWS connection and data fetching
├── data_processor.py  # Portfolio calculations
├── database.py        # SQLite snapshot storage
└── requirements.txt
```

## Tech Stack

- [ib_insync](https://github.com/erdewit/ib_insync) — Interactive Brokers API
- [Plotly Dash](https://dash.plotly.com/) — web dashboard framework
- [Pandas](https://pandas.pydata.org/) — data processing
- [SQLite](https://www.sqlite.org/) — local data persistence
