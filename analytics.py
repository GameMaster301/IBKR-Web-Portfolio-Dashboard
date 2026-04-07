import time
import pandas as pd
import numpy as np
from math import sqrt
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Risk-free rate: ~4.5% annual → daily
_RF_DAILY = 0.045 / 252

# ── Benchmark cache ───────────────────────────────────────────────────────────
_bench_cache: dict = {}
_CACHE_TTL = 3600  # 1 hour

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
            print(f"yfinance dividend failed for {sym}: {e}")
            return sym, None

    with ThreadPoolExecutor(max_workers=min(len(to_fetch), 5)) as pool:
        for sym, data in pool.map(fetch_one, to_fetch):
            _div_cache[sym] = (now, data)
            if data:
                result[sym] = data

    return result


def get_benchmark_series(ticker: str, start_date, end_date) -> pd.DataFrame | None:
    """
    Returns a DataFrame with columns ['date', 'value'] where value is
    normalized to 100 at start_date.  Cached for 1 hour.
    """
    cache_key = (ticker, str(start_date)[:10])
    now = time.time()

    if cache_key in _bench_cache:
        fetched_at, cached_df = _bench_cache[cache_key]
        if now - fetched_at < _CACHE_TTL:
            return cached_df

    try:
        import yfinance as yf
        fetch_start = (pd.Timestamp(start_date) - pd.Timedelta(days=10)).strftime('%Y-%m-%d')
        fetch_end   = (pd.Timestamp(end_date)   + pd.Timedelta(days=2)).strftime('%Y-%m-%d')
        raw = yf.download(ticker, start=fetch_start, end=fetch_end,
                          progress=False, auto_adjust=True)
        if raw.empty:
            return None

        closes = raw['Close'].squeeze()
        closes.index = pd.to_datetime(closes.index)

        # Normalize to 100 at the first date on or after start_date
        mask = closes.index >= pd.Timestamp(start_date)
        if not mask.any():
            return None
        base = closes[mask].iloc[0]
        normalized = (closes / base * 100).round(4)

        result = pd.DataFrame({'date': normalized.index, 'value': normalized.values})
        _bench_cache[cache_key] = (now, result)
        return result

    except Exception as e:
        print(f"Benchmark fetch failed ({ticker}): {e}")
        return None


def calculate_analytics(rows):
    """
    rows: list of (date, total_value, total_pnl) from get_all_snapshots()
    Returns a dict of metrics, or None if insufficient data.
    """
    if len(rows) < 2:
        return None

    df = pd.DataFrame(rows, columns=['date', 'total_value', 'total_pnl'])
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    values = df['total_value']
    df['daily_return'] = values.pct_change()
    returns = df['daily_return'].dropna()

    # ── Rolling returns ──────────────────────────────────────────────────────
    def period_return(days):
        needed = days + 1
        if len(df) < needed:
            return None
        start = df.iloc[-needed]['total_value']
        end   = df.iloc[-1]['total_value']
        return round((end - start) / start * 100, 2)

    # YTD: first snapshot this calendar year → today
    this_year = datetime.now().year
    ytd_df = df[df['date'].dt.year == this_year]
    if len(ytd_df) >= 2:
        ytd = round(
            (ytd_df.iloc[-1]['total_value'] - ytd_df.iloc[0]['total_value'])
            / ytd_df.iloc[0]['total_value'] * 100, 2)
    else:
        ytd = None

    # ── Max drawdown ─────────────────────────────────────────────────────────
    rolling_peak = values.cummax()
    drawdown_series = (values - rolling_peak) / rolling_peak * 100
    max_drawdown = round(drawdown_series.min(), 2)   # negative number

    # Index of deepest trough and its preceding peak
    trough_idx = drawdown_series.idxmin()
    peak_idx   = values.iloc[:trough_idx + 1].idxmax()
    dd_peak_date  = df.loc[peak_idx,  'date']
    dd_trough_date = df.loc[trough_idx, 'date']

    # ── Sharpe ratio (annualised) ────────────────────────────────────────────
    if len(returns) >= 20:
        excess   = returns - _RF_DAILY
        sharpe   = round(excess.mean() / returns.std() * sqrt(252), 2)
    else:
        sharpe = None

    # ── Annualised volatility ────────────────────────────────────────────────
    if len(returns) >= 2:
        volatility = round(returns.std() * sqrt(252) * 100, 2)
    else:
        volatility = None

    # ── Win rate ─────────────────────────────────────────────────────────────
    if len(returns) > 0:
        win_rate = round((returns > 0).sum() / len(returns) * 100, 1)
    else:
        win_rate = None

    # ── Best / worst single day ──────────────────────────────────────────────
    best_day  = round(returns.max() * 100, 2) if len(returns) > 0 else None
    worst_day = round(returns.min() * 100, 2) if len(returns) > 0 else None

    # ── Equity curve data (for chart) ───────────────────────────────────────
    curve = df[['date', 'total_value']].copy()
    curve['rolling_peak'] = rolling_peak
    curve['drawdown_pct'] = drawdown_series

    return {
        'return_7d':       period_return(7),
        'return_30d':      period_return(30),
        'return_90d':      period_return(90),
        'return_ytd':      ytd,
        'max_drawdown':    max_drawdown,
        'dd_peak_date':    dd_peak_date.strftime('%b %d, %Y'),
        'dd_trough_date':  dd_trough_date.strftime('%b %d, %Y'),
        'sharpe':          sharpe,
        'volatility':      volatility,
        'win_rate':        win_rate,
        'best_day':        best_day,
        'worst_day':       worst_day,
        'num_days':        len(df),
        'curve':           curve.to_dict('records'),
    }

