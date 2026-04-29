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


def _get_info(sym: str) -> dict:
    """
    Return the yfinance .info dict for an already-resolved symbol, cached 4 hours.
    All callers go through here so that at most one .info network call is made
    per symbol per TTL window — no matter how many functions request the same ticker.
    """
    key = ('info', sym)
    def _fetch():
        import yfinance as yf
        try:
            info = yf.Ticker(sym).info or {}
            return info if info.get('quoteType') else {}
        except Exception:
            return {}
    return cached_fetch(key, _TTL, _fetch)


def _yf_info(sym: str) -> dict:
    """
    Return yfinance .info dict for sym, trying EU exchange suffixes on failure.
    Each candidate symbol goes through _get_info so the result is cached.
    """
    info = _get_info(sym)
    if info.get('quoteType'):
        return info
    for suffix in _EU_SUFFIXES:
        info = _get_info(sym + suffix)
        if info.get('quoteType'):
            log.debug('[market_intel] %s resolved via %s%s', sym, sym, suffix)
            return info
    return {}


def _resolve_yf_sym(sym: str) -> str:
    """
    Return the Yahoo Finance symbol string to use for a given IBKR ticker.
    Uses _YF_SYM_CACHE (session-level) so resolution only runs once per process.
    Internally delegates to _get_info so each candidate's .info is also cached.
    """
    if sym in _YF_SYM_CACHE:
        return _YF_SYM_CACHE[sym]
    if _get_info(sym).get('quoteType'):
        _YF_SYM_CACHE[sym] = sym
        return sym
    for suffix in _EU_SUFFIXES:
        if _get_info(sym + suffix).get('quoteType'):
            resolved = sym + suffix
            log.debug('[market_intel] %s resolved to %s', sym, resolved)
            _YF_SYM_CACHE[sym] = resolved
            return resolved
    _YF_SYM_CACHE[sym] = sym
    return sym


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


# ── Per-ticker fundamentals ────────────────────────────────────────────────────

