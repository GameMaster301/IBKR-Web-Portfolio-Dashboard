"""Ops endpoint registered on the Flask app that Dash wraps.

GET /health  — machine-readable liveness check, returns 200 ok or 503 degraded.
"""

from __future__ import annotations

import time

_START = time.time()


def register(flask_app) -> None:
    import ibkr_client
    from flask import jsonify

    @flask_app.route('/health')
    def health():
        connected = ibkr_client.connection_status() == 'connected'
        demo      = ibkr_client.is_demo_mode()
        ok        = connected or demo
        payload   = {
            'status':         'ok' if ok else 'degraded',
            'connected':      connected,
            'demo':           demo,
            'uptime_seconds': int(time.time() - _START),
        }
        return jsonify(payload), 200 if ok else 503
