"""Shared pytest fixtures for ibkrdash tests.

All fixtures are built from demo_data.py so tests run with no network,
no IBKR connection, and no Dash server.
"""

import sys
import os

# Ensure the project root is on sys.path regardless of how pytest is invoked.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from demo_data import build_demo_payload


@pytest.fixture(scope='session')
def demo_payload():
    return build_demo_payload()


@pytest.fixture(scope='session')
def demo_df(demo_payload):
    from data_processor import process_positions
    return process_positions(
        demo_payload['positions'],
        demo_payload.get('market_data'),
    )


@pytest.fixture(scope='session')
def demo_summary(demo_df):
    from data_processor import get_summary
    return get_summary(demo_df)
