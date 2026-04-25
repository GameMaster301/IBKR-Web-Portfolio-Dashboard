"""Data fetch + connection-status banner + retry + demo-mode toggles.

Owns the IBKR-facing side of the UI loop: the 60-second portfolio refresh,
the status banner that switches between connecting / disconnected /
no-positions / connected, the retry button, and the two demo-mode buttons.

Also owns the keyboard-shortcut clientside callback (R = refresh, Esc =
close) since both shortcuts target IDs that this module already manages.
"""

from __future__ import annotations

import time
from datetime import datetime

from dash import Input, Output, State, html, no_update

from analytics import get_dividend_data_yf
from config import cfg
from dashboard_core.helpers import badge, status_banner
from data_processor import get_summary, process_positions
from ibkr_client import (
    connection_status,
    fetch_all_data,
    is_demo_mode,
    request_retry,
    set_demo_mode,
)
from styles import (
    COLOR_BAD,
    COLOR_BAD_BG,
    COLOR_BORDER,
    COLOR_BORDER_STRONG,
    COLOR_GOOD,
    COLOR_GOOD_BG,
    COLOR_GOOD_MEDIUM,
    COLOR_SURFACE,
    COLOR_SURFACE_SOFT,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_STRONG,
    COLOR_WARN_BG,
    COLOR_WARN_BORDER,
    COLOR_WARN_DEEP,
)

# Startup grace period: during the first ~25 s after launch we show a
# "Connecting …" spinner instead of "Disconnected" — the IB thread needs a
# few seconds to establish its socket, and up to ~15 s per port if it has
# to fall through to a second candidate. Once we've connected successfully
# at least once, we drop the grace period and report the real status.
_APP_START        = time.time()
_STARTUP_GRACE_S  = 25
_EVER_CONNECTED   = False

_REFRESH_MS = cfg['dashboard']['refresh_interval_seconds'] * 1000


