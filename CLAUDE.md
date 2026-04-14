# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
# Local
python main.py

# Docker (rebuild on code change)
docker compose up --build -d
docker compose logs -f dashboard
```

There are no tests or linting commands configured. The app is validated by running it.

**TWS prerequisite:** Interactive Brokers TWS or IB Gateway must be running with API enabled (Edit → Global Configuration → API → Settings → Enable ActiveX and Socket Clients, port 7497 for paper / 7496 for live).

## Architecture

The app is a Plotly Dash single-page dashboard. One Python process runs two concurrent systems:

1. **IB background thread** (`ibkr_client.py`) — a daemon thread running its own `asyncio` event loop, maintaining a persistent `ib_async.IB()` connection to TWS with exponential back-off reconnect and a 30-second heartbeat. All IB calls are async coroutines dispatched via `asyncio.run_coroutine_threadsafe`.

2. **Dash web server** (`dashboard.py`) — Dash callbacks run in a Flask/Werkzeug thread pool. They call into the IB thread via the module-level `_conn` singleton in `ibkr_client.py`.

### Data flow

```
TWS ──► ibkr_client._do_fetch() ──► fetch_all_data()
                                         │
                               dashboard.fetch_data()   (every 60 s)
                                         │
                               dcc.Store('portfolio-data')
                                    │              │
                          rendering callbacks    populate_market_intel()
                          (summary, holdings,       │
                           donut, dividends)    dcc.Store('market-intel-data')
                                                     │
                                            4 rendering callbacks
                                            (sector/geo, earnings,
                                             frontier, scenarios)

dcc.Interval('refresh-interval') ──► populate_valuation_data()
                                         │
                                   dcc.Store('valuation-data')
                                         │
                                   render_market_valuation()
```

`portfolio-data` is the central store — everything downstream depends on it. The market intel and valuation stores are cached for 4 hours internally (module-level dicts in `market_intel.py` and `market_valuation.py`).

### Module responsibilities

| File | Role |
|---|---|
| `main.py` | Entry point — starts IB thread, opens browser, starts Dash server |
| `ibkr_client.py` | IB connection singleton, `fetch_all_data()` coroutine (positions, market data, dividends, EUR/USD, daily P&L) |
| `dashboard.py` | All Dash layout and callbacks — ~1800 lines, the core of the UI |
| `data_processor.py` | Pure pandas transforms: enriches raw positions with daily change, spread, 52w range, allocation % |
| `analytics.py` | `get_dividend_data_yf()` — yfinance dividend fallback with 4h cache and parallel fetching |
| `market_intel.py` | yfinance-backed: price history, correlation matrix, sector/geo, earnings, efficient frontier — all 4h cached |
| `market_valuation.py` | Macro indicators: Buffett (Wilshire/World Bank), S&P 500 P/E (multpl.com), Shiller CAPE (multpl.com) — 4h cached |
| `ai_analyst.py` | Calls Claude API (`claude-sonnet-4-20250514`, 600 tokens) with portfolio snapshot prompt |
| `config.py` | Merges `config.yaml` defaults → env var overrides, exposes `cfg` dict |

### Key Dash patterns used

- **`dcc.Store` as message bus** — callbacks never call each other directly; they read/write stores. `portfolio-data` is the source of truth for all rendering.
- **`prevent_initial_call=True`** on user-triggered callbacks (AI analysis, PDF export, position click).
- **`no_update`** returned from `populate_market_intel` when the ticker list hasn't changed, preventing full chart rebuilds on every 60-second refresh.
- **`clientside_callback`** for keyboard shortcuts (R = refresh, Esc = close detail panel) to avoid round-trips.
- **Parallel fetching** — `populate_market_intel` and `populate_valuation_data` both use `ThreadPoolExecutor` to fan out yfinance/HTTP calls concurrently.

### Caching layers

- **`ibkr_client`**: no cache — every `fetch_all_data()` call hits TWS live (60-second interval is the throttle).
- **`analytics.py` (`_div_cache`)**: per-ticker, 4 hours.
- **`market_intel.py` (`_CACHE`)**: per-(tickers, period) key, 4 hours.
- **`market_valuation.py` (`_CACHE`)**: per-metric key, 4 hours.

### Configuration priority

`config.yaml` defaults → env vars win. Key env vars: `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID`, `IBKR_READONLY`, `DASH_PORT`, `REFRESH_INTERVAL`, `ANTHROPIC_API_KEY`. Docker sets `DASH_HOST=0.0.0.0` and `OPEN_BROWSER=0` automatically.

### Adding a new dashboard section

1. Add a `html.Div(id='my-section')` to the layout in `dashboard.py`.
2. Write a `@app.callback(Output('my-section', 'children'), Input('portfolio-data', 'data'))` callback.
3. If it needs yfinance data, add it to `populate_market_intel` and read from `market-intel-data` store instead of fetching directly.
4. If it needs new IBKR data, add the fetch to `_do_fetch()` in `ibkr_client.py` and include it in the returned dict.