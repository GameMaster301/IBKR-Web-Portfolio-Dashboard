"""Tests for data_processor.py — pure pandas transforms, no network."""

import pandas as pd
import pytest


def test_returns_dataframe(demo_df):
    assert isinstance(demo_df, pd.DataFrame)


def test_expected_columns(demo_df):
    required = {'ticker', 'pnl_pct', 'allocation_pct', 'market_value',
                 'daily_change', 'daily_change_pct', 'spread',
                 'low_52w', 'high_52w'}
    assert required.issubset(demo_df.columns)


def test_position_count(demo_df):
    assert len(demo_df) == 8


def test_allocation_sums_to_100(demo_df):
    total = demo_df['allocation_pct'].sum()
    assert abs(total - 100.0) < 0.5


def test_pnl_pct_sign_matches_pnl(demo_df):
    # P&L % and unrealized P&L should have the same sign (or both be zero).
    for _, row in demo_df.iterrows():
        if row['unrealized_pnl'] > 0:
            assert row['pnl_pct'] > 0, f"{row['ticker']}: pnl_pct sign mismatch"
        elif row['unrealized_pnl'] < 0:
            assert row['pnl_pct'] < 0, f"{row['ticker']}: pnl_pct sign mismatch"


def test_market_value_positive(demo_df):
    assert (demo_df['market_value'] > 0).all()


def test_sorted_by_market_value(demo_df):
    values = demo_df['market_value'].tolist()
    assert values == sorted(values, reverse=True)


# ── get_summary ──────────────────────────────────────────────────────────────

def test_summary_keys(demo_summary):
    for key in ('total_value', 'total_unrealized_pnl', 'num_positions',
                'best_performer', 'worst_performer', 'total_daily_pnl'):
        assert key in demo_summary, f"missing key: {key}"


def test_summary_position_count(demo_summary):
    assert demo_summary['num_positions'] == 8


def test_summary_total_value_positive(demo_summary):
    assert demo_summary['total_value'] > 0


def test_best_and_worst_performer_present(demo_summary):
    # best_performer / worst_performer are ticker strings
    assert isinstance(demo_summary['best_performer'], str)
    assert isinstance(demo_summary['worst_performer'], str)


def test_empty_dataframe_returns_empty_dict():
    from data_processor import get_summary
    summary = get_summary(pd.DataFrame())
    assert summary == {}
