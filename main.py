"""
Entry point — starts the IBKR background thread and the Dash server.

Docker notes
------------
- Set OPEN_BROWSER=0 (or any falsy value) to suppress the browser launch.
  The Docker image sets this automatically via ENV in the Dockerfile.
- All config can be driven by environment variables; see config.py.
"""

from __future__ import annotations

# Fail early and clearly on unsupported Python rather than crashing later with a
# cryptic AttributeError. 3.10 is the floor ib_async 2.x requires.
import sys

if sys.version_info < (3, 10):  # noqa: UP036  intentional runtime floor check
    raise SystemExit(
        f"This app needs Python 3.10 or newer — you're on "
        f"{sys.version_info.major}.{sys.version_info.minor}. "
        "Install a newer Python (or run the Docker image, which bundles 3.12)."
    )

# asyncio loop must be created before any ib_async-touching import below.
import asyncio

asyncio.set_event_loop(asyncio.new_event_loop())

import logging
import os
import threading

from config import cfg
from dashboard import app
from ibkr_client import save_connection_params, set_demo_mode, start_connection

# ── Logging ────────────────────────────────────────────────────────────────────
# Set LOG_FORMAT=json in the environment (or in Docker) to emit newline-
# delimited JSON logs suitable for Loki / CloudWatch / any log aggregator.
# Default is the human-readable format, which is easier to follow locally.
if os.environ.get('LOG_FORMAT', '').lower() == 'json':
    try:
        from pythonjsonlogger import jsonlogger
        _handler = logging.StreamHandler()
        _handler.setFormatter(jsonlogger.JsonFormatter(
            '%(asctime)s %(levelname)s %(name)s %(message)s',
            datefmt='%Y-%m-%dT%H:%M:%S',
        ))
        logging.root.setLevel(logging.INFO)
        logging.root.addHandler(_handler)
    except ImportError:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s  %(levelname)-8s  %(name)s  %(message)s',
            datefmt='%Y-%m-%dT%H:%M:%S',
        )
else:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s  %(levelname)-8s  %(name)s  %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%S',
    )

# Quiet third-party chatter. ib_async logs every market-data warning and
# connection handshake at INFO; yfinance logs 404s for tickers Yahoo doesn't
# have (normal for European ETFs like SPPE). None of it is actionable.
logging.getLogger('ib_async').setLevel(logging.WARNING)
logging.getLogger('ib_async.wrapper').setLevel(logging.ERROR)
logging.getLogger('yfinance').setLevel(logging.ERROR)
log = logging.getLogger(__name__)


def _open_browser():
    import time
    import webbrowser
    time.sleep(1.5)
    host = cfg['dashboard']['host']
    port = cfg['dashboard']['port']
    # Use localhost when host is 0.0.0.0 (Docker bind-all)
    display_host = 'localhost' if host in ('0.0.0.0', '') else host
    webbrowser.open(f'http://{display_host}:{port}')


if __name__ == '__main__':
    import sys
    demo = '--demo' in sys.argv or cfg.get('app', {}).get('demo_mode')
    ibkr = cfg['ibkr']
    if demo:
        log.info("Demo mode — saving connection params; thread starts on first Retry")
        set_demo_mode(True)
        save_connection_params(
            host=ibkr['host'],
            port=ibkr['port'],
            client_id=ibkr['client_id'],
            readonly=ibkr['readonly'],
            reconnect_delay=ibkr['reconnect_delay_seconds'],
            heartbeat_interval=ibkr.get('heartbeat_interval', 30),
        )
    else:
        log.info("Starting IBKR connection thread → %s:%d", ibkr['host'], ibkr['port'])
        start_connection(
            host=ibkr['host'],
            port=ibkr['port'],
            client_id=ibkr['client_id'],
            readonly=ibkr['readonly'],
            reconnect_delay=ibkr['reconnect_delay_seconds'],
            heartbeat_interval=ibkr.get('heartbeat_interval', 30),
        )

    # Pre-warm the valuation cache so the Market Valuation section is ready
    # when the user first opens the dashboard. On warm cache this is instant;
    # on cold cache the ~15-30s HTTP fetches run in the background while the
    # user sees the connecting/loading screen instead of a 30-second shimmer.
    def _prewarm_valuation():
        try:
            from market_valuation import (
                get_buffett_indicator,
                get_shiller_cape,
                get_sp500_pe,
                get_treasury_yield,
            )
            from net_util import run_parallel
            run_parallel({
                'buffett':  get_buffett_indicator,
                'sp500_pe': get_sp500_pe,
                'cape':     get_shiller_cape,
                'treasury': get_treasury_yield,
            })
        except Exception:
            pass
    threading.Thread(target=_prewarm_valuation, daemon=True, name='valuation-prewarm').start()

    # Skip browser auto-open in Docker / headless environments
    open_browser = os.environ.get('OPEN_BROWSER', '1').lower() not in ('0', 'false', 'no')
    if open_browser:
        threading.Thread(target=_open_browser, daemon=True).start()

    dash_cfg = cfg['dashboard']
    log.info("Starting Dash server on %s:%d", dash_cfg['host'], dash_cfg['port'])
    app.run(
        host=dash_cfg['host'],
        port=dash_cfg['port'],
        debug=False,
    )
