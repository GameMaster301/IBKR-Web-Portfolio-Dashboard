"""
Demo-mode payload.

Produces a deterministic mock portfolio in the exact shape returned by
`ibkr_client.fetch_all_data()` so every downstream callback (positions
table, detail panel, donut, dividends, market intel, valuation, coach)
works against it unchanged.

Tickers are real symbols so the yfinance-backed sector/geo/earnings
lookups in `market_intel.py` produce meaningful data.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from config import cfg

# (ticker, conId, qty, avg_cost, current_price, market_value_eur, unrealized_pnl_eur)
_POSITIONS = [
    ('AAPL',    265598,    60,  175.00, 195.50, 10_861.00,  1_139.00),
    ('MSFT',    272093,    40,  380.00, 420.75, 15_583.00,  1_509.00),
    ('NVDA',   4815747,    25,  450.00, 680.00, 15_740.00,  5_324.00),
    ('ASML',  117589399,   15,  720.00, 680.00, 10_200.00,   -600.00),
    ('TSLA',   76792991,   35,  250.00, 220.50,  7_146.00,   -956.00),
    ('VWCE',  349375628,  250,  105.00, 115.40, 28_850.00,  2_600.00),
    ('KO',        8894,   120,   58.00,  62.50,  6_944.00,    500.00),
    ('JNJ',      17900,    50,  155.00, 160.25,  7_419.00,    243.00),
]

# Per-ticker market-data overlays (bid/ask/open/high/low/prev_close/volume/52w/vwap).
# Prev-close is set to (current_price - daily_change) so the holdings table
# shows a realistic daily move.
_MARKET_DATA = {
    'AAPL': dict(bid=195.45, ask=195.55, open=194.20, high=196.10, low=193.80, prev_close=193.90, volume=48_230_000, low_52w=164.08, high_52w=237.23, vwap=195.10),
    'MSFT': dict(bid=420.50, ask=421.00, open=418.60, high=422.30, low=417.90, prev_close=418.20, volume=17_450_000, low_52w=309.45, high_52w=468.35, vwap=420.60),
    'NVDA': dict(bid=679.50, ask=680.50, open=670.00, high=685.00, low=668.50, prev_close=672.10, volume=41_800_000, low_52w=250.13, high_52w=750.00, vwap=678.20),
    'ASML': dict(bid=679.80, ask=680.40, open=682.00, high=684.50, low=676.30, prev_close=683.00, volume=1_120_000, low_52w=568.80, high_52w=1024.00, vwap=680.10),
    'TSLA': dict(bid=220.30, ask=220.70, open=224.00, high=225.10, low=218.70, prev_close=223.80, volume=74_600_000, low_52w=138.80, high_52w=299.29, vwap=220.90),
    'VWCE': dict(bid=115.30, ask=115.50, open=114.80, high=115.60, low=114.70, prev_close=114.90, volume=385_000, low_52w=94.20, high_52w=117.80, vwap=115.20),
    'KO':   dict(bid=62.45,  ask=62.55,  open=62.10,  high=62.80,  low=62.00,  prev_close=62.20,  volume=12_340_000, low_52w=51.55, high_52w=65.02, vwap=62.40),
    'JNJ':  dict(bid=160.15, ask=160.35, open=159.80, high=161.00, low=159.40, prev_close=159.70, volume=5_820_000,  low_52w=143.13, high_52w=175.97, vwap=160.20),
}

# IBKR-style dividend tick data for the three dividend payers.
# next_date / past_12m / next_12m / next_amount — mirrors tick-59 shape.
def _div_entries():
    today = datetime.now().date()
    def next_q(month_offset: int) -> str:
        # Next payment date roughly month_offset away, on the 15th.
        d = today.replace(day=15)
        month = d.month + month_offset
        year  = d.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        return d.replace(year=year, month=month).isoformat()
    return {
        'KO':   {'past_12m': 1.88, 'next_12m': 1.94, 'next_date': next_q(1), 'next_amount': 0.485},
        'JNJ':  {'past_12m': 4.76, 'next_12m': 4.96, 'next_date': next_q(2), 'next_amount': 1.24},
        'MSFT': {'past_12m': 3.00, 'next_12m': 3.32, 'next_date': next_q(2), 'next_amount': 0.83},
    }


def _trade_entries():
    """Six fake fills over the past 7 days, newest last (ibkr_client sorts ascending)."""
    now = datetime.now().replace(microsecond=0)
    fills = [
        # days_ago, ticker, side, shares, price
        (6, 'AAPL', 'BUY',  10, 194.80),
        (5, 'NVDA', 'BUY',   5, 672.40),
        (4, 'VWCE', 'BUY',  25, 114.90),
        (3, 'TSLA', 'SELL', 10, 222.10),
        (2, 'MSFT', 'BUY',   5, 418.50),
        (1, 'KO',   'BUY',  20,  62.20),
    ]
    out = []
    for days, ticker, side, shares, price in fills:
        t = now - timedelta(days=days, hours=2)
        out.append({
            'ticker': ticker,
            'side':   side,
            'shares': float(shares),
            'price':  round(price, 4),
            'time':   t.isoformat(),
            'value':  round(shares * price, 2),
        })
    return out


def build_demo_payload() -> dict:
    positions = []
    for ticker, conid, qty, avg, cur, mv, pnl in _POSITIONS:
        positions.append({
            'ticker':         ticker,
            'conId':          conid,
            'quantity':       float(qty),
            'avg_cost':       round(avg, 2),
            'current_price':  round(cur, 2),
            'market_value':   round(mv, 2),
            'unrealized_pnl': round(pnl, 2),
            'realized_pnl':   0.0,
            'price_stale':    False,
        })

    total_mv = sum(p['market_value'] for p in positions)
    cash_eur = 5_757.42
    net_liq  = round(total_mv + cash_eur, 2)

    account = {
        'cash_usd':             2_140.55,
        'cash_eur':             cash_eur,
        'buying_power':         round(net_liq * 1.9, 2),
        'net_liquidation':      net_liq,
        'available_funds':      round(net_liq * 0.82, 2),
        'excess_liquidity':     round(net_liq * 0.80, 2),
        'gross_position_value': round(total_mv, 2),
        'maint_margin':         round(total_mv * 0.25, 2),
        'init_margin':          round(total_mv * 0.30, 2),
        'cushion':              0.78,
        'leverage':             round(total_mv / net_liq, 3),
        'equity_with_loan':     net_liq,
        'sma':                  round(net_liq * 0.4, 2),
        'day_trades_remaining': 3.0,
        'eurusd_rate':          cfg['display']['eurusd_fallback'],
        'daily_pnl':            287.40,
    }

    return {
        'positions':   positions,
        'market_data': {k: dict(v) for k, v in _MARKET_DATA.items()},
        'div_data':    _div_entries(),
        'trades':      _trade_entries(),
        'account':     account,
    }