def _safe_f(v) -> float | None:
    """Float or None; treats nan as None."""
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _fetch_fundamentals_one(sym: str) -> dict:
    """Raw fetch — called by get_stock_fundamentals, wrapped in cached_fetch."""
    import yfinance as yf

    # .info is the expensive call — go through the shared cache so this fetch
    # is deduplicated with _yf_info / _resolve_yf_sym for the same symbol.
    info = _get_info(sym)
    t    = yf.Ticker(sym)   # still needed for recommendations, history, etc.

    # ── Analyst buy / hold / sell counts ─────────────────────────────────────
    buy = hold = sell = None
    try:
        rs = t.recommendations_summary
        if rs is not None and not rs.empty:
            row = rs[rs['period'] == '0m'] if 'period' in rs.columns else rs.iloc[0:1]
            if row.empty:
                row = rs.iloc[0:1]
            if not row.empty:
                r    = row.iloc[0]
                buy  = int((r.get('strongBuy') or 0) + (r.get('buy') or 0))
                hold = int(r.get('hold') or 0)
                sell = int((r.get('sell') or 0) + (r.get('strongSell') or 0))
    except Exception:
        pass

    # ── EPS beat rate: last 4 quarters ───────────────────────────────────────
    eps_beat = None
    try:
        eh = t.earnings_history
        if eh is not None and not eh.empty:
            recent   = eh.tail(4)
            beats    = int((recent['epsActual'] > recent['epsEstimate']).sum())
            eps_beat = f"{beats}/{len(recent)}"
    except Exception:
        pass

    # ── Insider net activity (last 90 days) ──────────────────────────────────
    insider_net = None
    try:
        ins = t.insider_transactions
        if ins is not None and not ins.empty:
            cutoff = pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=90)
            dates  = pd.to_datetime(ins['startDate'], utc=True, errors='coerce')
            recent = ins[dates >= cutoff]
            if not recent.empty:
                txn_col = next((c for c in recent.columns
                                if 'transaction' in c.lower()), None)
                val_col = next((c for c in recent.columns
                                if c.lower() == 'value'), None)
                if txn_col and val_col:
                    b = abs(float(
                        recent[recent[txn_col].str.contains('Buy',  na=False, case=False)
                               ][val_col].sum() or 0))
                    s = abs(float(
                        recent[recent[txn_col].str.contains('Sell', na=False, case=False)
                               ][val_col].sum() or 0))
                    insider_net = ('Net+' if b > s * 1.5
                                   else 'Net−' if s > b * 1.5
                                   else 'Neutral')
    except Exception:
        pass

    # ── RSI-14, above/below 50d MA, volume vs average ────────────────────────
    rsi = above_50d = vol_label = None
    try:
        hist = t.history(period='3mo')
        if len(hist) >= 15:
            closes = hist['Close']
            delta  = closes.diff()
            gain   = delta.clip(lower=0).rolling(14).mean()
            loss   = (-delta.clip(upper=0)).rolling(14).mean()
            g, loss_avg = float(gain.iloc[-1]), float(loss.iloc[-1])
            if loss_avg and loss_avg == loss_avg:
                rsi = round(100 - 100 / (1 + g / loss_avg), 1)

        if len(hist) >= 2:
            current = float(hist['Close'].iloc[-1])
            ma50    = _safe_f(info.get('fiftyDayAverage'))
            if ma50:
                above_50d = current > ma50

            avg_vol  = float(hist['Volume'].replace(0, pd.NA).dropna().mean() or 0)
            last_vol = float(hist['Volume'].iloc[-1] or 0)
            if avg_vol > 0:
                ratio     = last_vol / avg_vol
                vol_label = 'High' if ratio > 1.5 else ('Low' if ratio < 0.5 else 'Normal')
    except Exception:
        pass

    # ── Price target upside % ────────────────────────────────────────────────
    target        = _safe_f(info.get('targetMeanPrice'))
    current_price = _safe_f(info.get('currentPrice') or info.get('regularMarketPrice'))
    target_upside = None
    if target and current_price and current_price > 0:
        target_upside = round((target - current_price) / current_price * 100, 1)

    rev    = _safe_f(info.get('revenueGrowth'))
    margin = _safe_f(info.get('profitMargins'))
    short  = _safe_f(info.get('shortPercentOfFloat'))

    # ── Balance sheet + cash flow (for Financial Health signal) ──────────────
    fcf_yield    = None
    debt_ebitda  = None
    net_cash_pct = None
    roe          = None
    try:
        mktcap   = _safe_f(info.get('marketCap'))
        fcf_raw  = _safe_f(info.get('freeCashflow'))
        if fcf_raw is not None and mktcap and mktcap > 0:
            fcf_yield = round(fcf_raw / mktcap * 100, 2)

        debt     = _safe_f(info.get('totalDebt'))
        cash     = _safe_f(info.get('totalCash'))
        ebitda   = _safe_f(info.get('ebitda'))
        if debt is not None and ebitda is not None and ebitda > 0:
            debt_ebitda = round(debt / ebitda, 2)
        if debt is not None and cash is not None and mktcap and mktcap > 0:
            net_cash_pct = round((cash - debt) / mktcap * 100, 1)

        roe_raw = _safe_f(info.get('returnOnEquity'))
        if roe_raw is not None:
            roe = round(roe_raw * 100, 1)
    except Exception:
        pass

    return {
        'fwd_pe':         _safe_f(info.get('forwardPE')),
        'peg':            _safe_f(info.get('pegRatio')),
        'ev_ebitda':      _safe_f(info.get('enterpriseToEbitda')),
        'rev_growth_pct': round(rev    * 100, 1) if rev    is not None else None,
        'profit_margin':  round(margin * 100, 1) if margin is not None else None,
        'eps_beat':       eps_beat,
        'analyst_buy':    buy,
        'analyst_hold':   hold,
        'analyst_sell':   sell,
        'price_target':   round(target, 2) if target else None,
        'target_upside':  target_upside,
        'short_pct':      round(short  * 100, 2) if short  is not None else None,
        'insider_net':    insider_net,
        'rsi_14':         rsi,
        'above_50d_ma':   above_50d,
        'volume_vs_avg':  vol_label,
        'fcf_yield':      fcf_yield,
        'debt_ebitda':    debt_ebitda,
        'net_cash_pct':   net_cash_pct,
        'roe':            roe,
    }


