"""
Market intelligence for the portfolio dashboard.

All public functions are cached for 4 hours in a module-level dict so that
repeated Dash callback invocations don't hit yfinance every time.

Robustness improvements vs original:
- Every yfinance call has a per-symbol try/except; one bad ticker never
  breaks the whole batch.
- Simple retry wrapper (_fetch_with_retry) handles transient network errors.
- All functions return empty/None rather than raising, so Dash callbacks
  can show a friendly "data unavailable" message instead of a 500 error.

Functions
---------
get_price_history       — bulk OHLCV, returns per-ticker dates/prices/returns
get_correlation_matrix  — pairwise Pearson over daily returns
get_sector_geo          — sector, industry, country per ticker via yfinance
get_earnings_data       — next earnings date + historical 1-day post-earnings moves
compute_efficient_frontier — Monte Carlo weight simulation (uses cached history)
"""

import logging
import time
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)

# ── Yahoo Finance symbol resolution ───────────────────────────────────────────
# European tickers listed on non-US exchanges are not found by their plain
# IBKR symbol on Yahoo Finance — they need an exchange suffix (e.g. SPPE→SPPE.DE).
# These helpers are used by every function that calls yfinance so that a single
# European holding never silently breaks an entire section.

_EU_SUFFIXES = ['.DE', '.L', '.PA', '.AS', '.MI', '.SW', '.BR', '.LS', '.MC']

# Per-session cache: plain IBKR symbol → resolved Yahoo Finance symbol string.
# Avoids redundant .info network calls on every 4-hour cache refresh.
_YF_SYM_CACHE: dict = {}


def _yf_info(sym: str) -> dict:
    """
    Return yfinance .info dict for sym, trying EU exchange suffixes on failure.
    Result is NOT separately cached here — the caller's _CACHE handles that.
    """
    import yfinance as yf
    try:
        info = yf.Ticker(sym).info
        if info.get('quoteType'):
            return info
    except Exception:
        pass
    for suffix in _EU_SUFFIXES:
        try:
            alt = yf.Ticker(sym + suffix).info
            if alt.get('quoteType'):
                log.debug('[market_intel] %s resolved via %s%s', sym, sym, suffix)
                return alt
        except Exception:
            continue
    return {}


def _resolve_yf_sym(sym: str) -> str:
    """
    Return the Yahoo Finance symbol string to use for a given IBKR ticker.
    Uses _YF_SYM_CACHE so the resolution network call only happens once per session.
    """
    if sym in _YF_SYM_CACHE:
        return _YF_SYM_CACHE[sym]
    import yfinance as yf
    resolved = sym
    try:
        if yf.Ticker(sym).info.get('quoteType'):
            _YF_SYM_CACHE[sym] = sym
            return sym
    except Exception:
        pass
    for suffix in _EU_SUFFIXES:
        try:
            if yf.Ticker(sym + suffix).info.get('quoteType'):
                resolved = sym + suffix
                log.debug('[market_intel] %s resolved to %s', sym, resolved)
                break
        except Exception:
            continue
    _YF_SYM_CACHE[sym] = resolved
    return resolved


# ── In-process cache ───────────────────────────────────────────────────────────
_CACHE: dict = {}
_TTL = 3600 * 4   # 4 hours


def _cached(key, fn):
    """Return cached value if fresh, else call fn(), cache, and return."""
    now = time.time()
    if key in _CACHE:
        ts, val = _CACHE[key]
        if now - ts < _TTL:
            return val
    val = fn()
    _CACHE[key] = (now, val)
    return val


def _fetch_with_retry(fn, retries: int = 3, delay: float = 2.0):
    """
    Call fn() up to `retries` times, sleeping `delay` seconds between
    attempts.  Returns the result or raises the last exception.
    """
    last_exc = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
    raise last_exc


# ── Price history ──────────────────────────────────────────────────────────────

