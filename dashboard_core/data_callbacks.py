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
    connection_attempt,
    connection_status,
    ensure_connection_started,
    fetch_all_data,
    is_demo_mode,
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

    # ── Fast poll: update attempt counter every 4 s while connecting ─────────
    # Kept separate from fetch_data so the main data-fetch path is untouched.
    # fetch_all_data() returns None immediately when not connected, so this
    # callback is cheap.  The attempt count is encoded in the status string
    # ('connecting:N') so each increment changes the store value and forces
    # update_status to re-fire even though the base status hasn't changed.
    @app.callback(
        Output('connection-status', 'data', allow_duplicate=True),
        Input('fast-connect-poll', 'n_intervals'),
        State('connection-status', 'data'),
        prevent_initial_call=True,
    )
    def poll_connect_attempt(_, current_status):
        base = (current_status or '').split(':')[0]
        if base not in ('connecting', 'loading'):
            return no_update
        real = connection_status()
        if real != 'connected' and not _EVER_CONNECTED \
                and (time.time() - _APP_START) < _STARTUP_GRACE_S:
            return f'connecting:{connection_attempt()}'
        # Grace period just expired — push the real status immediately so the
        # banner switches within 4 s instead of waiting for the 60 s tick.
        if base in ('connecting', 'loading') and not _EVER_CONNECTED:
            return real
        return no_update

    # ── Data fetch ────────────────────────────────────────────────────────────
    @app.callback(
        Output('portfolio-data', 'data'),
        Output('connection-status', 'data'),
        Input('refresh-interval', 'n_intervals'),
        Input('startup-interval', 'n_intervals'),  # fires once at 2 s to catch post-restart timing
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
                return {}, f'connecting:{connection_attempt()}'
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
        Output('portfolio-title', 'children'),
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

        # status may be 'loading', 'connecting', or 'connecting:N' (N = attempt count)
        base_status = status.split(':')[0] if status else ''
        raw_attempt  = int(status.split(':')[1]) if status and ':' in status else None

        if base_status in ('loading', 'connecting'):
            spinner = html.Div(className='ibkr-spinner', style={
                'width': '36px', 'height': '36px', 'margin': '0 auto 24px',
                'border': '3px solid #e5e7eb', 'borderTop': f'3px solid {COLOR_GOOD}',
                'borderRadius': '50%',
            })
            dots = html.Span([
                html.Span(".", className='conn-dot', style={'animationDelay': '0s'}),
                html.Span(".", className='conn-dot', style={'animationDelay': '0.3s'}),
                html.Span(".", className='conn-dot', style={'animationDelay': '0.6s'}),
            ])
            if base_status == 'loading':
                title_content = "Starting dashboard…"
                body = "Loading your portfolio. This takes a few seconds."
                attempt_line = None
            else:
                title_content = "Connecting to IBKR"
                body = "Reaching IB Gateway / TWS. Trying all common ports — takes up to 30 seconds."
                if raw_attempt is not None:
                    attempt_line = html.P(
                        [f"Attempt {raw_attempt + 1} ", dots],
                        style={'fontSize': '13px', 'color': COLOR_TEXT_MUTED,
                               'margin': '8px 0 0', 'fontWeight': '600',
                               'letterSpacing': '0.03em'},
                    )
                else:
                    attempt_line = None
            banner = html.Div([
                spinner,
                html.P(title_content, style={'fontSize': '18px', 'fontWeight': '600',
                                     'color': COLOR_TEXT_STRONG, 'margin': '0',
                                     'letterSpacing': '-0.3px'}),
                html.P(body, style={'fontSize': '14px', 'color': COLOR_TEXT_MUTED,
                                    'margin': '6px 0 0', 'lineHeight': '1.5'}),
                *([attempt_line] if attempt_line else []),
            ], style={'textAlign': 'center', 'padding': '52px 40px',
                      'background': COLOR_SURFACE_SOFT, 'borderRadius': '14px',
                      'border': f'0.5px solid {COLOR_BORDER}'})
            return banner, badge("Connecting...", COLOR_TEXT_MUTED, COLOR_SURFACE, COLOR_BORDER_STRONG), "", "Portfolio", retry_hidden, exit_demo_hidden

        if status == 'disconnected':
            return status_banner("🔌", "Not connected to IBKR",
                                 "Make sure IB Gateway or TWS is open and logged in — the dashboard auto-detects the port and reconnects automatically.\n"
                                 "IB Gateway: Configure → Settings → API → Settings → Enable ActiveX and Socket Clients (Port 4002 paper / 4001 live).\n"
                                 "TWS: Edit → Global Configuration → API → Settings → Enable ActiveX and Socket Clients (Port 7497 paper / 7496 live).",
                                 COLOR_BAD_BG), \
                   badge("● Disconnected", COLOR_BAD, COLOR_BAD_BG, '#fecaca'), ts, "Portfolio", retry_shown, exit_demo_hidden

        if status == 'no_positions':
            conn_badge = (badge("● Demo mode", COLOR_WARN_DEEP, COLOR_WARN_BG, COLOR_WARN_BORDER, 'badge-warn') if demo
                          else badge("● Connected", COLOR_GOOD, COLOR_GOOD_BG, COLOR_GOOD_MEDIUM))
            return status_banner("📭", "No positions found",
                                 "Connected to IBKR successfully, but your account has no open positions.", COLOR_SURFACE_SOFT), \
                   conn_badge, ts, "Sample portfolio" if demo else "Portfolio", retry_hidden, (exit_demo_shown if demo else exit_demo_hidden)

        if demo:
            return None, badge("● Demo mode", COLOR_WARN_DEEP, COLOR_WARN_BG, COLOR_WARN_BORDER, 'badge-warn'), "", "Sample portfolio", retry_hidden, exit_demo_shown
        return None, badge(f"● Live · {_REFRESH_MS // 1000}s", COLOR_GOOD, COLOR_GOOD_BG, COLOR_GOOD_MEDIUM), ts, "Portfolio", retry_hidden, exit_demo_hidden

    @app.callback(
        Output('main-content-area', 'style'),
        Input('connection-status', 'data'),
    )
    def toggle_main_content(status):
        if (status or '').split(':')[0] == 'disconnected':
            return {'display': 'none'}
        return {}

    @app.callback(
        Output('connection-status', 'data', allow_duplicate=True),
        Input('retry-connection-btn', 'n_clicks'),
        prevent_initial_call=True,
    )
    def retry_connection(n_clicks):
        if not n_clicks:
            return no_update
        ensure_connection_started()  # starts thread if demo just exited, else request_retry
        return 'connecting:0'

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

    # ── Dark mode toggle ──────────────────────────────────────────────────────
    # Restore stored theme on page load: sets data-theme on <html> and shows
    # the correct icon.
    app.clientside_callback(
        """
        function(id, theme) {
            var t = theme || 'light';
            document.documentElement.setAttribute('data-theme', t);
            return t === 'dark' ? '☀' : '🌙';
        }
        """,
        Output('theme-toggle-btn', 'children'),
        Input('app-root', 'id'),
        State('theme-mode', 'data'),
    )

    # Toggle on button click: flips the store value, updates data-theme, flips
    # the icon.
    app.clientside_callback(
        """
        function(n, theme) {
            if (!n) return [window.dash_clientside.no_update, window.dash_clientside.no_update];
            var newTheme = (theme === 'dark') ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', newTheme);
            return [newTheme, newTheme === 'dark' ? '☀' : '🌙'];
        }
        """,
        Output('theme-mode', 'data'),
        Output('theme-toggle-btn', 'children', allow_duplicate=True),
        Input('theme-toggle-btn', 'n_clicks'),
        State('theme-mode', 'data'),
        prevent_initial_call=True,
    )