def get_stock_fundamentals(ticker: str) -> dict:
    """
    Valuation, growth, sentiment, and technical metrics for a single ticker.

    Returns
    -------
    {fwd_pe, peg, ev_ebitda, rev_growth_pct, profit_margin, eps_beat,
     analyst_buy, analyst_hold, analyst_sell, price_target, target_upside,
     short_pct, insider_net, rsi_14, above_50d_ma, volume_vs_avg,
     fcf_yield, debt_ebitda, net_cash_pct, roe}

    All values are float | str | bool | None — None means "not available".
    Cached 4 hours. Returns {} on total failure.
    """
    sym = _resolve_yf_sym(ticker)
    key = ('fundamentals', sym)
    try:
        return cached_fetch(key, _TTL, lambda: _fetch_fundamentals_one(sym))
    except Exception as e:
        log.warning('[market_intel] fundamentals %s: %s', ticker, e)
        return {}


def get_etf_brief_data(ticker: str) -> dict:
    """
    ETF-specific summary: category, expense ratio, AUM, sector weights.
    Returns {} for non-ETF tickers. Cached 4 hours.

    Returns
    -------
    {long_name, category, expense_ratio (% or None), total_assets ($ or None),
     sector_weights {sector: fraction}, geo}
    """
    sym = _resolve_yf_sym(ticker)
    key = ('etf_brief', sym)

    def _fetch():
        info = _get_info(sym)
        if info.get('quoteType', '').upper() != 'ETF':
            return {}
        import yfinance as yf
        expense_raw = _safe_f(
            info.get('annualReportExpenseRatio') or info.get('totalExpenseRatio')
        )
        expense_pct = round(expense_raw * 100, 3) if expense_raw is not None else None
        sector_weights = _fetch_etf_sector_weights(yf.Ticker(sym))
        # geographic exposure — infer from name/category (same logic as get_sector_geo)
        hay = ' '.join(filter(None, [
            info.get('category') or '',
            info.get('longName')  or '',
            info.get('shortName') or '',
        ])).lower()
        if any(x in hay for x in ('u.s.', ' us ', 's&p', 'sp 500', 'nasdaq',
                                   'russell', 'america', 'united states', 'domestic')):
            geo = 'United States'
        elif any(x in hay for x in ('emerging', 'em bond', 'em equity')):
            geo = 'Emerging Markets'
        elif any(x in hay for x in ('msci world', 'all-world', 'all world',
                                    'global', 'world', 'international',
                                    'developed markets')):
            geo = 'Global'
        elif any(x in hay for x in ('europe', 'european', 'eurozone', 'stoxx',
                                    'ftse 100', 'ftse 250')):
            geo = 'Europe'
        else:
            geo = None
        return {
            'long_name':      info.get('longName') or ticker,
            'category':       info.get('category') or 'ETF / Fund',
            'expense_ratio':  expense_pct,
            'total_assets':   _safe_f(info.get('totalAssets')),
            'sector_weights': sector_weights,
            'geo':            geo,
        }

    try:
        return cached_fetch(key, _TTL, _fetch)
    except Exception as e:
        log.warning('[market_intel] etf_brief %s: %s', ticker, e)
        return {}


