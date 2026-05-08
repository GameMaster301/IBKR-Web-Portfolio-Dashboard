"""
Demo-mode payloads — three switchable sample portfolios.

Each portfolio produces a deterministic mock payload in the exact shape returned
by `ibkr_client.fetch_all_data()` so every downstream callback (positions table,
detail panel, donut, dividends, market intel, valuation, coach) works against
it unchanged.

Tickers are real symbols so the yfinance-backed sector/geo/earnings lookups in
`market_intel.py` produce meaningful data.

Three portfolios are intentionally diverse for showcase purposes:
  • balanced — diversified mix of US tech, EU semis, an EUR-denominated ETF and
    dividend stalwarts.  ~8 holdings, ~€103k.
  • income   — dividend-focused: REITs, consumer staples, telecom and a
    dividend ETF.  Lower volatility, fully populated dividend panel.
  • growth   — aggressive high-beta tech: AI semis, social, crypto-adjacent,
    speculative growth ETF.  Larger daily P&L swings, no dividends.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from config import cfg

# ── Portfolio registry — exposed to the UI for the switcher ─────────────────
DEMO_PORTFOLIOS: list[dict] = [
    {
        'id':          'balanced',
        'name':        'Balanced',
        'description': 'Diversified US tech, EU semis, ETFs and dividend stalwarts.',
    },
    {
        'id':          'income',
        'name':        'Income',
        'description': 'Dividend-focused — REITs, consumer staples, telecom and a dividend ETF.',
    },
    {
        'id':          'growth',
        'name':        'Growth',
        'description': 'Aggressive high-beta tech and crypto-adjacent names. No dividends.',
    },
]
DEFAULT_PORTFOLIO_ID = 'balanced'


# ── Per-portfolio raw data ──────────────────────────────────────────────────
# Tuple shape: (ticker, conId, qty, avg_cost, current_price, market_value_eur, unrealized_pnl_eur)
# Prices are in the stock's native currency; market_value/pnl are EUR
# (account base) — for USD names we use ~1.08 EUR/USD.

_BALANCED_POSITIONS = [
    ('AAPL',    265598,    60,  175.00, 195.50, 10_861.00,  1_139.00),
    ('MSFT',    272093,    40,  380.00, 420.75, 15_583.00,  1_509.00),
    ('NVDA',   4815747,    25,  450.00, 680.00, 15_740.00,  5_324.00),
    ('ASML',  117589399,   15,  720.00, 680.00, 10_200.00,   -600.00),
    ('TSLA',   76792991,   35,  250.00, 220.50,  7_146.00,   -956.00),
    ('VWCE',  349375628,  250,  105.00, 115.40, 28_850.00,  2_600.00),
    ('KO',        8894,   120,   58.00,  62.50,  6_944.00,    500.00),
    ('JNJ',      17900,    50,  155.00, 160.25,  7_419.00,    243.00),
]

_INCOME_POSITIONS = [
    ('KO',        8894,   160,   58.00,  62.50,   9_259.00,    667.00),
    ('JNJ',      17900,    55,  155.00, 160.25,   8_161.00,    267.00),
    ('PG',       11703,    50,  145.00, 158.50,   7_338.00,    625.00),
    ('VZ',       16633,   300,   38.00,  40.50,  11_250.00,    694.00),
    ('O',        78050,   200,   52.00,  55.20,  10_222.00,    593.00),
    ('MO',       11210,   250,   42.00,  45.80,  10_602.00,    880.00),
    ('PEP',       6413,    40,  165.00, 172.30,   6_381.00,    270.00),
    ('SCHD',  56945527,   350,   26.00,  28.50,   9_236.00,    810.00),
]

_GROWTH_POSITIONS = [
    ('NVDA',   4815747,    30,  450.00, 680.00,  18_889.00,  6_389.00),
    ('TSLA',  76792991,    50,  250.00, 220.50,  10_208.00, -1_366.00),
    ('AMD',     4391,      80,  130.00, 155.00,  11_481.00,  1_852.00),
    ('META', 107113386,    25,  480.00, 545.00,  12_616.00,  1_505.00),
    ('PLTR',  444554742,  400,   18.00,  24.50,   9_074.00,  2_407.00),
    ('COIN',  443461367,   40,  200.00, 245.00,   9_074.00,  1_667.00),
    ('AVGO',   28358,       8, 1200.00,1380.00,  10_222.00,  1_333.00),
    ('ARKK',  244536293,  300,   48.00,  54.20,  15_056.00,  1_722.00),
]

# Per-ticker market-data overlays.  Prev-close is set so the holdings table
# shows a realistic daily move; Income names are intentionally low-volatility,
# Growth names show larger swings.
_MARKET_DATA = {
    # Balanced
    'AAPL': dict(bid=195.45, ask=195.55, open=194.20, high=196.10, low=193.80, prev_close=193.90, volume=48_230_000, low_52w=164.08, high_52w=237.23, vwap=195.10),
    'MSFT': dict(bid=420.50, ask=421.00, open=418.60, high=422.30, low=417.90, prev_close=418.20, volume=17_450_000, low_52w=309.45, high_52w=468.35, vwap=420.60),
    'NVDA': dict(bid=679.50, ask=680.50, open=670.00, high=685.00, low=668.50, prev_close=672.10, volume=41_800_000, low_52w=250.13, high_52w=750.00, vwap=678.20),
    'ASML': dict(bid=679.80, ask=680.40, open=682.00, high=684.50, low=676.30, prev_close=683.00, volume=1_120_000, low_52w=568.80, high_52w=1024.00, vwap=680.10),
    'TSLA': dict(bid=220.30, ask=220.70, open=224.00, high=225.10, low=218.70, prev_close=223.80, volume=74_600_000, low_52w=138.80, high_52w=299.29, vwap=220.90),
    'VWCE': dict(bid=115.30, ask=115.50, open=114.80, high=115.60, low=114.70, prev_close=114.90, volume=385_000, low_52w=94.20, high_52w=117.80, vwap=115.20),
    'KO':   dict(bid=62.45,  ask=62.55,  open=62.10,  high=62.80,  low=62.00,  prev_close=62.20,  volume=12_340_000, low_52w=51.55, high_52w=65.02, vwap=62.40),
    'JNJ':  dict(bid=160.15, ask=160.35, open=159.80, high=161.00, low=159.40, prev_close=159.70, volume=5_820_000,  low_52w=143.13, high_52w=175.97, vwap=160.20),
    # Income (only the new tickers here — KO/JNJ already above)
    'PG':   dict(bid=158.40, ask=158.60, open=157.50, high=159.20, low=157.10, prev_close=157.80, volume=8_120_000,  low_52w=145.20, high_52w=170.55, vwap=158.40),
    'VZ':   dict(bid=40.45,  ask=40.55,  open=40.10,  high=40.70,  low=39.90,  prev_close=40.05,  volume=16_240_000, low_52w=32.20,  high_52w=44.73,  vwap=40.40),
    'O':    dict(bid=55.15,  ask=55.25,  open=54.80,  high=55.40,  low=54.60,  prev_close=54.85,  volume=5_180_000,  low_52w=47.35,  high_52w=62.15,  vwap=55.10),
    'MO':   dict(bid=45.75,  ask=45.85,  open=45.30,  high=46.00,  low=45.10,  prev_close=45.20,  volume=9_350_000,  low_52w=38.40,  high_52w=50.20,  vwap=45.70),
    'PEP':  dict(bid=172.20, ask=172.40, open=171.50, high=173.00, low=171.20, prev_close=171.40, volume=4_120_000,  low_52w=145.80, high_52w=180.50, vwap=172.10),
    'SCHD': dict(bid=28.45,  ask=28.55,  open=28.20,  high=28.70,  low=28.10,  prev_close=28.25,  volume=6_540_000,  low_52w=24.10,  high_52w=30.40,  vwap=28.45),
    # Growth (only the new tickers — NVDA/TSLA already above)
    'AMD':  dict(bid=154.90, ask=155.10, open=152.00, high=156.50, low=151.50, prev_close=152.30, volume=35_120_000, low_52w=90.50,  high_52w=181.20, vwap=154.80),
    'META': dict(bid=544.80, ask=545.20, open=540.00, high=547.00, low=539.50, prev_close=540.50, volume=12_410_000, low_52w=380.60, high_52w=580.30, vwap=544.50),
    'PLTR': dict(bid=24.45,  ask=24.55,  open=23.80,  high=24.80,  low=23.70,  prev_close=23.95,  volume=65_140_000, low_52w=14.10,  high_52w=32.60,  vwap=24.40),
    'COIN': dict(bid=244.80, ask=245.20, open=240.00, high=247.00, low=238.50, prev_close=240.50, volume=8_320_000,  low_52w=110.50, high_52w=283.40, vwap=244.50),
    'AVGO': dict(bid=1379.50,ask=1380.50,open=1370.00,high=1385.00,low=1365.00,prev_close=1372.00,volume=1_240_000,  low_52w=950.00, high_52w=1450.00,vwap=1379.00),
    'ARKK': dict(bid=54.15,  ask=54.25,  open=53.40,  high=54.60,  low=53.20,  prev_close=53.50,  volume=12_310_000, low_52w=35.20,  high_52w=62.40,  vwap=54.10),
}


def _div_entries(portfolio_id: str) -> dict:
    """IBKR-style dividend tick data per portfolio. Mirrors tick-59 shape."""
    today = datetime.now().date()

    def next_q(month_offset: int) -> str:
        d = today.replace(day=15)
        month = d.month + month_offset
        year  = d.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        return d.replace(year=year, month=month).isoformat()

    if portfolio_id == 'balanced':
        return {
            'KO':   {'past_12m': 1.88, 'next_12m': 1.94, 'next_date': next_q(1), 'next_amount': 0.485},
            'JNJ':  {'past_12m': 4.76, 'next_12m': 4.96, 'next_date': next_q(2), 'next_amount': 1.24},
            'MSFT': {'past_12m': 3.00, 'next_12m': 3.32, 'next_date': next_q(2), 'next_amount': 0.83},
        }
    if portfolio_id == 'income':
        return {
            'KO':   {'past_12m': 1.88, 'next_12m': 1.94, 'next_date': next_q(1), 'next_amount': 0.485},
            'JNJ':  {'past_12m': 4.76, 'next_12m': 4.96, 'next_date': next_q(2), 'next_amount': 1.24},
            'PG':   {'past_12m': 4.03, 'next_12m': 4.20, 'next_date': next_q(1), 'next_amount': 1.0065},
            'VZ':   {'past_12m': 2.66, 'next_12m': 2.71, 'next_date': next_q(1), 'next_amount': 0.6775},
            'O':    {'past_12m': 3.16, 'next_12m': 3.20, 'next_date': next_q(1), 'next_amount': 0.265},
            'MO':   {'past_12m': 4.08, 'next_12m': 4.16, 'next_date': next_q(2), 'next_amount': 1.04},
            'PEP':  {'past_12m': 5.42, 'next_12m': 5.62, 'next_date': next_q(1), 'next_amount': 1.405},
            'SCHD': {'past_12m': 1.05, 'next_12m': 1.10, 'next_date': next_q(2), 'next_amount': 0.275},
        }
    # growth — no dividends
    return {}


def _trade_entries(portfolio_id: str) -> list:
    """Six fake fills over the past 7 days, newest last (ibkr_client sorts ascending)."""
    if portfolio_id == 'balanced':
        fills = [
            (6, 'AAPL', 'BUY',  10, 194.80),
            (5, 'NVDA', 'BUY',   5, 672.40),
            (4, 'VWCE', 'BUY',  25, 114.90),
            (3, 'TSLA', 'SELL', 10, 222.10),
            (2, 'MSFT', 'BUY',   5, 418.50),
            (1, 'KO',   'BUY',  20,  62.20),
        ]
    elif portfolio_id == 'income':
        fills = [
            (6, 'KO',   'BUY', 30,  62.10),
            (5, 'VZ',   'BUY', 50,  40.20),
            (4, 'O',    'BUY', 25,  54.90),
            (3, 'PEP',  'BUY',  8, 171.50),
            (2, 'SCHD', 'BUY', 50,  28.30),
            (1, 'MO',   'BUY', 30,  45.50),
        ]
    else:  # growth
        fills = [
            (6, 'NVDA', 'BUY',   5, 672.40),
            (5, 'AMD',  'BUY',  20, 153.10),
            (4, 'META', 'BUY',   5, 542.00),
            (3, 'PLTR', 'SELL', 50,  24.30),
            (2, 'COIN', 'BUY',   5, 242.50),
            (1, 'ARKK', 'BUY',  30,  53.80),
        ]

    now = datetime.now().replace(microsecond=0)
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


# Per-portfolio account characteristics (cash, daily_pnl) — chosen to make
# each profile feel different at a glance.
_ACCOUNT_OVERLAYS = {
    'balanced': {'cash_eur': 5_757.42, 'cash_usd': 2_140.55, 'daily_pnl':  287.40},
    'income':   {'cash_eur': 8_420.10, 'cash_usd': 1_640.00, 'daily_pnl':   95.20},
    'growth':   {'cash_eur': 12_180.65,'cash_usd': 3_220.40, 'daily_pnl': -428.70},
}

_POSITION_SETS = {
    'balanced': _BALANCED_POSITIONS,
    'income':   _INCOME_POSITIONS,
    'growth':   _GROWTH_POSITIONS,
}


def build_demo_payload(portfolio_id: str = DEFAULT_PORTFOLIO_ID) -> dict:
    """Build the full demo payload for the requested portfolio.

    Falls back to the default portfolio if the id is unknown so callers never
    have to validate.
    """
    if portfolio_id not in _POSITION_SETS:
        portfolio_id = DEFAULT_PORTFOLIO_ID

    positions = []
    for ticker, conid, qty, avg, cur, mv, pnl in _POSITION_SETS[portfolio_id]:
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

    overlay  = _ACCOUNT_OVERLAYS[portfolio_id]
    total_mv = sum(p['market_value'] for p in positions)
    cash_eur = overlay['cash_eur']
    net_liq  = round(total_mv + cash_eur, 2)

    account = {
        'base_currency':        'EUR',
        'cash_usd':             overlay['cash_usd'],
        'cash_base':            cash_eur,
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
        'daily_pnl':            overlay['daily_pnl'],
    }

    # Only ship the market-data slice for this portfolio's tickers.
    tickers     = {p['ticker'] for p in positions}
    market_data = {k: dict(v) for k, v in _MARKET_DATA.items() if k in tickers}

    return {
        'positions':   positions,
        'market_data': market_data,
        'div_data':    _div_entries(portfolio_id),
        'trades':      _trade_entries(portfolio_id),
        'account':     account,
    }
