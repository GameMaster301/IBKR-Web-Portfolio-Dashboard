# Demo mode

No IB Gateway, no TWS, no account needed. Demo mode loads a deterministic sample portfolio so you can explore every feature before connecting a live account.

## How to launch

**Option 1 — from the UI (recommended)**

Start the dashboard normally. If no IBKR connection is detected, the start screen shows two buttons:

- **↻ Retry connection** — attempts to reconnect to IB Gateway / TWS
- **▶ Try demo mode** — loads the sample portfolio immediately, no config needed

**Option 2 — environment variable (Docker)**

```bash
echo "DEMO_MODE=1" >> .env
docker compose up -d
```

Or one-shot:

```bash
docker run --rm -e DEMO_MODE=1 -p 8050:8050 gmarinos/ibkrdash:latest
```

**Option 3 — CLI flag (running from source)**

```bash
python main.py --demo
```

Both `DEMO_MODE=1` and `--demo` resolve to the same flag inside the app.

## What works in demo mode

Everything. The sample portfolio is a realistic multi-stock, multi-currency holding set that exercises all dashboard sections:

| Section | Demo behaviour |
|---|---|
| Holdings table | Full position list with live yfinance prices |
| Summary cards | Total value, P&L, daily change, cash balance |
| Allocation donut | Portfolio weights by position |
| Position detail | Click any row — stats, price chart, fundamentals signal grid, investment brief, ETF fund overview |
| Market Intelligence | Sector / geography exposure, earnings calendar (real yfinance data for the sample tickers) |
| Market Valuation – US | Buffett Indicator, S&P 500 P/E, Shiller CAPE, Yield Gap (live macro data) |
| Dividends | Yield, projected income, payment schedule |
| Portfolio Coach | All 5 rules-based scenarios run against the sample portfolio; LLM chat works if you paste an API key |
| PDF export | Generates a snapshot of the demo portfolio |

## Exiting demo mode

Click **Exit demo** in the top-right header bar. The dashboard immediately attempts to reconnect to IB Gateway / TWS. If a live connection is established the real portfolio loads; otherwise the disconnected screen is shown.

## Notes

- Demo mode bypasses the IBKR background thread entirely — no socket connection is attempted.
- Prices shown in the holdings table are fetched live from yfinance for the sample tickers, so they reflect current market prices even though the positions and quantities are fixed.
- The demo portfolio resets to its original state on every restart (it is not persisted).