def get_price_history(tickers: list, period: str = '90d') -> dict:
    """
    Bulk-download adjusted closes for all tickers in one yfinance call.

    Returns
    -------
    {ticker: {'dates': [str, ...], 'prices': [float, ...], 'returns': [float, ...]}}
    Tickers with fewer than 10 trading days of data are excluded.
    """
    import yfinance as yf

    key = ('prices', tuple(sorted(tickers)), period)

    def fetch():
        result = {}
        if not tickers:
            return result
        try:
            def _download():
                syms = tickers if len(tickers) > 1 else tickers[0]
                return yf.download(syms, period=period, auto_adjust=True,
                                   progress=False, threads=True)

            raw = _fetch_with_retry(_download)
            if raw.empty:
                closes = pd.DataFrame()
            elif len(tickers) == 1:
                closes = pd.DataFrame({tickers[0]: raw['Close'].squeeze()})
            else:
                closes = raw['Close']

            closes = closes.dropna(how='all') if not closes.empty else closes

            # For any ticker that returned no data, retry with EU exchange suffixes.
            # This handles European tickers like SPPE (XETRA) that Yahoo Finance
            # only knows as SPPE.DE — the bulk download silently drops them.
            missing = [s for s in tickers
                       if s not in closes.columns
                       or closes[s].dropna().empty]
            alt_series: dict = {}
            for sym in missing:
                for suffix in _EU_SUFFIXES:
                    try:
                        alt = yf.download(sym + suffix, period=period,
                                          auto_adjust=True, progress=False)
                        if not alt.empty:
                            s_data = alt['Close'].squeeze().dropna()
                            if len(s_data) >= 10:
                                alt_series[sym] = s_data
                                log.debug('[market_intel] %s resolved via %s%s for prices',
                                          sym, sym, suffix)
                                break
                    except Exception:
                        continue

            for sym in tickers:
                if sym in alt_series:
                    s = alt_series[sym]
                elif sym in closes.columns:
                    s = closes[sym].dropna()
                else:
                    continue
                if len(s) < 10:
                    continue
                r = s.pct_change().dropna()
                result[sym] = {
                    'dates':   [d.strftime('%Y-%m-%d') for d in s.index],
                    'prices':  s.round(4).tolist(),
                    'returns': r.round(6).tolist(),
                }
        except Exception as e:
            log.warning('[market_intel] price_history error: %s', e)
        return result

    return _cached(key, fetch)


# ── Correlation matrix ─────────────────────────────────────────────────────────

def get_correlation_matrix(tickers: list, period: str = '90d') -> dict:
    """
    Pairwise Pearson correlation of daily returns.

    Returns
    -------
    {'tickers': [...], 'matrix': [[float, ...], ...]}
    Empty matrix if < 2 tickers have enough history.
    """
    hist  = get_price_history(tickers, period)
    valid = [s for s in tickers if s in hist and len(hist[s]['returns']) >= 20]
    if len(valid) < 2:
        return {'tickers': valid, 'matrix': []}

    n    = min(len(hist[s]['returns']) for s in valid)
    df   = pd.DataFrame({s: hist[s]['returns'][-n:] for s in valid})
    corr = df.corr().round(2)
    return {'tickers': valid, 'matrix': corr.values.tolist()}


# ── Sector & geography ─────────────────────────────────────────────────────────

def get_sector_geo(tickers: list) -> dict:
    """
    Fetch sector, industry, and country for each ticker via yfinance.
    ETFs typically return no sector; they are labelled 'ETF / Fund'.

    Returns
    -------
    {ticker: {'sector': str, 'industry': str, 'country': str, 'longName': str}}
    """
    import yfinance as yf

    key = ('sector_geo', tuple(sorted(tickers)))

    def fetch():
        def one(sym):
            try:
                info = _yf_info(sym)
                is_etf = info.get('quoteType', '').upper() == 'ETF'
                if is_etf:
                    sector  = info.get('category') or 'ETF / Fund'
                    industry = sector
                    # Infer geographic exposure from the category name — this
                    # reflects the ETF's *underlying holdings*, not where it's
                    # listed (a UCITS S&P 500 ETF listed in Germany is still US).
                    cat = sector.lower()
                    if any(x in cat for x in ('u.s.', 'u.s. ', 'us ', 's&p', 'nasdaq',
                                              'america', 'united states', 'domestic')):
                        country = 'United States'
                    elif any(x in cat for x in ('europe', 'european', 'eurozone')):
                        country = 'Europe'
                    elif any(x in cat for x in ('emerging', 'em bond', 'em equity')):
                        country = 'Emerging Markets'
                    elif any(x in cat for x in ('global', 'world', 'international')):
                        country = 'Global'
                    elif any(x in cat for x in ('china', 'japan', 'india', 'pacific')):
                        country = cat.split()[0].title()
                    else:
                        country = 'ETF / Global'
                else:
                    sector   = info.get('sector')   or 'Unknown'
                    industry = info.get('industry') or 'Unknown'
                    country  = info.get('country')  or 'Unknown'
                return sym, {
                    'sector':   sector,
                    'industry': industry,
                    'country':  country,
                    'longName': info.get('longName') or sym,
                }
            except Exception as e:
                log.warning('[market_intel] sector_geo %s: %s', sym, e)
                return sym, {
                    'sector': 'Unknown', 'industry': 'Unknown',
                    'country': 'Unknown', 'longName': sym,
                }

        with ThreadPoolExecutor(max_workers=min(len(tickers), 6)) as pool:
            return dict(pool.map(one, tickers))

    return _cached(key, fetch)


# ── Earnings calendar ──────────────────────────────────────────────────────────

