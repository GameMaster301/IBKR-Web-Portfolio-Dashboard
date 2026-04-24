"""
Entry point — starts the IBKR background thread and the Dash server.

Docker notes
------------
- Set OPEN_BROWSER=0 (or any falsy value) to suppress the browser launch.
  The Docker image sets this automatically via ENV in the Dockerfile.
- All config can be driven by environment variables; see config.py.
"""

from __future__ import annotations

# asyncio loop must be created before any ib_async-touching import below.
import asyncio

asyncio.set_event_loop(asyncio.new_event_loop())

import logging
import os
import threading

from config import cfg
from dashboard import app
from ibkr_client import set_demo_mode, start_connection

# ── Logging ────────────────────────────────────────────────────────────────────
# Use a slightly richer format so Docker log aggregators can parse level / name.
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
    if cfg.get('app', {}).get('demo_mode'):
        log.info("Demo mode — no IBKR connection")
        set_demo_mode(True)
    else:
        ibkr = cfg['ibkr']
        log.info("Starting IBKR connection thread → %s:%d", ibkr['host'], ibkr['port'])
        start_connection(
            host=ibkr['host'],
            port=ibkr['port'],
            client_id=ibkr['client_id'],
            readonly=ibkr['readonly'],
            reconnect_delay=ibkr['reconnect_delay_seconds'],
            heartbeat_interval=ibkr.get('heartbeat_interval', 30),
        )

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
