# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
# Local Python
python main.py

# Docker — build from source
cp .env.example .env          # first time only; edit IBKR_PORT and optionally ANTHROPIC_API_KEY
docker compose up --build -d
docker compose logs -f dashboard
```

There are no tests or linting commands configured. The app is validated by running it.

**IB Gateway prerequisite:** IB Gateway must be running with API enabled (Configure → Settings → API → Settings → Enable ActiveX and Socket Clients). Default port is **4002** (paper) or **4001** (live). TWS also works — use port 7497 (paper) or 7496 (live) and update `IBKR_PORT` accordingly.

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
| `dashboard.py` | All Dash layout and callbacks (~2000 lines) — the core of the UI |
| `data_processor.py` | Pure pandas transforms: enriches raw positions with daily change, spread, 52w range, allocation % |
| `analytics.py` | `get_dividend_data_yf()` — yfinance dividend fallback with 4h cache and parallel fetching |
| `market_intel.py` | yfinance-backed: price history, correlation matrix, sector/geo, earnings, efficient frontier — all 4h cached |
| `market_valuation.py` | Macro indicators: Buffett (Wilshire/World Bank), S&P 500 P/E (multpl.com), Shiller CAPE (multpl.com) — 4h cached |
| `ai_analyst.py` | Dual-mode AI: rule-based analysis/chat (no API key needed) with automatic upgrade to `claude-sonnet-4-6` when `ANTHROPIC_API_KEY` is set. Public entry points: `analyse_portfolio()` and `chat_portfolio()`. |
| `config.py` | Merges `config.yaml` defaults → env var overrides, exposes `cfg` dict |

### Key Dash patterns used

- **`dcc.Store` as message bus** — callbacks never call each other directly; they read/write stores. `portfolio-data` is the source of truth for all rendering.
- **`prevent_initial_call=True`** on user-triggered callbacks (AI analysis, PDF export, position click).
- **`no_update`** returned from `populate_market_intel` when the ticker list hasn't changed, preventing full chart rebuilds on every 60-second refresh.
- **`clientside_callback`** for keyboard shortcuts (R = refresh, Esc = close detail panel) to avoid round-trips.
- **Parallel fetching** — `populate_market_intel` and `populate_valuation_data` both use `ThreadPoolExecutor` to fan out yfinance/HTTP calls concurrently.

### Styling

All CSS customisation lives in `assets/custom.css`. Dash auto-serves everything in `assets/`. Inline styles in `dashboard.py` use the `CARD` dict (defined near the top of the file) as a shared base for card styling — extend it rather than copy-pasting raw style dicts.

### Caching layers

- **`ibkr_client`**: no cache — every `fetch_all_data()` call hits TWS live (60-second interval is the throttle).
- **`analytics.py` (`_div_cache`)**: per-ticker, 4 hours.
- **`market_intel.py` (`_CACHE`)**: per-(tickers, period) key, 4 hours.
- **`market_valuation.py` (`_CACHE`)**: per-metric key, 4 hours.

### Configuration priority

`config.yaml` defaults → env vars win. Key env vars: `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID`, `IBKR_READONLY`, `IBKR_RECONNECT_DELAY`, `DASH_HOST`, `DASH_PORT`, `REFRESH_INTERVAL`, `EURUSD_FALLBACK`, `ANTHROPIC_API_KEY`. `CONFIG_PATH` overrides the default `config.yaml` location (useful for Docker volume mounts). Docker sets `DASH_HOST=0.0.0.0` and `OPEN_BROWSER=0` automatically.

### Adding a new dashboard section

1. Add a `html.Div(id='my-section')` to the layout in `dashboard.py`.
2. Write a `@app.callback(Output('my-section', 'children'), Input('portfolio-data', 'data'))` callback.
3. Use `section_label('Title')` for the section header and `make_table(headers, rows)` for any tabular data — these helpers are defined near the top of `dashboard.py` and used by every existing section.
4. If it needs yfinance data, add it to `populate_market_intel` and read from `market-intel-data` store instead of fetching directly.
5. If it needs new IBKR data, add the fetch to `_do_fetch()` in `ibkr_client.py` and include it in the returned dict.

## CI / Docker Hub publishing

**On every push to `main`:** `.github/workflows/docker-publish.yml` builds a multi-platform image (`linux/amd64` + `linux/arm64`) and pushes it to Docker Hub as `gamemaster301/ibkrdash:latest`.

**On a version tag** (e.g. `git tag v1.2.0 && git push origin v1.2.0`): `.github/workflows/release.yml` does the same Docker build (also tags `:v1.2.0`) **and** creates a GitHub Release with `ibkrdash-setup.zip` attached. The zip contains `docker-compose.yml`, `.env`, all start/stop/update scripts, and `SETUP.txt`.

**Required GitHub repository secrets:**

| Secret | Value |
|---|---|
| `DOCKERHUB_USERNAME` | Docker Hub username |
| `DOCKERHUB_TOKEN` | Docker Hub access token (read/write scope) |

**Required PAT scope:** pushing changes to `.github/workflows/` requires a GitHub Personal Access Token with the `workflow` scope enabled.
