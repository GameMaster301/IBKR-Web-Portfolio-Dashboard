"""Typed payloads for every dcc.Store in the dashboard.

These are the contracts between ibkr_client/market_* producers and the
Dash callbacks that consume them. If you change a shape here, the
consumers light up red in the IDE.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict


class PositionData(TypedDict):
    ticker: str
    quantity: float
    avg_cost: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    pnl_pct: float
    allocation_pct: float
    price_stale: NotRequired[bool]
    daily_change: NotRequired[float | None]
    daily_change_pct: NotRequired[float | None]
    low_52w: NotRequired[float | None]
    high_52w: NotRequired[float | None]
    spread_pct: NotRequired[float | None]
    range_52w_pct: NotRequired[float | None]


class SummaryData(TypedDict):
    total_value: float
    total_unrealized_pnl: float
    num_positions: int
    largest_position: str
    largest_position_pct: float
    best_performer: str
    worst_performer: str
    total_pnl_pct: NotRequired[float | None]
    total_daily_pnl: NotRequired[float | None]


class AccountData(TypedDict, total=False):
    cash_eur: float
    net_liquidation: float
    gross_position_value: float
    eurusd_rate: float
    daily_pnl: float | None


class PortfolioData(TypedDict):
    summary: SummaryData
    positions: list[PositionData]
    account: AccountData
    div_data: NotRequired[dict]


class SectorGeoEntry(TypedDict, total=False):
    sector: str
    industry: str
    country: str
    longName: str
    is_etf: bool
    sector_weights: dict[str, float]


class MarketIntelData(TypedDict):
    tickers: list[str]
    sector_geo: dict[str, SectorGeoEntry]
    earnings: dict


class ValuationData(TypedDict, total=False):
    buffett: dict
    sp500_pe: dict
    cape: dict
    treasury: dict
