import logging
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)

# ── Dividend cache ────────────────────────────────────────────────────────────
_div_cache: dict = {}
_DIV_TTL = 14400  # 4 hours


def get_dividend_data_yf(tickers: list) -> dict:
    """
    Fetch dividend info from yfinance for a list of tickers.
    Returns {ticker: {past_12m, next_12m, next_date, next_amount}}.
    Cached per ticker for 4 hours. Fetches in parallel (max 5 workers).
    """
    import yfinance as yf
    now    = time.time()
    result = {}

    to_fetch = []
    for sym in tickers:
        if sym in _div_cache:
            fetched_at, cached = _div_cache[sym]
            if now - fetched_at < _DIV_TTL:
                if cached:
                    result[sym] = cached
                continue
        to_fetch.append(sym)

    if not to_fetch:
        return result

    def fetch_one(sym):
        for attempt in range(3):
            try:
                info     = yf.Ticker(sym).info
                div_rate = info.get('dividendRate') or info.get('trailingAnnualDividendRate')
                if not div_rate or float(div_rate) <= 0:
                    return sym, None
                ex_ts    = info.get('exDividendDate')
                ex_date  = datetime.fromtimestamp(ex_ts).strftime('%Y-%m-%d') if ex_ts else None
                last_div = info.get('lastDividendValue')
                return sym, {
                    'past_12m':    round(float(div_rate), 4),
                    'next_12m':    round(float(div_rate), 4),
                    'next_date':   ex_date,
                    'next_amount': round(float(last_div), 4) if last_div else None,
                }
            except Exception as e:
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                else:
                    log.warning("yfinance dividend failed for %s: %s", sym, e)
                    return sym, None

    with ThreadPoolExecutor(max_workers=min(len(to_fetch), 5)) as pool:
        for sym, data in pool.map(fetch_one, to_fetch):
            _div_cache[sym] = (now, data)
            if data:
                result[sym] = data

    return result



