"""
Per-ticker dividend enrichment via yfinance.

Used as a fallback when IBKR's native dividend-tick data is empty (common
for non-US instruments). Results are persisted through `cache_util` with
a 4-hour TTL so cold starts don't re-hit yfinance for every ticker.
"""

from __future__ import annotations

import logging
from datetime import datetime

from cache_util import cached_fetch
from net_util import fetch_parallel, fetch_with_retry

log = logging.getLogger(__name__)

_DIV_TTL = 3600 * 4   # 4 hours; backed by cache_util (diskcache, persists across restarts)


def _fetch_one_dividend(sym: str):
    """Hit yfinance for a single symbol's dividend info. Returns a dict or
    None. Retries transient failures; returns None (not raise) after
    exhausting retries so a single bad ticker doesn't poison the batch."""
    import yfinance as yf

    def once():
        info     = yf.Ticker(sym).info
        div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate')
        if not div_rate or float(div_rate) <= 0:
            return None
        ex_ts    = info.get('exDividendDate')
        ex_date  = datetime.fromtimestamp(ex_ts).strftime('%Y-%m-%d') if ex_ts else None
        last_div = info.get('lastDividendValue')
        return {
            'past_12m':    round(float(div_rate), 4),
            'next_12m':    round(float(div_rate), 4),
            'next_date':   ex_date,
            'next_amount': round(float(last_div), 4) if last_div else None,
        }

    try:
        return fetch_with_retry(once, retries=3, base_delay=2.0)
    except Exception as e:
        log.warning("yfinance dividend failed for %s: %s", sym, e)
        return None


def get_dividend_data_yf(tickers: list) -> dict:
    """
    Fetch dividend info from yfinance for a list of tickers.
    Returns {ticker: {past_12m, next_12m, next_date, next_amount}}.
    Cached per ticker for 4 hours (persisted to disk). Fetches in parallel.
    """
    if not tickers:
        return {}

    def fetch_one(sym: str):
        return cached_fetch(('dividend', sym), _DIV_TTL,
                            lambda s=sym: _fetch_one_dividend(s))

    results = fetch_parallel(tickers, fetch_one, max_workers=5)
    return {sym: data for sym, data in results.items() if data}