def get_earnings_data(tickers: list) -> dict:
    """
    Return next earnings date and historical post-earnings 1-day price moves.

    Returns
    -------
    {ticker: {'next_date': str|None, 'avg_1d_move': float|None,
              'last_1d_moves': [float, ...]}}
    """
    import yfinance as yf
    from datetime import datetime

    key = ('earnings', tuple(sorted(tickers)))

    def fetch():
        def one(sym):
            out = {'next_date': None, 'avg_1d_move': None, 'last_1d_moves': []}
            try:
                # Resolve to the correct Yahoo Finance symbol (e.g. SPPE → SPPE.DE)
                yf_sym = _resolve_yf_sym(sym)
                t      = yf.Ticker(yf_sym)
                info   = _fetch_with_retry(lambda: t.info, retries=2)

                # Next earnings date
                for field in ('earningsTimestamp', 'earningsTimestampStart'):
                    ts = info.get(field)
                    if ts and isinstance(ts, (int, float)) and ts > 0:
                        try:
                            out['next_date'] = datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
                        except Exception:
                            pass
                        break

                # Historical 1-day post-earnings moves
                try:
                    ed = t.earnings_dates
                    if ed is not None and not ed.empty:
                        now_ts = pd.Timestamp.now()
                        past   = sorted(
                            [d for d in ed.index
                             if pd.Timestamp(d).tz_localize(None) < now_ts],
                            reverse=True,
                        )[:4]

                        if past:
                            hist = t.history(period='2y', interval='1d')
                            if not hist.empty:
                                if hist.index.tz:
                                    hist = hist.copy()
                                    hist.index = hist.index.tz_localize(None)
                                moves = []
                                for earn_dt in past:
                                    ts_e = pd.Timestamp(earn_dt)
                                    if ts_e.tz:
                                        ts_e = ts_e.tz_localize(None)
                                    after = hist.index[hist.index >= ts_e]
                                    if len(after) < 2:
                                        continue
                                    c0 = float(hist.loc[after[0], 'Close'])
                                    c1 = float(hist.loc[after[1], 'Close'])
                                    if c0 > 0:
                                        moves.append(round(abs((c1 - c0) / c0) * 100, 2))
                                if moves:
                                    out['last_1d_moves'] = moves
                                    out['avg_1d_move']   = round(sum(moves) / len(moves), 2)
                except Exception:
                    pass   # ETFs / tickers without earnings dates

            except Exception as e:
                log.warning('[market_intel] earnings %s: %s', sym, e)
            return sym, out

        with ThreadPoolExecutor(max_workers=min(len(tickers), 6)) as pool:
            return dict(pool.map(one, tickers))

    return _cached(key, fetch)


# ── Efficient frontier ─────────────────────────────────────────────────────────

def compute_efficient_frontier(tickers: list, weights: list,
                                period: str = '90d', n: int = 2500) -> dict | None:
    """
    Monte Carlo simulation of n random portfolio weight combinations.

    Returns
    -------
    {
      'portfolios': [{'vol': float, 'ret': float, 'sharpe': float}, ...],
      'current':    {'vol': float, 'ret': float, 'sharpe': float},
      'tickers':    [str, ...],
    }
    Returns None if fewer than 2 tickers have sufficient history.
    """
    try:
        hist  = get_price_history(tickers, period)
        valid = [s for s in tickers if s in hist and len(hist[s]['returns']) >= 20]
        if len(valid) < 2:
            return None

        min_n = min(len(hist[s]['returns']) for s in valid)
        R     = np.array([hist[s]['returns'][-min_n:] for s in valid])

        mu  = R.mean(axis=1)
        cov = np.cov(R)
        rf  = 0.045 / 252
        ann = 252

        np.random.seed(0)
        portfolios = []
        for _ in range(n):
            w     = np.random.dirichlet(np.ones(len(valid)))
            p_ret = float(np.dot(w, mu))          * ann * 100
            p_vol = float(np.sqrt(w @ cov @ w))   * np.sqrt(ann) * 100
            p_sr  = float((np.dot(w, mu) - rf)
                          / np.sqrt(w @ cov @ w)  * np.sqrt(ann))
            portfolios.append({'vol': round(p_vol, 2),
                               'ret': round(p_ret, 2),
                               'sharpe': round(p_sr, 2)})

        valid_set = set(valid)
        w_map     = {s: w for s, w in zip(tickers, weights) if s in valid_set}
        total_w   = sum(w_map.values())
        if total_w <= 0:
            w_cur = np.ones(len(valid)) / len(valid)
        else:
            w_cur = np.array([w_map.get(s, 0) / total_w for s in valid])

        c_ret = float(np.dot(w_cur, mu))            * ann * 100
        c_vol = float(np.sqrt(w_cur @ cov @ w_cur)) * np.sqrt(ann) * 100
        c_sr  = float((np.dot(w_cur, mu) - rf)
                      / np.sqrt(w_cur @ cov @ w_cur) * np.sqrt(ann))

        return {
            'portfolios': portfolios,
            'current':    {'vol': round(c_vol, 2),
                           'ret': round(c_ret, 2),
                           'sharpe': round(c_sr, 2)},
            'tickers':    valid,
        }
    except Exception as e:
        log.warning('[market_intel] efficient_frontier error: %s', e)
        return None
