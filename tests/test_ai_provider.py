"""Tests for ai_provider.py — provider detection and text helpers, no HTTP calls."""

import pytest
from ai_provider import detect_provider, provider_label, _strip_followups


# ── detect_provider ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('key,expected', [
    ('sk-ant-api03-abc123', 'anthropic'),
    ('sk-proj-abc123',      'openai'),
    ('sk-abc123',           'openai'),
    ('xai-abc123',          'xai'),
    ('',                    None),
    ('invalid-key',         None),
])
def test_detect_provider(key, expected):
    assert detect_provider(key) == expected


# ── provider_label ───────────────────────────────────────────────────────────

def test_provider_label_returns_string():
    for name in ('anthropic', 'openai', 'xai'):
        label = provider_label(name)
        assert isinstance(label, str) and label


def test_provider_label_unknown():
    label = provider_label('unknown_provider')
    assert isinstance(label, str)


# ── _strip_followups ─────────────────────────────────────────────────────────

def test_strip_followups_with_valid_json():
    text = 'Here is my analysis.\n\nFOLLOWUPS: ["Q1?", "Q2?", "Q3?"]'
    body, followups = _strip_followups(text)
    assert 'FOLLOWUPS' not in body
    assert len(followups) == 3
    assert followups[0] == 'Q1?'


def test_strip_followups_without_marker():
    body, followups = _strip_followups('Just a plain answer.')
    assert body == 'Just a plain answer.'
    assert followups == []


def test_strip_followups_empty_string():
    body, followups = _strip_followups('')
    assert body == ''
    assert followups == []


def test_strip_followups_invalid_json():
    text = 'Answer.\n\nFOLLOWUPS: not valid json'
    body, followups = _strip_followups(text)
    # Should not raise; followups may be empty or partial
    assert isinstance(followups, list)


def test_strip_followups_body_preserved():
    text = 'Multi\nline\nbody.\n\nFOLLOWUPS: ["Q?"]'
    body, _ = _strip_followups(text)
    assert 'Multi' in body
    assert 'line' in body
