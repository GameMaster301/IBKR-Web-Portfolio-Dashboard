"""
Pure pandas transforms over raw IBKR positions.

Takes the raw `positions` list + `market_data` dict from `ibkr_client`
and enriches it with: pnl_pct, total_cost, allocation_pct, daily change,
bid/ask spread, 52-week range position, volume, VWAP.

No network, no state. Everything is deterministic given its inputs — so
these functions are safe to call from any thread or from tests.
"""

from __future__ import annotations

import pandas as pd


def process_positions(positions, market_data=None):
    if not positions:
        return pd.DataFrame()

    df = pd.DataFrame(positions)
    market_data = market_data or {}

    if 'price_stale' not in df.columns:
        df['price_stale'] = False
    df['pnl_pct'] = ((df['current_price'] - df['avg_cost']) / df['avg_cost'] * 100).round(2)
    df['total_cost'] = (df['avg_cost'] * df['quantity']).round(2)

    total_value = df['market_value'].sum()
    df['allocation_pct'] = ((df['market_value'] / total_value) * 100).round(2)

    # Market data enrichment
    daily_changes = []
    daily_change_pcts = []
    spreads = []
    low_52w_list = []
    high_52w_list = []
    range_pct_list = []
    volumes = []
    vwaps = []

    for _, row in df.iterrows():
        sym = row['ticker']
        md = market_data.get(sym, {})
        price = row['current_price']

        # Daily change vs previous close
        prev_close = md.get('prev_close')
        if prev_close and prev_close > 0:
            chg = round(price - prev_close, 2)
            chg_pct = round((chg / prev_close) * 100, 2)
        else:
            chg = None
            chg_pct = None
        daily_changes.append(chg)
        daily_change_pcts.append(chg_pct)

        # Bid/ask spread
        bid = md.get('bid')
        ask = md.get('ask')
        if bid and ask and bid > 0 and ask > 0:
            spreads.append(round(ask - bid, 4))
        else:
            spreads.append(None)

        # 52-week range
        low_52w = md.get('low_52w')
        high_52w = md.get('high_52w')
        low_52w_list.append(low_52w)
        high_52w_list.append(high_52w)
        if low_52w and high_52w and high_52w > low_52w:
            range_pct = round((price - low_52w) / (high_52w - low_52w) * 100, 1)
        else:
            range_pct = None
        range_pct_list.append(range_pct)

        volumes.append(md.get('volume'))
        vwaps.append(md.get('vwap'))

    df['daily_change'] = daily_changes
    df['daily_change_pct'] = daily_change_pcts
    df['spread'] = spreads
    df['low_52w'] = low_52w_list
    df['high_52w'] = high_52w_list
    df['range_52w_pct'] = range_pct_list
    df['volume'] = volumes
    df['vwap'] = vwaps

    df = df.sort_values('market_value', ascending=False).reset_index(drop=True)
    return df


def get_summary(df):
    if df.empty:
        return {}

    total_cost = df['total_cost'].sum()
    total_value = df['market_value'].sum()
    total_unrealized = df['unrealized_pnl'].sum()
    total_realized = df['realized_pnl'].sum()

    mask = df['daily_change'].notna()
    total_daily_pnl = round((df.loc[mask, 'daily_change'] * df.loc[mask, 'quantity']).sum(), 2) if mask.any() else None

    summary = {
        'total_value':         round(total_value, 2),
        'total_unrealized_pnl':round(total_unrealized, 2),
        'total_realized_pnl':  round(total_realized, 2),
        'total_pnl_pct':       round(total_unrealized / total_cost * 100, 2) if total_cost else 0,
        'num_positions':       len(df),
        'best_performer':      df.loc[df['pnl_pct'].idxmax(), 'ticker'],
        'worst_performer':     df.loc[df['pnl_pct'].idxmin(), 'ticker'],
        'total_daily_pnl':     total_daily_pnl,
        'largest_position':    df.iloc[0]['ticker'],
        'largest_position_pct':df.iloc[0]['allocation_pct'],
    }

    return summary
