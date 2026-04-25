"""Tests for coach.py — rules-based scenario rendering, no network, no LLM."""

import pytest
from dash import html
from coach import SCENARIOS, render_scenario


def test_all_scenarios_registered():
    ids = {s['id'] for s in SCENARIOS}
    assert ids == {'perf', 'biggest_risk', 'what_if', 'sector_geo', 'vs_market'}


def test_all_scenarios_have_label():
    for s in SCENARIOS:
        assert s.get('label'), f"scenario {s['id']} missing label"


@pytest.mark.parametrize('scenario_id', [s['id'] for s in SCENARIOS])
def test_scenario_renders_with_demo_data(scenario_id, demo_payload):
    result = render_scenario(scenario_id, demo_payload, None, None)
    assert result is not None


@pytest.mark.parametrize('scenario_id', [s['id'] for s in SCENARIOS])
def test_scenario_renders_with_no_data(scenario_id):
    result = render_scenario(scenario_id, None, None, None)
    assert result is not None  # should return "not ready" card, not raise


def test_unknown_scenario_id_does_not_raise():
    result = render_scenario('nonexistent', None, None, None)
    assert result is not None


def test_perf_scenario_returns_dash_element(demo_payload):
    result = render_scenario('perf', demo_payload, None, None)
    assert isinstance(result, html.Div)


def test_biggest_risk_scenario_returns_dash_element(demo_payload):
    result = render_scenario('biggest_risk', demo_payload, None, None)
    assert isinstance(result, html.Div)
