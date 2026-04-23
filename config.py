"""
Configuration loader.

Priority (highest wins):
  1. Environment variables (IBKR_HOST, IBKR_PORT, etc.)
  2. config.yaml  (CONFIG_PATH env var overrides the default location)
  3. Built-in defaults

This means Docker deployments can drive everything from env vars without
touching config.yaml, while local users keep the YAML-first workflow.
"""

from __future__ import annotations

import os

import yaml

# Allow config.yaml to live elsewhere (useful for Docker volume mounts).
_CONFIG_PATH = os.environ.get(
    'CONFIG_PATH',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yaml'),
)

_DEFAULTS: dict = {
    'ibkr': {
        'host':                   '127.0.0.1',
        'port':                   4002,
        'client_id':              1,
        'readonly':               True,
        'reconnect_delay_seconds': 5,   # base delay; actual delay is exponential
        'heartbeat_interval':     30,   # seconds between liveness checks
    },
    'dashboard': {
        'host':                     '127.0.0.1',
        'port':                     8050,
        'refresh_interval_seconds': 60,
    },
    'display': {
        'eurusd_fallback': 1.08,
    },
    'app': {
        'demo_mode': False,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _apply_env_overrides(cfg: dict) -> dict:
    """Read well-known environment variables and overlay them on cfg."""
    env = os.environ.get

    # IBKR connection
    if env('IBKR_HOST'):
        cfg['ibkr']['host'] = env('IBKR_HOST')
    if env('IBKR_PORT'):
        cfg['ibkr']['port'] = int(env('IBKR_PORT'))
    if env('IBKR_CLIENT_ID'):
        cfg['ibkr']['client_id'] = int(env('IBKR_CLIENT_ID'))
    if env('IBKR_READONLY'):
        cfg['ibkr']['readonly'] = env('IBKR_READONLY', '').lower() not in ('false', '0', 'no')
    if env('IBKR_RECONNECT_DELAY'):
        cfg['ibkr']['reconnect_delay_seconds'] = int(env('IBKR_RECONNECT_DELAY'))

    # Dashboard server
    if env('DASH_HOST'):
        cfg['dashboard']['host'] = env('DASH_HOST')
    if env('DASH_PORT'):
        cfg['dashboard']['port'] = int(env('DASH_PORT'))
    if env('REFRESH_INTERVAL'):
        cfg['dashboard']['refresh_interval_seconds'] = int(env('REFRESH_INTERVAL'))

    # Display
    if env('EURUSD_FALLBACK'):
        cfg['display']['eurusd_fallback'] = float(env('EURUSD_FALLBACK'))

    # App-wide
    if env('DEMO_MODE'):
        cfg['app']['demo_mode'] = env('DEMO_MODE', '').lower() in ('1', 'true', 'yes', 'on')

    return cfg


def load_config() -> dict:
    cfg = _DEFAULTS
    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH, 'r') as f:
            user_cfg = yaml.safe_load(f) or {}
        cfg = _deep_merge(_DEFAULTS, user_cfg)
    cfg = _apply_env_overrides(cfg)
    return cfg


cfg = load_config()
