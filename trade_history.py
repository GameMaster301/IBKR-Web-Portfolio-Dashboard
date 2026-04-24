"""
Historical trade ingestion.

IBKR's live `reqExecutions` API only returns about 7 days of fills, so this
module lets the user upload longer-range trade history as CSV exported from
the IBKR Client Portal → Performance & Reports → Transaction History.

Parsed trades are normalized into the same dict shape as live trades and
persisted to `data/uploaded_trades.json` so they survive restarts.
"""

from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime
from io import StringIO

log = logging.getLogger(__name__)

_DATA_DIR   = os.environ.get('IBKRDASH_DATA_DIR') or os.path.join(os.getcwd(), 'data')
_STORE_PATH = os.path.join(_DATA_DIR, 'uploaded_trades.json')


def _parse_dt(raw):
    """Parse the various datetime formats IBKR emits → ISO 8601, or None."""
    if not raw:
        return None
    s = str(raw).strip().strip('"')
    for fmt in (
        '%Y%m%d;%H%M%S',       # Flex "20240115;093012"
        '%Y-%m-%d;%H:%M:%S',
        '%Y-%m-%d, %H:%M:%S',  # CSV "2024-01-15, 09:30:00"
        '%Y-%m-%d %H:%M:%S',
        '%Y%m%d',
        '%Y-%m-%d',
    ):
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    return None


def _safe_float(v, default=0.0):
    try:
        return float(str(v).replace(',', ''))
    except (TypeError, ValueError):
        return default


def parse_activity_csv(content: bytes) -> list:
    """
    Parse IBKR Activity Statement / Trade Confirmation CSV into normalized
    trade dicts.  The CSV is multi-section (each section has its own header
    row starting with 'Header'); we only extract rows where the first column
    is 'Trades' and the DataDiscriminator is 'Order' or 'Trade'.
    """
    try:
        text = content.decode('utf-8-sig', errors='replace')
    except Exception as e:
        log.warning('CSV decode error: %s', e)
        return []

    trades = []
    reader = csv.reader(StringIO(text))
    header: list | None = None

    for row in reader:
        if not row or row[0] != 'Trades':
            continue
        if len(row) >= 2 and row[1] == 'Header':
            header = row
            continue
        if len(row) < 2 or row[1] != 'Data' or not header:
            continue

        d = dict(zip(header, row, strict=False))
        disc = (d.get('DataDiscriminator') or '').strip()
        if disc and disc not in ('Order', 'Trade'):
            continue   # skip SubTotal / Total rows

        symbol = (d.get('Symbol') or '').strip()
        qty    = _safe_float(d.get('Quantity'))
        price  = _safe_float(d.get('T. Price') or d.get('Trade Price'))
        dt     = _parse_dt(d.get('Date/Time'))
        if not (symbol and qty and price and dt):
            continue
        side = 'BUY' if qty > 0 else 'SELL'
        trades.append({
            'ticker': symbol,
            'side':   side,
            'shares': abs(qty),
            'price':  round(price, 4),
            'time':   dt,
            'value':  round(abs(qty) * price, 2),
            'source': 'csv',
        })
    return trades


def _trade_key(t):
    return (
        t.get('ticker'),
        t.get('side'),
        round(_safe_float(t.get('shares')), 4),
        round(_safe_float(t.get('price')), 4),
        t.get('time'),
    )


def load_uploaded_trades() -> list:
    if not os.path.exists(_STORE_PATH):
        return []
    try:
        with open(_STORE_PATH, encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        log.warning('Failed to load uploaded trades: %s', e)
        return []


def save_uploaded_trades(new_trades: list) -> list:
    """Merge `new_trades` into the persisted list, dedupe, and return the full list."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    existing = load_uploaded_trades()
    seen = {_trade_key(t) for t in existing}
    for t in new_trades:
        k = _trade_key(t)
        if k not in seen:
            existing.append(t)
            seen.add(k)
    existing.sort(key=lambda x: x.get('time') or '')
    with open(_STORE_PATH, 'w', encoding='utf-8') as f:
        json.dump(existing, f, indent=2, default=str)
    return existing


def clear_uploaded_trades() -> list:
    try:
        if os.path.exists(_STORE_PATH):
            os.remove(_STORE_PATH)
    except Exception as e:
        log.warning('Failed to clear uploaded trades: %s', e)
    return []