def register(app):
    # ── Keyboard shortcuts (clientside) ───────────────────────────────────────
    # Attaches a single document-level keydown listener on first render.
    # The window._kbInit guard prevents duplicate listeners if Dash ever
    # re-runs this callback (e.g. hot-reload in dev mode).
    #
    #   R / r  → clicks the hidden kb-refresh-btn  → triggers fetch_data
    #   Escape → clicks the hidden kb-escape-btn   → clears selected-ticker
    #
    # We deliberately skip the event when focus is inside an <input> or
    # <textarea> so the user can still type without triggering the shortcut.
    app.clientside_callback(
        """
        function(id) {
            if (!window._kbInit) {
                window._kbInit = true;
                document.addEventListener('keydown', function(e) {
                    var tag = (e.target.tagName || '').toLowerCase();
                    if (tag === 'input' || tag === 'textarea') return;

                    if (e.key === 'r' || e.key === 'R') {
                        var btn = document.getElementById('kb-refresh-btn');
                        if (btn) btn.click();
                    }
                    if (e.key === 'Escape') {
                        var btn = document.getElementById('kb-escape-btn');
                        if (btn) btn.click();
                    }
                });
            }
            return window.dash_clientside.no_update;
        }
        """,
        # We need *some* output; writing to kb-refresh-btn.disabled is
        # harmless (the button is hidden and never actually disabled by any
        # other callback).
        Output('kb-refresh-btn', 'disabled'),
        # app-root.id is a static string — it fires exactly once on mount.
        Input('app-root', 'id'),
    )

    # ── Data fetch ────────────────────────────────────────────────────────────
    @app.callback(
        Output('portfolio-data', 'data'),
        Output('connection-status', 'data'),
        Input('refresh-interval', 'n_intervals'),
        Input('startup-interval', 'n_intervals'),  # fires once at 5 s to catch post-restart timing
        Input('kb-refresh-btn', 'n_clicks'),       # triggered when user presses R
    )
    def fetch_data(*_):
        global _EVER_CONNECTED
        raw = fetch_all_data()
        if not raw or not raw['positions']:
            if raw is not None:
                _EVER_CONNECTED = True
                return {}, 'no_positions'
            real = connection_status()
            if real != 'connected' and not _EVER_CONNECTED \
                    and (time.time() - _APP_START) < _STARTUP_GRACE_S:
                return {}, 'connecting'
            return {}, real
        _EVER_CONNECTED = True
        df = process_positions(raw['positions'], raw.get('market_data', {}))
        if df.empty:
            return {}, 'no_positions'
        summary = get_summary(df)
        # IBKR tick-59 data first; yfinance fills any gaps
        div_data = raw.get('div_data', {})
        tickers  = [p['ticker'] for p in raw['positions']]
        missing  = [t for t in tickers if t not in div_data]
        if missing:
            div_data.update(get_dividend_data_yf(missing))
        return {
            'positions': df.to_dict('records'),
            'summary':   summary,
            'account':   raw['account'],
            'div_data':  div_data,
        }, 'connected'

    # ── Status banner + connection badge ──────────────────────────────────────
    @app.callback(
        Output('status-banner', 'children'),
        Output('connection-badge', 'children'),
        Output('last-updated', 'children'),
        Output('retry-connection-wrap', 'style'),
        Output('exit-demo-wrap', 'style'),
        Input('connection-status', 'data'),
        Input('portfolio-data', 'data'),
    )
    def update_status(status, data):
        ts = f"Updated {datetime.now().strftime('%H:%M:%S')}"
        retry_hidden = {'display': 'none', 'textAlign': 'center', 'marginBottom': '24px'}
        retry_shown  = {'display': 'block', 'textAlign': 'center', 'marginBottom': '24px'}
        exit_demo_hidden = {'display': 'none'}
        exit_demo_shown  = {'display': 'block'}
        demo = is_demo_mode()

        if status in ('loading', 'connecting'):
            spinner = html.Div(className='ibkr-spinner', style={
                'width': '36px', 'height': '36px', 'margin': '0 auto 24px',
                'border': '3px solid #e5e7eb', 'borderTop': f'3px solid {COLOR_GOOD}',
                'borderRadius': '50%',
            })
            title = "Starting dashboard…" if status == 'loading' else "Connecting to IBKR…"
            body  = ("Loading your portfolio. This takes a few seconds."
                     if status == 'loading'
                     else "Reaching IB Gateway / TWS. Trying all common ports — takes up to 20 seconds.")
            banner = html.Div([
                spinner,
                html.P(title, style={'fontSize': '18px', 'fontWeight': '600',
                                     'color': COLOR_TEXT_STRONG, 'margin': '0',
                                     'letterSpacing': '-0.3px'}),
                html.P(body, style={'fontSize': '14px', 'color': COLOR_TEXT_MUTED,
                                    'margin': '6px 0 0', 'lineHeight': '1.5'}),
            ], style={'textAlign': 'center', 'padding': '52px 40px',
                      'background': COLOR_SURFACE_SOFT, 'borderRadius': '14px',
                      'border': f'0.5px solid {COLOR_BORDER}'})
            return banner, badge("Connecting...", COLOR_TEXT_MUTED, COLOR_SURFACE, COLOR_BORDER_STRONG), "", retry_hidden, exit_demo_hidden

        if status == 'disconnected':
            return status_banner("🔌", "Not connected to IBKR",
                                 "Make sure IB Gateway or TWS is open and logged in — the dashboard auto-detects the port and reconnects automatically.\n"
                                 "IB Gateway: Configure → Settings → API → Settings → Enable ActiveX and Socket Clients (Port 4002 paper / 4001 live).\n"
                                 "TWS: Edit → Global Configuration → API → Settings → Enable ActiveX and Socket Clients (Port 7497 paper / 7496 live).",
                                 COLOR_BAD_BG), \
                   badge("● Disconnected", COLOR_BAD, COLOR_BAD_BG, '#fecaca'), ts, retry_shown, exit_demo_hidden

        if status == 'no_positions':
            conn_badge = (badge("● Demo mode", COLOR_WARN_DEEP, COLOR_WARN_BG, COLOR_WARN_BORDER) if demo
                          else badge("● Connected", COLOR_GOOD, COLOR_GOOD_BG, COLOR_GOOD_MEDIUM))
            return status_banner("📭", "No positions found",
                                 "Connected to IBKR successfully, but your account has no open positions.", COLOR_SURFACE_SOFT), \
                   conn_badge, ts, retry_hidden, (exit_demo_shown if demo else exit_demo_hidden)

        if demo:
            return None, badge("● Demo mode", COLOR_WARN_DEEP, COLOR_WARN_BG, COLOR_WARN_BORDER), ts, retry_hidden, exit_demo_shown
        return None, badge(f"● Live · {_REFRESH_MS // 1000}s", COLOR_GOOD, COLOR_GOOD_BG, COLOR_GOOD_MEDIUM), ts, retry_hidden, exit_demo_hidden

    @app.callback(
        Output('connection-status', 'data', allow_duplicate=True),
        Input('retry-connection-btn', 'n_clicks'),
        prevent_initial_call=True,
    )
    def retry_connection(n_clicks):
        if not n_clicks:
            return no_update
        request_retry()
        return 'connecting'

    # ── Demo mode toggle ──────────────────────────────────────────────────────
    # The two buttons write to kb-refresh-btn.n_clicks to piggyback on
    # fetch_data's existing trigger — that re-runs the fetch immediately with
    # the new demo flag so the user sees the portfolio populate (or clear)
    # without waiting for the 60-second refresh tick.
    @app.callback(
        Output('kb-refresh-btn', 'n_clicks', allow_duplicate=True),
        Output('connection-status', 'data', allow_duplicate=True),
        Input('try-demo-btn', 'n_clicks'),
        State('kb-refresh-btn', 'n_clicks'),
        prevent_initial_call=True,
    )
    def enable_demo(n, cur):
        if not n:
            return no_update, no_update
        set_demo_mode(True)
        return (cur or 0) + 1, 'connected'

    @app.callback(
        Output('kb-refresh-btn', 'n_clicks', allow_duplicate=True),
        Output('connection-status', 'data', allow_duplicate=True),
        Input('exit-demo-btn', 'n_clicks'),
        State('kb-refresh-btn', 'n_clicks'),
        prevent_initial_call=True,
    )
    def disable_demo(n, cur):
        if not n:
            return no_update, no_update
        set_demo_mode(False)
        return (cur or 0) + 1, connection_status()
