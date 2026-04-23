"""
Macro market valuation indicators.

Three metrics that give a high-level read on whether the overall market is
cheap or expensive — independent of your specific portfolio:

  Buffett Indicator  — US Total Market Cap / US GDP × 100
  S&P 500 P/E        — Trailing & forward price-to-earnings (yfinance)
  Shiller CAPE       — Cyclically Adjusted P/E (10-year real earnings avg)
                       Source: Robert Shiller / Yale (ie_data.xls)

All functions are cached 4 hours so repeated Dash renders never hit the
network. Each returns None on failure so the UI can degrade gracefully.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

import pandas as pd

from cache_util import cached_fetch

log = logging.getLogger(__name__)

_TTL = 3600 * 4   # 4 hours; backed by cache_util (diskcache, persists across restarts)


# ── Zone classifiers ───────────────────────────────────────────────────────────

def buffett_zone(v: float) -> tuple[str, str]:
    """(label, hex-colour) for a given Buffett Indicator value (%).

    Thresholds anchored to the modern structural baseline (~125-130%),
    not the pre-tech-era 100%.  The US economy shifted from capital-heavy
    industry to high-margin software, so market cap naturally runs higher
    relative to GDP today.
    """
    if v < 75:   return ('Well Below Historic Norms',     '#16a34a')
    if v < 110:  return ('Fairly Valued',                 '#22c55e')
    if v < 150:  return ('Elevated — Within Modern Range', '#84cc16')
    if v < 190:  return ('Running Hot',                   '#f97316')
    return              ('Historically Stretched',        '#dc2626')


def pe_zone(v: float) -> tuple[str, str]:
    """(label, hex-colour) for a trailing P/E value."""
    if v < 15:   return ('Cheap',                 '#16a34a')
    if v < 20:   return ('Fairly Valued',          '#22c55e')
    if v < 25:   return ('Expensive',              '#eab308')
    if v < 30:   return ('Very Expensive',         '#f97316')
    return              ('Extremely Expensive',    '#dc2626')


def cape_zone(v: float) -> tuple[str, str]:
    """(label, hex-colour) for a Shiller CAPE value."""
    if v < 15:   return ('Undervalued',            '#16a34a')
    if v < 20:   return ('Fairly Valued',          '#22c55e')
    if v < 25:   return ('Overvalued',             '#eab308')
    if v < 30:   return ('Highly Overvalued',      '#f97316')
    return              ('Extremely Overvalued',   '#dc2626')


def treasury_zone(v: float) -> tuple[str, str]:
    """
    (label, hex-colour) for the 10-year Treasury yield (%).

    Low yield  → bonds pay little → stocks face less competition (green).
    High yield → bonds pay well   → stocks must justify higher valuations (red).
    """
    if v < 2:    return ('Very Low — Stocks Favoured',   '#16a34a')
    if v < 3:    return ('Low — Stocks Competitive',     '#22c55e')
    if v < 4:    return ('Moderate',                     '#eab308')
    if v < 5:    return ('Elevated — Bonds Competitive', '#f97316')
    return              ('High — Bonds Attractive',      '#dc2626')


# ── Buffett Indicator ──────────────────────────────────────────────────────────

def _fred_date_to_quarter(date_str: str) -> str:
    """Convert a FRED quarterly date string to a readable quarter label.
    '2025-10-01' -> 'Q4 2025',  '2025-07-01' -> 'Q3 2025', etc.
    """
    try:
        month = int(date_str[5:7])
        year  = date_str[:4]
        return f'Q{(month - 1) // 3 + 1} {year}'
    except Exception:
        return date_str[:7]


def get_buffett_indicator() -> dict | None:
    """
    Total US equity market cap (Wilshire 5000 index ≈ market cap in $B)
    divided by US nominal GDP × 100.

    GDP source: FRED (St. Louis Fed) quarterly series — updated within weeks
    of each BEA release, far more current than World Bank annual data.
    Fallback: World Bank annual data (1-2 years stale but always available).

    Returns
    -------
    {value, market_cap_t, gdp_t, gdp_quarter, gdp_source}  or None on failure.
    """
    _UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'

    def fetch():
        import yfinance as yf
        import io

        # Wilshire 5000 Full Cap index — index level ≈ US total equity market
        # cap in billions of USD (Wilshire Associates convention).
        hist = yf.Ticker('^W5000').history(period='5d')
        if hist.empty:
            raise RuntimeError('Empty Wilshire 5000 history')
        market_cap_b = float(hist['Close'].dropna().iloc[-1])

        import datetime
        gdp_b, gdp_quarter, gdp_source = None, None, None
        now          = datetime.datetime.now()
        current_year = now.year
        current_q    = (now.month - 1) // 3 + 1

        def _quarters_since(data_year: int, data_q: int) -> int:
            return (current_year - data_year) * 4 + (current_q - data_q)

        def _forward_extrapolate(gdp_val: float, data_year: int, data_q: int,
                                 quarterly_rate: float = 0.0068) -> tuple:
            """Project gdp_val forward to the current quarter.
            quarterly_rate=0.0068 ≈ 2.8% annualised nominal growth.
            Returns (projected_gdp, label).  Label is '(est.)' when projected.
            """
            n = _quarters_since(data_year, data_q)
            if n <= 0:
                return gdp_val, f'Q{data_q} {data_year}'
            projected = gdp_val * (1 + quarterly_rate) ** n
            return projected, f'Q{current_q} {current_year} (est.)'

        # ── Primary: FRED GDP series (two URL formats) ────────────────────────
        for fred_url in [
            'https://fred.stlouisfed.org/graph/fredgraph.csv?id=GDP',
            'https://fred.stlouisfed.org/data/GDP.txt',
        ]:
            try:
                req = urllib.request.Request(fred_url, headers={'User-Agent': _UA})
                # Short timeout — FRED occasionally stalls and we'd rather fall
                # back to World Bank than block the Market Valuation panel.
                with urllib.request.urlopen(req, timeout=5) as resp:
                    raw = resp.read().decode('utf-8')
                if raw.lstrip().startswith('<'):
                    raise ValueError('FRED returned HTML')
                df = pd.read_csv(io.StringIO(raw), names=['date', 'gdp'], skiprows=1)
                df = df[df['gdp'] != '.'].copy()
                df['gdp'] = pd.to_numeric(df['gdp'], errors='coerce')
                df = df.dropna(subset=['gdp'])
                if df.empty:
                    raise ValueError('No numeric rows in FRED response')
                latest_date = str(df.iloc[-1]['date'])   # e.g. "2025-10-01"
                raw_gdp     = float(df.iloc[-1]['gdp'])
                data_year   = int(latest_date[:4])
                data_q      = (int(latest_date[5:7]) - 1) // 3 + 1
                # Use actual trailing growth rate from FRED data if available
                if len(df) >= 4:
                    quarterly_rate = (raw_gdp / float(df.iloc[-4]['gdp'])) ** 0.25 - 1
                else:
                    quarterly_rate = 0.0068
                gdp_b, gdp_quarter = _forward_extrapolate(
                    raw_gdp, data_year, data_q, quarterly_rate)
                gdp_source = 'FRED'
                log.debug('Buffett GDP from FRED: raw %s $%.1fT → %s $%.1fT',
                          _fred_date_to_quarter(latest_date), raw_gdp / 1000,
                          gdp_quarter, gdp_b / 1000)
                break
            except Exception as e:
                log.warning('FRED GDP attempt failed (%s): %s', fred_url, e)

        # ── Fallback: World Bank annual nominal GDP (current USD) ─────────────
        if gdp_b is None:
            try:
                url = (
                    'https://api.worldbank.org/v2/country/US/indicator/NY.GDP.MKTP.CD'
                    '?format=json&mrv=3&per_page=3'
                )
                req = urllib.request.Request(url, headers={'User-Agent': _UA})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
                for record in (data[1] or []):
                    if record.get('value') is not None:
                        raw_gdp   = float(record['value']) / 1e9
                        data_year = int(record.get('date', current_year))
                        # World Bank gives annual data; treat as Q4 of that year
                        gdp_b, gdp_quarter = _forward_extrapolate(
                            raw_gdp, data_year, 4)
                        gdp_source = 'World Bank'
                        log.debug('Buffett GDP from World Bank: %d $%.1fT → %s $%.1fT',
                                  data_year, raw_gdp / 1000, gdp_quarter, gdp_b / 1000)
                        break
            except Exception as e:
                log.warning('World Bank GDP fetch also failed: %s', e)

        if gdp_b is None:
            raise RuntimeError('Could not fetch GDP from FRED or World Bank')

        ratio = (market_cap_b / gdp_b) * 100

        return {
            'value':        round(ratio, 1),
            'market_cap_t': round(market_cap_b / 1_000, 1),
            'gdp_t':        round(gdp_b        / 1_000, 1),
            'gdp_quarter':  gdp_quarter,
            'gdp_source':   gdp_source,
        }

    try:
        return cached_fetch('buffett', _TTL, fetch)
    except Exception as e:
        log.warning('Buffett indicator fetch failed: %s', e)
        return None


# ── S&P 500 P/E ratio ──────────────────────────────────────────────────────────

def get_sp500_pe() -> dict | None:
    """
    Trailing P/E for the S&P 500.

    Primary:  multpl.com monthly table (most reliable, no API key needed).
    Fallback: yfinance SPY ETF (^GSPC does not expose PE via yfinance).

    Returns
    -------
    {trailing_pe, forward_pe, price}  or None on failure.
    """
    def fetch():
        import re
        # ── Primary: multpl.com ───────────────────────────────────────────────
        try:
            url = 'https://www.multpl.com/s-p-500-pe-ratio/table/by-month'
            req = urllib.request.Request(
                url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode('utf-8', errors='replace')
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
            values = []
            for row in rows:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                if len(cells) >= 2:
                    val_text = re.sub(r'<[^>]+>', '', cells[1]).strip()
                    try:
                        values.append(float(val_text))
                    except ValueError:
                        pass
            if values:
                trailing = round(values[0], 1)
                # Also grab ^GSPC price from yfinance for display
                price = None
                try:
                    import yfinance as yf
                    hist = yf.Ticker('^GSPC').history(period='2d')
                    if not hist.empty:
                        price = round(float(hist['Close'].dropna().iloc[-1]), 2)
                except Exception:
                    pass
                return {'trailing_pe': trailing, 'forward_pe': None, 'price': price}
        except Exception as e:
            log.debug('multpl P/E fetch failed, trying yfinance: %s', e)

        # ── Fallback: yfinance SPY (ETF exposes PE, ^GSPC does not) ──────────
        import yfinance as yf
        info     = yf.Ticker('SPY').info
        trailing = info.get('trailingPE')
        forward  = info.get('forwardPE')
        price    = None
        try:
            hist  = yf.Ticker('^GSPC').history(period='2d')
            price = round(float(hist['Close'].dropna().iloc[-1]), 2)
        except Exception:
            pass
        if trailing is None and forward is None:
            raise RuntimeError('No P/E data from multpl or yfinance')
        return {
            'trailing_pe': round(float(trailing), 1) if trailing else None,
            'forward_pe':  round(float(forward),  1) if forward  else None,
            'price':       price,
        }

    try:
        return cached_fetch('sp500_pe', _TTL, fetch)
    except Exception as e:
        log.warning('S&P 500 P/E fetch failed: %s', e)
        return None


# ── Shiller CAPE ───────────────────────────────────────────────────────────────

def get_shiller_cape() -> dict | None:
    """
    Cyclically Adjusted P/E (Shiller CAPE / P/E10).

    Source: multpl.com monthly table — more reliably accessible than Yale's
    ie_data.xls which is often slow or unreachable.

    Returns
    -------
    {value, hist_mean, hist_median, last_date, dates:[str], values:[float]}
    or None on failure.
    """
    def fetch():
        import re
        url = 'https://www.multpl.com/shiller-pe/table/by-month'
        req = urllib.request.Request(
            url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='replace')

        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
        dates, values = [], []
        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(cells) < 2:
                continue
            date_text = re.sub(r'<[^>]+>', '', cells[0]).strip()
            val_text  = re.sub(r'[<][^>]+[>]|\xa0|&#x2002;', '', cells[1]).strip()
            try:
                # Parse "Apr 1, 2026" → "2026-04"
                dt = pd.to_datetime(date_text, errors='coerce')
                if pd.isna(dt):
                    continue
                dates.append(dt.strftime('%Y-%m'))
                values.append(float(val_text))
            except (ValueError, TypeError):
                continue

        if not values:
            raise RuntimeError('Could not parse CAPE table from multpl.com')

        # multpl returns newest-first — reverse to oldest-first for stats/chart
        dates  = dates[::-1]
        values = values[::-1]

        series  = pd.Series(values, dtype=float)
        current = values[-1]
        last_date = dates[-1]

        # Last ~50 years for chart (≈ 600 monthly rows)
        chart_dates  = dates[-600:]
        chart_values = values[-600:]

        return {
            'value':       round(current,           1),
            'hist_mean':   round(float(series.mean()),   1),
            'hist_median': round(float(series.median()), 1),
            'last_date':   last_date,
            'dates':       chart_dates,
            'values':      [round(v, 1) for v in chart_values],
        }

    try:
        return cached_fetch('shiller_cape', _TTL, fetch)
    except Exception as e:
        log.warning('Shiller CAPE fetch failed: %s', e)
        return None


# ── 10-Year Treasury Yield ─────────────────────────────────────────────────────

def get_treasury_yield() -> dict | None:
    """
    Current US 10-year Treasury yield (^TNX via yfinance).

    Returns
    -------
    {value}  e.g. {'value': 4.35}  or None on failure.
    """
    def fetch():
        import yfinance as yf
        hist = yf.Ticker('^TNX').history(period='5d')
        if hist.empty:
            raise RuntimeError('Empty ^TNX history')
        value = round(float(hist['Close'].dropna().iloc[-1]), 2)
        return {'value': value}

    try:
        return cached_fetch('treasury_yield', _TTL, fetch)
    except Exception as e:
        log.warning('Treasury yield fetch failed: %s', e)
        return None