def score_fundamentals(f: dict) -> dict:
    """
    Derive 6 signal labels + narrative text from a fundamentals dict.
    Pure function — no network calls. Returns {} if f is empty.
    """
    if not f:
        return {}

    _RED    = '#dc2626'
    _ORANGE = '#ea580c'
    _YELLOW = '#d97706'
    _GREEN  = '#16a34a'
    _GRAY   = '#9ca3af'
    _TEAL   = '#0891b2'

    pe          = f.get('fwd_pe')
    peg         = f.get('peg')
    rev         = f.get('rev_growth_pct')
    margin      = f.get('profit_margin')
    roe         = f.get('roe')
    fcf_yield   = f.get('fcf_yield')
    debt_ebitda = f.get('debt_ebitda')
    net_cash_pct = f.get('net_cash_pct')
    buy         = f.get('analyst_buy')
    hold_n      = f.get('analyst_hold')
    sell        = f.get('analyst_sell')
    short_pct   = f.get('short_pct')
    insider_net = f.get('insider_net')
    rsi         = f.get('rsi_14')
    above_50d   = f.get('above_50d_ma')

    # ── Valuation ─────────────────────────────────────────────────────────────
    val_label, val_color = 'Unknown', _GRAY
    if pe is not None or peg is not None:
        if (pe and pe > 40) or (peg and peg > 3):
            val_label, val_color = 'Very High', _RED
        elif (pe and pe > 25) or (peg and peg > 2):
            val_label, val_color = 'High', _ORANGE
        elif (pe is None or pe < 15) and (peg is None or peg < 1):
            val_label, val_color = 'Low', _GREEN
        else:
            val_label, val_color = 'Fair', _YELLOW

    # ── Growth ────────────────────────────────────────────────────────────────
    grw_label, grw_color = 'Unknown', _GRAY
    if rev is not None:
        if rev > 20:
            grw_label, grw_color = 'Very Strong', _GREEN
        elif rev > 10:
            grw_label, grw_color = 'Strong', _GREEN
        elif rev > 5:
            grw_label, grw_color = 'Moderate', _YELLOW
        elif rev >= 0:
            grw_label, grw_color = 'Weak', _ORANGE
        else:
            grw_label, grw_color = 'Declining', _RED

    # ── Profitability ─────────────────────────────────────────────────────────
    prf_label, prf_color = 'Unknown', _GRAY
    if margin is not None:
        if margin > 20:
            prf_label, prf_color = 'Strong', _GREEN
        elif margin > 10:
            prf_label, prf_color = 'Moderate', _YELLOW
        elif margin > 0:
            prf_label, prf_color = 'Weak', _ORANGE
        else:
            prf_label, prf_color = 'Losing', _RED
        if roe is not None and roe > 20 and prf_label in ('Moderate', 'Weak'):
            prf_label, prf_color = 'Strong', _GREEN

    # ── Financial Health ──────────────────────────────────────────────────────
    fin_label, fin_color = 'Unknown', _GRAY
    has_fin = any(v is not None for v in [fcf_yield, debt_ebitda, net_cash_pct])
    if has_fin:
        net_cash_pos = net_cash_pct is not None and net_cash_pct > 0
        low_debt     = debt_ebitda is not None and debt_ebitda < 2
        pos_fcf      = fcf_yield   is not None and fcf_yield > 3
        high_debt    = debt_ebitda is not None and debt_ebitda > 5
        neg_fcf      = fcf_yield   is not None and fcf_yield < 0
        if net_cash_pos:                    # net cash on balance sheet trumps everything
            fin_label, fin_color = 'Strong', _GREEN
        elif pos_fcf and low_debt:          # generates cash AND carries little debt
            fin_label, fin_color = 'Strong', _GREEN
        elif neg_fcf or high_debt:          # burning cash OR heavily leveraged
            fin_label, fin_color = 'At Risk', _RED
        else:
            fin_label, fin_color = 'Moderate', _YELLOW

    # ── Sentiment — composite: analyst bias + insider activity + short interest ─
    # Analysts alone are lagging. Insider buying is the strongest signal; high
    # short interest is a warning even when Wall Street is bullish.
    snt_label, snt_color = 'Unknown', _GRAY
    if all(v is not None for v in [buy, hold_n, sell]):
        total = (buy or 0) + (hold_n or 0) + (sell or 0)
        if total > 0:
            buy_pct = (buy or 0) / total * 100
            score   = 2 if buy_pct > 60 else (1 if buy_pct > 40 else -1)
            if insider_net == 'Net+':   score += 1   # insiders buying: strong positive
            elif insider_net == 'Net−': score -= 1  # insiders selling: negative
            if short_pct is not None and short_pct > 10:
                score -= 1               # >10% short float = known bearish thesis
            if score >= 3:
                snt_label, snt_color = 'Very Bullish', _GREEN
            elif score >= 2:
                snt_label, snt_color = 'Bullish', _GREEN
            elif score >= 1:
                snt_label, snt_color = 'Neutral', _YELLOW
            elif score == 0:
                snt_label, snt_color = 'Cautious', _ORANGE
            else:
                snt_label, snt_color = 'Bearish', _RED
    elif insider_net == 'Net+':
        snt_label, snt_color = 'Insider Buying', _GREEN
    elif insider_net == 'Net−':
        snt_label, snt_color = 'Insider Selling', _RED

    # ── Momentum ──────────────────────────────────────────────────────────────
    mom_label, mom_color = 'Unknown', _GRAY
    if rsi is not None or above_50d is not None:
        overbought  = rsi is not None and rsi > 70
        oversold    = rsi is not None and rsi < 30
        neutral_rsi = rsi is not None and 40 <= rsi <= 70
        above       = above_50d is True
        below       = above_50d is False
        if overbought:
            mom_label, mom_color = 'Extended', _ORANGE      # run hard — watch for reversal
        elif oversold:
            mom_label, mom_color = 'Oversold', _TEAL        # potential bounce zone
        elif above and neutral_rsi:
            mom_label, mom_color = 'Uptrend', _GREEN        # trend intact, not stretched
        elif below and (rsi is None or rsi < 45):
            mom_label, mom_color = 'Losing Momentum', _RED  # trend broken, weak RSI
        else:
            mom_label, mom_color = 'Mixed', _YELLOW         # conflicting signals

    # ── Overall text ──────────────────────────────────────────────────────────
    _OVERALL_MAP = {
        ('Very High', 'Very Strong'): ("High-conviction growth stock — priced for perfection",
                                       "Strong company, but any earnings miss will reprice it sharply"),
        ('Very High', 'Strong'):      ("Expensive premium for solid — not exceptional — growth",
                                       "Poor risk/reward unless the growth rate accelerates"),
        ('Very High', 'Moderate'):    ("Overpriced for the growth on offer — limited upside",
                                       "High risk of disappointment at current valuation"),
        ('Very High', 'Weak'):        ("Very expensive, growth falling short — unfavorable setup",
                                       "Price implies a turnaround the numbers don't yet support"),
        ('Very High', 'Declining'):   ("Danger zone — premium price, shrinking revenue",
                                       "Unless a major catalyst exists, risk outweighs reward"),
        ('High', 'Very Strong'):      ("Growth justifies a premium — but execution must continue",
                                       "Attractive if momentum holds; vulnerable if growth slows"),
        ('High', 'Strong'):           ("Paying up for quality — reasonable if growth persists",
                                       "Balanced risk/reward, but limited downside protection"),
        ('High', 'Moderate'):         ("Priced for more growth than it is currently delivering",
                                       "Needs to accelerate to justify the valuation"),
        ('High', 'Weak'):             ("Expensive with weak growth — unfavorable setup",
                                       "Market may be mispricing the risk here"),
        ('High', 'Declining'):        ("Expensive while the business contracts — high risk",
                                       "Elevated downside if decline continues"),
        ('Fair', 'Very Strong'):      ("Strong growth at a fair price — favorable setup",
                                       "Potentially undervalued; reward outweighs risk here"),
        ('Fair', 'Strong'):           ("Solid risk/reward — growing without overpaying",
                                       "A balanced position; no urgent reason to add or exit"),
        ('Fair', 'Moderate'):         ("Steady business, fair price — no strong edge either way",
                                       "Hold if you own it; no compelling entry or exit signal"),
        ('Fair', 'Weak'):             ("Fair price, but growth needs to recover to matter",
                                       "Monitor for improvement before adding exposure"),
        ('Fair', 'Declining'):        ("Reasonable price, but the business is heading the wrong way",
                                       "Watch for stabilization before drawing conclusions"),
        ('Low', 'Very Strong'):       ("Overlooked opportunity — fast growth at a low price",
                                       "Strong risk/reward if the growth rate is sustainable"),
        ('Low', 'Strong'):            ("Attractively priced with solid fundamentals",
                                       "Favorable entry — reward outweighs risk if growth holds"),
        ('Low', 'Moderate'):          ("Cheap, but verify the low price is not deserved",
                                       "Value play or value trap — the business must stabilize"),
        ('Low', 'Weak'):              ("Cheap for a reason — do not mistake price for value",
                                       "Investigate why it is this cheap before acting"),
        ('Low', 'Declining'):         ("Deep discount, but the business is shrinking",
                                       "Only for distressed-value specialists — high risk"),
    }
    key = (val_label, grw_label)
    if key in _OVERALL_MAP:
        overall, overall_sub = _OVERALL_MAP[key]
    elif val_label != 'Unknown' and grw_label == 'Unknown':
        overall = f"{val_label} valuation"
        overall_sub = "Growth data unavailable"
    elif grw_label != 'Unknown' and val_label == 'Unknown':
        overall = f"{grw_label} growth"
        overall_sub = "Valuation data unavailable"
    else:
        overall = "Insufficient data for full assessment"
        overall_sub = "Some metrics could not be retrieved"

    # ── Explanation sentences ─────────────────────────────────────────────────
    sentences = []
    if rev is not None:
        if rev > 20:
            sentences.append(f"This company is growing very fast (revenue {rev:+.1f}%/yr).")
        elif rev > 10:
            sentences.append(f"Revenue is growing solidly at {rev:+.1f}%/yr.")
        elif rev >= 0:
            sentences.append(f"Revenue growth is modest at {rev:+.1f}%/yr.")
        else:
            sentences.append(f"Revenue is shrinking ({rev:+.1f}%/yr).")
    if val_label not in ('Unknown',) and grw_label not in ('Unknown',):
        if val_label == 'Very High' and grw_label in ('Very Strong', 'Strong'):
            sentences.append("The stock is expensive — the market expects continued outperformance, and any earnings miss will be punished hard.")
        elif val_label == 'Very High':
            sentences.append("The stock is very expensive despite limited growth — the price implies a turnaround not yet visible in the numbers.")
        elif val_label == 'High' and grw_label in ('Very Strong', 'Strong'):
            sentences.append("The premium is supported by strong growth — but any slowdown would quickly erode the case for holding at this price.")
        elif val_label == 'High':
            sentences.append("The stock carries a premium the current growth rate does not fully support.")
        elif val_label == 'Low' and grw_label in ('Very Strong', 'Strong'):
            sentences.append("The stock is cheap relative to its growth — if that growth is real, this looks mispriced to the upside.")
        elif val_label == 'Low' and grw_label in ('Declining', 'Weak'):
            sentences.append("The low price likely reflects real business weakness — cheap is not the same as good value.")
        elif val_label == 'Fair':
            sentences.append("The stock is reasonably priced — no valuation alarm in either direction.")
    if fin_label == 'Strong':
        sentences.append("Financially strong — generates real cash with manageable debt.")
    elif fin_label == 'Moderate':
        sentences.append("Financial health is moderate — manageable debt but watch cash flow.")
    elif fin_label == 'At Risk':
        sentences.append("Financial health is a concern — elevated debt or negative cash flow.")

    # ── Watch items — driven by the specific metrics that triggered signals ────
    watch = []
    if val_label in ('Very High', 'High'):
        watch.append("Earnings vs expectations — a miss at this valuation will reprice the stock sharply")
    if rev is not None and 0 < rev < 8:
        watch.append("Revenue growth — it is thin and needs to hold or accelerate")
    elif rev is not None and rev < 0:
        watch.append("Whether the revenue decline is stabilizing or still deepening")
    if margin is not None and margin < 10:
        watch.append("Margin trend — profitability is below healthy levels and needs to improve")
    if fin_label == 'At Risk':
        watch.append("Liquidity runway and debt refinancing terms")
    if short_pct is not None and short_pct > 10:
        watch.append(f"High short interest ({short_pct:.1f}%) — a strong bearish thesis is already in the market")
    if mom_label == 'Extended':
        watch.append("Signs of momentum fading after an extended run")
    if not watch:
        watch = ["Quarterly earnings results", "Analyst estimate revisions"]
    watch = watch[:3]

    # ── Upside / Downside ─────────────────────────────────────────────────────
    if grw_label in ('Very Strong', 'Strong'):
        upside = "Growth continues above expectations → stock rises further"
    elif fin_label == 'Strong' and val_label == 'Low':
        upside = "Market re-rates the stock as value is recognized"
    elif prf_label in ('Weak', 'Losing'):
        upside = "Margins improve as the business scales"
    else:
        upside = "Steady performance sustains the current valuation"

    if val_label in ('Very High', 'High') and grw_label in ('Very Strong', 'Strong'):
        downside = "Growth slows or disappoints → stock drops sharply"
    elif fin_label == 'At Risk':
        downside = "Debt burden or cash burn forces dilutive financing"
    elif grw_label in ('Declining', 'Weak'):
        downside = "Continued revenue weakness leads to multiple compression"
    else:
        downside = "Macro headwinds or sector rotation compress the multiple"

    # ── One-line takeaway ─────────────────────────────────────────────────────
    _QUALITY_MAP = {
        ('Strong',   'Strong'):   "High-quality",
        ('Strong',   'Moderate'): "Solid",
        ('Strong',   'Weak'):     "Profitable but financially stretched",
        ('Moderate', 'Strong'):   "Decent quality",
        ('Moderate', 'Moderate'): "Steady",
        ('Weak',     'Strong'):   "Unprofitable but well-funded",
        ('Losing',   'At Risk'):  "High-risk",
    }
    _GROWTH_DESC = {
        'Very Strong': "very strong growth",
        'Strong':      "solid growth",
        'Moderate':    "moderate growth",
        'Weak':        "weak growth",
        'Declining':   "declining revenue",
        'Unknown':     "unconfirmed growth",
    }
    _RISK_MAP = {
        ('Very High', 'Very Strong'): "the price already assumes continued outperformance",
        ('Very High', 'Strong'):      "the premium leaves little room for error",
        ('High',      'Very Strong'): "any slowdown could cause sharp re-pricing",
        ('High',      'Moderate'):    "the valuation assumes improvement not yet delivered",
        ('Low',       'Declining'):   "the cheap price may signal deeper problems",
        ('Low',       'Weak'):        "cheap for a reason — verify the thesis before buying",
    }
    quality     = _QUALITY_MAP.get((prf_label, fin_label), "")
    growth_desc = _GROWTH_DESC.get(grw_label, "")
    risk        = _RISK_MAP.get((val_label, grw_label), "monitor for changes in the core drivers")
    if quality and growth_desc:
        takeaway = f"{quality} company with {growth_desc}, but {risk}."
    elif growth_desc:
        takeaway = f"Company with {growth_desc} — {risk}."
    else:
        takeaway = "Insufficient data for a definitive assessment."

    return {
        'signals': {
            'valuation':     {'label': val_label,  'color': val_color},
            'growth':        {'label': grw_label,  'color': grw_color},
            'profitability': {'label': prf_label,  'color': prf_color},
            'fin_health':    {'label': fin_label,  'color': fin_color},
            'sentiment':     {'label': snt_label,  'color': snt_color},
            'momentum':      {'label': mom_label,  'color': mom_color},
        },
        'overall':     overall,
        'overall_sub': overall_sub,
        'explanation': sentences,
        'watch':       watch,
        'upside':      upside,
        'downside':    downside,
        'takeaway':    takeaway,
    }

