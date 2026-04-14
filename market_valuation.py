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

import json
import logging
import time
import urllib.request
import urllib.error

import pandas as pd

log = logging.getLogger(__name__)

_CACHE: dict = {}
_TTL = 3600 * 4   # 4 hours


def _cached(key, fn):
    now = time.time()
    if key in _CACHE:
        ts, val = _CACHE[key]
        if now - ts < _TTL:
            return val
    val = fn()
    _CACHE[key] = (now, val)
    return val


# ── Zone classifiers ───────────────────────────────────────────────────────────

def buffett_zone(v: float) -> tuple[str, str]:
    """(label, hex-colour) for a given Buffett Indicator value (%)."""
    if v < 75:   return ('Undervalued',          '#16a34a')
    if v < 100:  return ('Fairly Valued',         '#22c55e')
    if v < 120:  return ('Modestly Overvalued',   '#eab308')
    if v < 150:  return ('Overvalued',            '#f97316')
    return              ('Strongly Overvalued',   '#dc2626')


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


# ── Buffett Indicator ──────────────────────────────────────────────────────────

def get_buffett_indicator() -> dict | None:
    """
    Total US equity market cap (Wilshire 5000 index ≈ market cap in $B)
    divided by US nominal GDP (World Bank, latest available year) × 100.

    Returns
    -------
    {value, market_cap_t, gdp_t, gdp_year}  or None on failure.
    """
    def fetch():
        import yfinance as yf

        # Wilshire 5000 Full Cap index — designed so index level ≈ US total
        # equity market cap in billions of USD (Wilshire Associates convention).
        hist = yf.Ticker('^W5000').history(period='5d')
        if hist.empty:
            raise RuntimeError('Empty Wilshire 5000 history')
        market_cap_b = float(hist['Close'].dropna().iloc[-1])

        # US nominal GDP from World Bank (free, no API key required).
        # mrv=3 asks for the 3 most recent annual records; we take the first
        # non-null one (usually 1-2 years behind real-time).
        url = (
            'https://api.worldbank.org/v2/country/US/indicator/NY.GDP.MKTP.CD'
            '?format=json&mrv=3&per_page=3'
        )
        req = urllib.request.Request(url, headers={'User-Agent': 'ibkrdash/1.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        gdp_usd, gdp_year = None, None
        for record in (data[1] or []):
            if record.get('value') is not None:
                gdp_usd  = float(record['value'])
                gdp_year = record.get('date', '')
                break

        if gdp_usd is None:
            raise RuntimeError('World Bank returned no GDP value')

        gdp_b = gdp_usd / 1e9   # current USD → billions
        ratio = (market_cap_b / gdp_b) * 100

        return {
            'value':        round(ratio, 1),
            'market_cap_t': round(market_cap_b / 1_000, 1),   # $T
            'gdp_t':        round(gdp_b        / 1_000, 1),   # $T
            'gdp_year':     gdp_year,
        }

    try:
        return _cached('buffett', fetch)
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
        return _cached('sp500_pe', fetch)
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
        return _cached('shiller_cape', fetch)
    except Exception as e:
        log.warning('Shiller CAPE fetch failed: %s', e)
        return None
