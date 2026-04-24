"""
Market intelligence for the portfolio dashboard.

All public functions are cached for 4 hours in a module-level dict so that
repeated Dash callback invocations don't hit yfinance every time.

Robustness improvements vs original:
- Every yfinance call has a per-symbol try/except; one bad ticker never
  breaks the whole batch.
- Transient network errors are retried via net_util.fetch_with_retry.
- All functions return empty/None rather than raising, so Dash callbacks
  can show a friendly "data unavailable" message instead of a 500 error.

Functions
---------
get_price_history  — bulk OHLCV, returns per-ticker dates/prices/returns
get_sector_geo     — sector, industry, country per ticker via yfinance
get_earnings_data  — next earnings date + historical 1-day post-earnings moves
"""

from __future__ import annotations

import logging

import pandas as pd

from cache_util import cached_fetch
from net_util import fetch_parallel, fetch_with_retry

log = logging.getLogger(__name__)

# ── Yahoo Finance symbol resolution ───────────────────────────────────────────
# European tickers listed on non-US exchanges are not found by their plain
# IBKR symbol on Yahoo Finance — they need an exchange suffix (e.g. SPPE→SPPE.DE).
# These helpers are used by every function that calls yfinance so that a single
# European holding never silently breaks an entire section.

_EU_SUFFIXES = ['.DE', '.L', '.PA', '.AS', '.MI', '.SW', '.BR', '.LS', '.MC']

# yfinance's ETF sector-weighting keys are lowercase with underscores
# (e.g. 'consumer_cyclical').  Map them to the same Title-Case names
# yfinance uses for stocks via .info['sector'], so ETF contributions
# can be merged straight into the portfolio sector totals.
_YF_SECTOR_NAMES = {
    'realestate':             'Real Estate',
    'real_estate':            'Real Estate',
    'consumer_cyclical':      'Consumer Cyclical',
    'basic_materials':        'Basic Materials',
    'consumer_defensive':     'Consumer Defensive',
    'technology':             'Technology',
    'communication_services': 'Communication Services',
    'financial_services':     'Financial Services',
    'utilities':              'Utilities',
    'industrials':            'Industrials',
    'energy':                 'Energy',
    'healthcare':             'Healthcare',
}


def _normalize_sector(raw: str) -> str:
    key = (raw or '').strip().lower().replace('-', '_').replace(' ', '_')
    return _YF_SECTOR_NAMES.get(key, (raw or '').strip().title() or 'Unknown')


def _fetch_etf_sector_weights(yf_ticker) -> dict:
    """
    Return {sector_name: fraction} for an ETF via Yahoo Finance's funds_data.
    Returns {} on any failure (missing attribute, network error, empty data).
    Fractions are kept as-is (sum to ~1.0).
    """
    try:
        fd = yf_ticker.funds_data
        raw = getattr(fd, 'sector_weightings', None)
        if not raw:
            return {}
        weights: dict = {}
        for k, v in raw.items():
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if f <= 0:
                continue
            name = _normalize_sector(k)
            weights[name] = weights.get(name, 0.0) + f
        return weights
    except Exception:
        return {}

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


# ── Cache TTL ──────────────────────────────────────────────────────────────────
_TTL = 3600 * 4   # 4 hours; backed by cache_util (diskcache, persists across restarts)


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

            raw = fetch_with_retry(_download)
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
                            if len(s_data) >= 2:
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
                if len(s) < 2:
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

    return cached_fetch(key, _TTL, fetch)


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
                sector_weights: dict = {}
                if is_etf:
                    sector  = info.get('category') or 'ETF / Fund'
                    industry = sector
                    # Fetch the ETF's real per-sector holdings breakdown
                    # so the dashboard can redistribute its weight across
                    # Technology, Financials, … instead of lumping it
                    # into a single 'ETF / Fund' slice.  Best-effort only:
                    # some ETFs (bonds, commodities, small providers) return
                    # nothing, in which case the caller falls back to the
                    # category label above.
                    try:
                        yf_sym = _resolve_yf_sym(sym)
                        sector_weights = _fetch_etf_sector_weights(yf.Ticker(yf_sym))
                    except Exception as e:
                        log.debug('[market_intel] ETF weights %s: %s', sym, e)
                    # Infer geographic exposure from the ETF name/category —
                    # this reflects the *underlying holdings*, not where it's
                    # listed (a UCITS S&P 500 ETF listed in Germany is still US).
                    # yfinance's `category` is often a Morningstar style-box
                    # label (e.g. "Large Blend") with no geographic keyword, so
                    # we also search longName/shortName which usually contain
                    # the index name (e.g. "SPDR S&P 500 UCITS ETF").
                    hay = ' '.join(filter(None, [
                        sector,
                        info.get('longName')  or '',
                        info.get('shortName') or '',
                    ])).lower()
                    if any(x in hay for x in ('u.s.', ' us ', 's&p', 'sp 500',
                                              'nasdaq', 'russell',
                                              'america', 'united states', 'domestic')):
                        country = 'United States'
                    elif any(x in hay for x in ('emerging', 'em bond', 'em equity')):
                        country = 'Emerging Markets'
                    elif any(x in hay for x in ('msci world', 'all-world', 'all world',
                                                'global', 'world', 'international',
                                                'developed markets')):
                        country = 'Global'
                    elif any(x in hay for x in ('europe', 'european', 'eurozone',
                                                'stoxx', 'euro stoxx',
                                                'ftse 100', 'ftse 250')):
                        country = 'Europe'
                    elif any(x in hay for x in ('china', 'japan', 'india', 'pacific',
                                                'korea', 'taiwan')):
                        for k in ('china', 'japan', 'india', 'pacific', 'korea', 'taiwan'):
                            if k in hay:
                                country = k.title()
                                break
                    else:
                        country = 'ETF / Global'
                else:
                    sector   = info.get('sector')   or 'Unknown'
                    industry = info.get('industry') or 'Unknown'
                    country  = info.get('country')  or 'Unknown'
                return {
                    'sector':         sector,
                    'industry':       industry,
                    'country':        country,
                    'longName':       info.get('longName') or sym,
                    'is_etf':         is_etf,
                    'sector_weights': sector_weights,
                }
            except Exception as e:
                log.warning('[market_intel] sector_geo %s: %s', sym, e)
                return {
                    'sector': 'Unknown', 'industry': 'Unknown',
                    'country': 'Unknown', 'longName': sym,
                    'is_etf': False, 'sector_weights': {},
                }

        return fetch_parallel(tickers, one)

    return cached_fetch(key, _TTL, fetch)


# ── Earnings calendar ──────────────────────────────────────────────────────────

def get_earnings_data(tickers: list) -> dict:
    """
    Return next earnings date and historical post-earnings 1-day price moves.

    Returns
    -------
    {ticker: {'next_date': str|None, 'avg_1d_move': float|None,
              'last_1d_moves': [float, ...]}}
    """
    from datetime import datetime

    import yfinance as yf

    key = ('earnings', tuple(sorted(tickers)))

    def fetch():
        def one(sym):
            out = {'next_date': None, 'avg_1d_move': None, 'last_1d_moves': []}
            try:
                # Resolve to the correct Yahoo Finance symbol (e.g. SPPE → SPPE.DE)
                yf_sym = _resolve_yf_sym(sym)
                t      = yf.Ticker(yf_sym)
                info   = fetch_with_retry(lambda: t.info, retries=2)

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
            return out

        return fetch_parallel(tickers, one)

    return cached_fetch(key, _TTL, fetch)

