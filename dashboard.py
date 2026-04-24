"""
Dash layout and callbacks for the IBKR portfolio dashboard.

This module is intentionally large — it owns the full UI surface (layout +
every @app.callback). A split into `dashboard_core/` submodules is planned
(see notes.txt, step 5). Until then keep this file self-contained: helpers
at the top, layout in one place, callbacks grouped by section banner.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import date, datetime

import dash
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
from dash import ALL, ctx, dash_table, dcc, html, no_update
from dash.dependencies import Input, Output, State

import ai_provider
from analytics import get_dividend_data_yf
from coach import SCENARIOS, render_scenario
from config import cfg
from dashboard_core import export as _export_mod
from dashboard_core.helpers import (
    badge,
    make_table,
    section_label,
    status_banner,
    to_eur,
)
from data_processor import get_summary, process_positions
from ibkr_client import (
    connection_status,
    fetch_all_data,
    is_demo_mode,
    request_retry,
    set_demo_mode,
)
from market_intel import get_earnings_data, get_price_history, get_sector_geo
from market_valuation import (
    buffett_zone,
    cape_zone,
    get_buffett_indicator,
    get_shiller_cape,
    get_sp500_pe,
    get_treasury_yield,
    pe_zone,
)
from net_util import run_parallel
from styles import CARD, LINK_PILL
from trade_history import (
    load_uploaded_trades,
    parse_activity_csv,
    save_uploaded_trades,
)

log = logging.getLogger(__name__)

app = dash.Dash(__name__, suppress_callback_exceptions=True)

# Startup grace period: during the first ~25 s after launch we show a
# "Connecting …" spinner instead of "Disconnected" — the IB thread needs a
# few seconds to establish its socket, and up to ~15 s per port if it has
# to fall through to a second candidate. Once we've connected successfully
# at least once, we drop the grace period and report the real status.
_APP_START        = time.time()
_STARTUP_GRACE_S  = 25
_EVER_CONNECTED   = False

# Re-exported so legacy inline references `style=_LINK_STYLE` keep working.
# Source of truth lives in styles.py.
_LINK_STYLE = LINK_PILL

# ── Layout ─────────────────────────────────────────────────────────────────────

_REFRESH_MS = cfg['dashboard']['refresh_interval_seconds'] * 1000

app.layout = html.Div([

    # Sticky header — stays pinned to the top of the viewport while the user scrolls.
    # Negative left/right margins let it bleed to the page edges while the content
    # remains indented via matching padding, giving a full-width background bar.
    html.Div([
        html.Div([
            html.Div([
                html.H1("Portfolio", style={'margin': '0', 'fontSize': '22px', 'fontWeight': '600', 'color': '#111'}),
                html.P(id='last-updated', style={'margin': '4px 0 0', 'color': '#888', 'fontSize': '14px'}),
            ]),
            html.Div([
                html.Div(id='connection-badge'),
                html.Div(
                    html.Button("Exit demo", id='exit-demo-btn', n_clicks=0, style={
                        'fontSize': '13px', 'color': '#92400e', 'background': '#fffbeb',
                        'border': '0.5px solid #fcd34d', 'borderRadius': '8px',
                        'padding': '6px 12px', 'cursor': 'pointer',
                    }),
                    id='exit-demo-wrap',
                    style={'display': 'none'},
                ),
                html.Button("↓ PDF", id='export-pdf-btn', n_clicks=0, style={
                    'fontSize': '13px', 'color': '#555', 'background': '#f5f5f5',
                    'border': '0.5px solid #ddd', 'borderRadius': '8px',
                    'padding': '6px 14px', 'cursor': 'pointer',
                }),
            ], id='header-actions', style={'display': 'flex', 'alignItems': 'center', 'gap': '12px'}),
        ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'flex-start'}),
    ], id='sticky-header', style={
        'position': 'sticky', 'top': '0', 'zIndex': '100',
        'backgroundColor': '#fff',
        # bleed out past the 64px side-padding of app-root so the bar spans full width
        'marginLeft': '-64px', 'marginRight': '-64px',
        'paddingLeft': '64px', 'paddingRight': '64px',
        'paddingTop': '24px', 'paddingBottom': '17px',
        'marginBottom': '28px',
        'borderBottom': '0.5px solid #ebebeb',
    }),

    # Status / loading banner (hidden when data loaded)
    html.Div(id='status-banner', style={'marginBottom': '24px'}),

    # Retry-connection + Try-demo buttons — shown only when status='disconnected'.
    # Live in the static layout so their callbacks always register.
    html.Div([
        html.Button("↻ Retry connection", id='retry-connection-btn', n_clicks=0, style={
            'fontSize': '14px', 'fontWeight': '500', 'color': '#fff',
            'background': '#dc2626', 'border': 'none', 'borderRadius': '8px',
            'padding': '10px 22px', 'cursor': 'pointer',
        }),
        html.Button("▶ Try demo mode", id='try-demo-btn', n_clicks=0, style={
            'fontSize': '14px', 'fontWeight': '500', 'color': '#111',
            'background': '#fff', 'border': '0.5px solid #d4d4d4', 'borderRadius': '8px',
            'padding': '10px 22px', 'cursor': 'pointer', 'marginLeft': '10px',
        }),
        html.P("No TWS? Explore the dashboard with a sample portfolio.",
               style={'fontSize': '13px', 'color': '#888', 'margin': '10px 0 0'}),
    ],
        id='retry-connection-wrap',
        style={'display': 'none', 'textAlign': 'center', 'marginBottom': '24px'},
    ),

    # 4 summary cards
    html.Div(id='summary-cards', style={
        'display': 'grid', 'gridTemplateColumns': 'repeat(4, 1fr)',
        'gap': '14px', 'marginBottom': '24px',
    }),

    # Holdings + Donut
    html.Div([
        html.Div([
            html.Div([
                html.Div([
                    html.Div([
                        section_label("Holdings"),
                        html.Span(id='stale-price-badge'),
                    ], style={'display': 'flex', 'alignItems': 'center', 'gap': '12px'}),
                    html.Button("✨ Ask", id='coach-toggle-btn', n_clicks=0, style={
                        'fontSize': '13px', 'color': '#555', 'background': '#f5f5f5',
                        'border': '0.5px solid #ddd', 'borderRadius': '8px',
                        'padding': '6px 14px', 'cursor': 'pointer',
                    }),
                ], style={'display': 'flex', 'justifyContent': 'space-between',
                          'alignItems': 'center', 'marginBottom': '0px'}),
                html.Span(id='positions-count', style={
                    'fontSize': '14px', 'color': '#888',
                    'display': 'block', 'marginBottom': '12px',
                }),
            ]),
            html.Div(id='holdings-table'),
        ], style={**CARD, 'flex': '1', 'alignSelf': 'flex-start'}),

        html.Div([
            section_label("Allocation"),
            dcc.Graph(id='donut-chart', config={'displayModeBar': False}, style={'height': '260px'}),
        ], style={**CARD, 'width': '260px', 'display': 'flex', 'flexDirection': 'column', 'justifyContent': 'flex-start'}),
    ], style={'display': 'flex', 'gap': '14px'}),

    # Position detail panel (shown on row click). Rendered BEFORE the coach
    # panel so that when both are open the ticker chart sits above the coach
    # conversation — the natural reading order for "I clicked a ticker and
    # then asked the coach about it".
    html.Div(id='position-detail'),

    # AI Coach panel (shown when the ✨ Ask coach button is clicked)
    html.Div(id='coach-panel'),

    # ── Market Intelligence block ──────────────────────────────────────────────
    # All sections below are populated by populate_market_intel() which fetches
    # yfinance data (cached 4 h). On first load they show a loading message
    # while the fetch runs in the background; subsequent loads are instant.
    html.Div([
        html.P("Market Intelligence", style={
            'fontSize': '15px', 'color': '#888', 'margin': '0 0 4px',
            'textTransform': 'uppercase', 'letterSpacing': '0.07em', 'fontWeight': '600',
        }),
        html.P("Sector & geography ",
               style={'fontSize': '15px', 'color': '#888', 'margin': '0'}),
    ], style={
        'marginTop': '40px', 'paddingTop': '32px',
        'borderTop': '0.5px solid #f0f0f0', 'marginBottom': '24px',
    }),

    html.Div(id='sector-geo-section'),

    # Earnings
    html.Div(id='earnings-section', style={
        'marginTop': '32px', 'paddingTop': '28px',
        'borderTop': '0.5px solid #f0f0f0',
    }),

    # Dividends
    html.Div(id='dividend-section', style={
        'marginTop': '32px', 'paddingTop': '28px',
        'borderTop': '0.5px solid #f0f0f0',
    }),


    # Market Valuation (Buffett / S&P PE / Shiller CAPE)
    html.Div(id='market-valuation-section', style={
        'marginTop': '32px', 'paddingTop': '28px',
        'borderTop': '0.5px solid #f0f0f0',
    }),

    # ── Toast notification container ──────────────────────────────────────────
    # Fixed to the bottom-right corner; pointer-events:none so it never blocks
    # clicks on underlying content.  The inner child is replaced by the
    # update_toast callback and given a unique `key` so React re-mounts it
    # (= restarts the CSS animation) on every new message.
    html.Div(id='toast', style={
        'position': 'fixed', 'bottom': '28px', 'right': '28px',
        'zIndex': '9999', 'pointerEvents': 'none',
    }),

    # ── Hidden buttons triggered by keyboard shortcuts ────────────────────────
    # The clientside callback below simulates .click() on these when the user
    # presses R (refresh) or Escape (close position detail).  Using hidden
    # buttons keeps all state-change logic in regular server-side callbacks.
    html.Button(id='kb-refresh-btn', n_clicks=0,
                style={'display': 'none', 'position': 'absolute'}),
    html.Button(id='kb-escape-btn',  n_clicks=0,
                style={'display': 'none', 'position': 'absolute'}),

    dcc.Download(id='download-pdf'),
    # A one-shot 5-second interval fires on page load so positions appear
    # quickly after a restart (before the main 60-second cycle kicks in).
    dcc.Interval(id='startup-interval', interval=2000, n_intervals=0, max_intervals=1),
    dcc.Interval(id='refresh-interval', interval=_REFRESH_MS, n_intervals=0),
    dcc.Store(id='portfolio-data'),
    dcc.Store(id='market-intel-data'),      # yfinance enrichment, 4-h cache
    dcc.Store(id='valuation-data'),         # macro valuation indicators, 4-h cache
    dcc.Store(id='connection-status', data='loading'),
    dcc.Store(id='selected-ticker', data=None),
    dcc.Store(id='selected-period', data='1M'),
    dcc.Store(id='uploaded-trades', data=load_uploaded_trades()),
    # Coach panel state
    dcc.Store(id='coach-open', data=False),
    dcc.Store(id='coach-mode', data='preset'),            # 'preset' or 'ai'
    dcc.Store(id='coach-active-id', data=None),           # currently picked scenario id
    # Browser-persistent API key (localStorage). Never touches the server's disk.
    dcc.Store(id='coach-api-key', storage_type='local', data=''),
    # Thread list (persisted): [{id, title, created, history:[{q,a,error?,followups?}]}]
    dcc.Store(id='coach-threads', storage_type='local', data=[]),
    # Active thread id (persisted)
    dcc.Store(id='coach-active-thread-id', storage_type='local', data=None),
    # Derived: history of the currently active thread — kept as a store so all
    # the chat-rendering + copy callbacks stay unchanged.
    dcc.Store(id='coach-chat-history', data=[]),
    # Pre-fill for the chat input — set when the user clicks "Ask coach" from
    # a position-detail panel, or when they hit Edit on a past question.
    dcc.Store(id='coach-prefill', data=''),
    # Pending user question — set by submit_question, consumed by run_llm.
    # run_llm's Input is this Store, so writing a question triggers the
    # LLM call directly (no Interval indirection needed).
    dcc.Store(id='coach-pending-q', data=None),
    # Dummy sinks for clientside callbacks (copy / auto-scroll)
    dcc.Store(id='coach-copy-signal', data=0),
    dcc.Store(id='coach-scroll-signal', data=0),
    # Sinks for smooth-scroll-into-view clientside callbacks
    dcc.Store(id='position-detail-scroll-signal', data=0),
    dcc.Store(id='coach-panel-scroll-signal', data=0),

], id='app-root', style={
    'fontFamily': '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    'fontSize': '16px',
    'padding': '48px 64px',
    'maxWidth': '1400px',
    'margin': '0 auto',
    'backgroundColor': '#fff',
    'color': '#111',
})


# ── Keyboard shortcuts (clientside) ───────────────────────────────────────────
# Attaches a single document-level keydown listener on first render.
# The window._kbInit guard prevents duplicate listeners if Dash ever
# re-runs this callback (e.g. hot-reload in dev mode).
#
#   R / r  → clicks the hidden kb-refresh-btn  → triggers fetch_data
#   Escape → clicks the hidden kb-escape-btn   → clears selected-ticker
#
# We deliberately skip the event when focus is inside an <input> or <textarea>
# so the user can still type in any input field without triggering the shortcut.
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
    # We need *some* output; writing to kb-refresh-btn.disabled is harmless
    # (the button is hidden and never actually disabled by any other callback).
    Output('kb-refresh-btn', 'disabled'),
    # app-root.id is a static string — it fires exactly once on mount.
    Input('app-root', 'id'),
)


# ── Data fetch ─────────────────────────────────────────────────────────────────

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


# ── Status banner + connection badge ───────────────────────────────────────────

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
            'width': '44px', 'height': '44px', 'margin': '0 auto 18px',
            'border': '4px solid #e5e7eb', 'borderTop': '4px solid #16a34a',
            'borderRadius': '50%',
        })
        title = "Starting dashboard..." if status == 'loading' else "Connecting to IBKR..."
        body  = ("Loading your portfolio. This takes a few seconds."
                 if status == 'loading'
                 else "Reaching IB Gateway / TWS. Trying all common ports — this takes up to 20 seconds.")
        banner = html.Div([
            spinner,
            html.P(title, style={'fontSize': '17px', 'fontWeight': '600',
                                 'color': '#111', 'margin': '0 0 6px'}),
            html.P(body, style={'fontSize': '15px', 'color': '#888',
                                'margin': '0', 'lineHeight': '1.6'}),
        ], style={'textAlign': 'center', 'padding': '48px 32px',
                  'background': '#fafafa', 'borderRadius': '14px',
                  'border': '0.5px solid #ebebeb'})
        return banner, badge("Connecting...", '#888', '#f5f5f5', '#e0e0e0'), "", retry_hidden, exit_demo_hidden

    if status == 'disconnected':
        return status_banner("🔌", "Not connected to IBKR",
                             "Make sure IB Gateway or TWS is open and logged in — the dashboard auto-detects the port and reconnects automatically.\n"
                             "IB Gateway: Configure → Settings → API → Settings → Enable ActiveX and Socket Clients (Port 4002 paper / 4001 live).\n"
                             "TWS: Edit → Global Configuration → API → Settings → Enable ActiveX and Socket Clients (Port 7497 paper / 7496 live).",
                             '#fef2f2'), \
               badge("● Disconnected", '#dc2626', '#fef2f2', '#fecaca'), ts, retry_shown, exit_demo_hidden

    if status == 'no_positions':
        conn_badge = (badge("● Demo mode", '#92400e', '#fffbeb', '#fcd34d') if demo
                      else badge("● Connected", '#16a34a', '#f0fdf4', '#bbf7d0'))
        return status_banner("📭", "No positions found",
                             "Connected to IBKR successfully, but your account has no open positions.", '#fafafa'), \
               conn_badge, ts, retry_hidden, (exit_demo_shown if demo else exit_demo_hidden)

    if demo:
        return None, badge("● Demo mode", '#92400e', '#fffbeb', '#fcd34d'), ts, retry_hidden, exit_demo_shown
    return None, badge(f"● Live · {_REFRESH_MS // 1000}s", '#16a34a', '#f0fdf4', '#bbf7d0'), ts, retry_hidden, exit_demo_hidden


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


# ── Demo mode toggle ───────────────────────────────────────────────────────────
# The two buttons write to kb-refresh-btn.n_clicks to piggyback on fetch_data's
# existing trigger — that re-runs the fetch immediately with the new demo flag
# so the user sees the portfolio populate (or clear) without waiting for the
# 60-second refresh tick.

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


# ── Summary cards ──────────────────────────────────────────────────────────────

@app.callback(
    Output('summary-cards', 'children'),
    Input('portfolio-data', 'data'),
)
def update_summary(data):
    if not data or 'summary' not in data:
        return []

    s = data['summary']
    a = data.get('account', {})
    rate = a.get('eurusd_rate', 1.08)

    total_val  = s['total_value']
    unreal_pnl = s['total_unrealized_pnl']
    daily_pnl  = a.get('daily_pnl') or s.get('total_daily_pnl')
    cash_eur   = a.get('cash_eur', 0)
    pnl_pct    = s.get('total_pnl_pct')

    def card(label, eur_val, pnl_pct=None, is_pnl=False, note=None):
        positive = eur_val >= 0
        accent = ('#16a34a' if positive else '#dc2626') if is_pnl else '#111'
        val_str = f"€{eur_val:+,.2f}" if is_pnl else f"€{eur_val:,.2f}"
        usd_str = f"${eur_val * rate:,.2f}"
        return html.Div([
            html.P(label, style={
                'fontSize': '14px', 'color': '#999', 'margin': '0 0 10px',
                'textTransform': 'uppercase', 'letterSpacing': '0.05em', 'fontWeight': '500',
            }),
            html.P(val_str, style={
                'fontSize': '26px', 'fontWeight': '600', 'margin': '0',
                'color': accent if is_pnl else '#111', 'letterSpacing': '-0.5px',
            }),
            html.Div([
                html.Span(usd_str, style={'fontSize': '14px', 'color': '#999'}),
                html.Span(f" · {pnl_pct:+.2f}%",
                          style={'fontSize': '14px', 'color': accent}) if pnl_pct is not None else None,
                html.Span(f" · {note}",
                          style={'fontSize': '14px', 'color': '#888'}) if note is not None else None,
            ], style={'marginTop': '4px'}),
        ], style={
            'background': '#fafafa', 'borderRadius': '12px', 'padding': '18px',
            'borderLeft': f'3px solid {"#ebebeb" if not is_pnl else accent}',
        })

    total_val_eur = to_eur(total_val, rate)
    cash_pct = round(cash_eur / total_val_eur * 100, 1) if total_val_eur else None
    return [
        card("Total Value",    total_val_eur),
        card("Unrealized P&L", to_eur(unreal_pnl, rate), pnl_pct=pnl_pct, is_pnl=True),
        card("Today's P&L",    to_eur(daily_pnl, rate) if daily_pnl is not None else 0, is_pnl=True),
        card("Cash",           cash_eur, note=f"{cash_pct:.1f}% of portfolio" if cash_pct is not None else None),
    ]


# ── Holdings ───────────────────────────────────────────────────────────────────

@app.callback(
    Output('holdings-table', 'children'),
    Output('positions-count', 'children'),
    Output('stale-price-badge', 'children'),
    Input('portfolio-data', 'data'),
)
def update_holdings(data):
    if not data or 'positions' not in data:
        return html.P("—", style={'color': '#ccc', 'fontSize': '15px'}), '', None
    df = pd.DataFrame(data['positions'])
    rate = data.get('account', {}).get('eurusd_rate', 1.08)

    count = f"{len(df)} positions"
    any_stale = df.get('price_stale', pd.Series(False)).any()
    stale_badge = html.Span("● Market closed · last-close prices",
                            style={
                                'fontSize': '13px', 'color': '#b45309',
                                'background': '#fffbeb', 'border': '0.5px solid #fde68a',
                                'padding': '3px 9px', 'borderRadius': '20px',
                                'marginTop': '-10px',
                            }) if any_stale else None

    # Pre-format display columns
    df['price_display'] = df.apply(
        lambda r: f"~${r['current_price']:,.2f}" if r.get('price_stale') else f"${r['current_price']:,.2f}",
        axis=1
    )
    df['value_eur_display'] = (df['market_value'] / rate).apply(lambda v: f"€{v:,.0f}")
    df['weight_display']    = df['allocation_pct'].apply(lambda v: f"{v:.1f}%")
    df['pnl_pct_display']   = df['pnl_pct'].apply(lambda v: f"{v:+.2f}%")

    table_data = df[[
        'ticker', 'quantity', 'avg_cost', 'price_display',
        'market_value', 'value_eur_display', 'pnl_pct', 'pnl_pct_display',
        'unrealized_pnl', 'weight_display',
    ]].to_dict('records')

    table = dash_table.DataTable(
        id='holdings-datatable',
        columns=[
            {'name': 'Ticker',   'id': 'ticker',           'type': 'text'},
            {'name': 'Qty',      'id': 'quantity',          'type': 'numeric'},
            {'name': 'Avg Cost', 'id': 'avg_cost',          'type': 'numeric',
             'format': {'specifier': '$,.2f'}},
            {'name': 'Price',    'id': 'price_display',     'type': 'text'},
            {'name': 'Value ($)', 'id': 'market_value',     'type': 'numeric',
             'format': {'specifier': '$,.0f'}},
            {'name': 'Value (€)', 'id': 'value_eur_display',  'type': 'text'},
            {'name': 'P&L %',   'id': 'pnl_pct_display',    'type': 'text'},
            {'name': 'P&L ($)', 'id': 'unrealized_pnl',    'type': 'numeric',
             'format': {'specifier': '+$,.2f'}},
            {'name': 'Weight',  'id': 'weight_display',     'type': 'text'},
        ],
        data=table_data,
        sort_action='native',
        sort_by=[{'column_id': 'market_value', 'direction': 'desc'}],
        page_size=50,
        style_as_list_view=True,
        style_table={'overflowX': 'auto'},
        style_header={
            'fontSize': '14px', 'color': '#999', 'fontWeight': '500',
            'textTransform': 'uppercase', 'letterSpacing': '0.04em',
            'backgroundColor': '#fff', 'border': 'none',
            'borderBottom': '0.5px solid #f5f5f5',
            'paddingBottom': '14px',
        },
        style_cell={
            'fontFamily': '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
            'fontSize': '16px', 'padding': '12px 12px',
            'backgroundColor': '#fff', 'color': '#111',
            'border': 'none', 'borderBottom': '0.5px solid #f5f5f5',
        },
        style_cell_conditional=(
            [{'if': {'column_id': 'ticker'}, 'fontWeight': '600', 'textAlign': 'left'}] +
            [{'if': {'column_id': c}, 'textAlign': 'right'}
             for c in ['quantity', 'avg_cost', 'price_display', 'market_value',
                       'value_eur_display', 'pnl_pct_display', 'unrealized_pnl', 'weight_display']]
        ),
        style_data_conditional=[
            {'if': {'filter_query': '{pnl_pct} >= 0', 'column_id': 'pnl_pct_display'},
             'color': '#16a34a'},
            {'if': {'filter_query': '{pnl_pct} < 0', 'column_id': 'pnl_pct_display'},
             'color': '#dc2626'},
            {'if': {'filter_query': '{unrealized_pnl} >= 0', 'column_id': 'unrealized_pnl'},
             'color': '#16a34a'},
            {'if': {'filter_query': '{unrealized_pnl} < 0', 'column_id': 'unrealized_pnl'},
             'color': '#dc2626'},
{'if': {'filter_query': '{price_display} contains "~"', 'column_id': 'price_display'},
             'color': '#b45309'},
            {'if': {'state': 'active'}, 'backgroundColor': '#f0f7ff', 'border': 'none'},
        ],
    )

    return table, count, stale_badge


# ── Donut ──────────────────────────────────────────────────────────────────────

@app.callback(
    Output('donut-chart', 'figure'),
    Input('portfolio-data', 'data'),
)
def update_donut(data):
    blank = go.Figure()
    blank.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                        xaxis=dict(visible=False), yaxis=dict(visible=False))
    if not data or 'positions' not in data:
        return blank
    df = pd.DataFrame(data['positions'])
    colors = ['#378ADD', '#f97316', '#a855f7', '#22c55e', '#eab308', '#ec4899', '#14b8a6']
    fig = px.pie(df, values='market_value', names='ticker', hole=0.68,
                 color_discrete_sequence=colors)
    fig.update_traces(
        textposition='none',
        textinfo='none',
        hovertemplate='<b>%{label}</b><br>$%{value:,.2f}  ·  %{percent}<extra></extra>',
    )
    fig.update_layout(
        margin=dict(t=0, b=0, l=0, r=0),
        showlegend=True,
        legend=dict(orientation='v', x=0.5, y=0.5, xanchor='center', yanchor='middle',
                    font=dict(size=12, color='#555'), itemclick=False, itemdoubleclick=False),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
    )
    return fig


# ── Export ─────────────────────────────────────────────────────────────────────
# PDF export moved to dashboard_core/export.py.
_export_mod.register(app)


# ── Position detail (click to expand) ─────────────────────────────────────────

@app.callback(
    Output('selected-ticker', 'data'),
    Output('holdings-datatable', 'active_cell'),
    Output('holdings-datatable', 'selected_cells'),
    Input('holdings-datatable', 'active_cell'),
    Input('kb-escape-btn', 'n_clicks'),   # triggered when user presses Escape
    Input({'type': 'position-close', 'index': ALL}, 'n_clicks'),
    State('holdings-datatable', 'data'),
    State('selected-ticker', 'data'),
    prevent_initial_call=True,
)
def select_ticker(active_cell, _esc, close_clicks, table_data, current):
    trig = ctx.triggered_id
    # Escape key or ✕ close button: always close the detail panel and clear
    # the DataTable's active-cell highlight so the row doesn't stay tinted.
    if trig == 'kb-escape-btn':
        return None, None, []
    if isinstance(trig, dict) and trig.get('type') == 'position-close':
        if not any(close_clicks or []):
            return no_update, no_update, no_update
        return None, None, []
    if not active_cell or not table_data:
        return no_update, no_update, no_update
    ticker = table_data[active_cell['row']]['ticker']
    if current == ticker:
        return None, None, []
    return ticker, no_update, no_update


def _range_bar(low, high, current):
    """Visual 52-week range bar with current price marker."""
    pct = max(0.0, min(100.0, (current - low) / (high - low) * 100))
    return html.Div([
        html.Div([
            html.Div(style={
                'position': 'absolute', 'left': 0, 'top': 0,
                'width': '100%', 'height': '4px',
                'background': '#f0f0f0', 'borderRadius': '2px',
            }),
            html.Div(style={
                'position': 'absolute', 'left': 0, 'top': 0,
                'width': f'{pct}%', 'height': '4px',
                'background': '#378ADD', 'borderRadius': '2px',
            }),
            html.Div(style={
                'position': 'absolute', 'left': f'{pct}%', 'top': '-4px',
                'width': '14px', 'height': '14px', 'borderRadius': '50%',
                'background': '#378ADD', 'border': '2px solid #fff',
                'boxShadow': '0 0 0 1.5px #378ADD',
                'transform': 'translateX(-50%)',
            }),
        ], style={'position': 'relative', 'height': '14px', 'margin': '8px 0'}),
        html.Div([
            html.Span(f"${low:,.2f}", style={'fontSize': '13px', 'color': '#555', 'fontWeight': '500'}),
            html.Span(f"{pct:.0f}% of range",
                      style={'fontSize': '13px', 'color': '#555', 'fontWeight': '500',
                             'position': 'absolute',
                             'left': '50%', 'transform': 'translateX(-50%)'}),
            html.Span(f"${high:,.2f}", style={'fontSize': '13px', 'color': '#555', 'fontWeight': '500'}),
        ], style={'display': 'flex', 'justifyContent': 'space-between', 'position': 'relative'}),
    ])


def _stat(label, value, accent=None):
    return html.Div([
        html.P(label, style={
            'fontSize': '13px', 'color': '#555', 'margin': '0 0 4px',
            'textTransform': 'uppercase', 'letterSpacing': '0.05em',
            'fontWeight': '600',
        }),
        html.P(value, style={
            'fontSize': '17px', 'fontWeight': '600', 'margin': '0',
            'color': accent or '#111',
        }),
    ], style={'minWidth': '90px'})


_PERIOD_CHOICES = ['1M', '3M', '1Y', '3Y', '5Y']
_PERIOD_TO_YF   = {'1M': '1mo', '3M': '3mo', '1Y': '1y', '3Y': '3y', '5Y': '5y'}


def _period_btn(label, active):
    return html.Button(label, id={'type': 'period-btn', 'index': label},
                       n_clicks=0, style={
        'background': '#378ADD' if active else 'transparent',
        'color':      '#fff'    if active else '#555',
        'border':     '1px solid ' + ('#378ADD' if active else '#ddd'),
        'borderRadius': '6px', 'padding': '4px 12px',
        'fontSize': '12px', 'cursor': 'pointer',
        'fontFamily': 'inherit', 'fontWeight': '500',
    })


def _build_price_sparkline(ticker, period, avg_cost, trades=None):
    yf_period = _PERIOD_TO_YF.get(period, '1mo')
    try:
        hist = get_price_history([ticker], yf_period)
    except Exception as e:
        log.debug('sparkline fetch failed for %s: %s', ticker, e)
        hist = {}
    data_h = hist.get(ticker) or {}
    prices = data_h.get('prices') or []
    dates  = data_h.get('dates')  or []
    if len(prices) < 2:
        return html.P('Chart data unavailable for this period.',
                      style={'color': '#555', 'fontSize': '14px', 'fontWeight': '500',
                             'textAlign': 'center', 'margin': '24px 0 8px'})

    first, last = prices[0], prices[-1]
    up = last >= first
    line_color = '#16a34a' if up else '#dc2626'
    fill_color = 'rgba(22,163,74,0.08)' if up else 'rgba(220,38,38,0.08)'

    fig = go.Figure(go.Scatter(
        x=dates, y=prices, mode='lines',
        line=dict(color=line_color, width=2),
        fill='tozeroy', fillcolor=fill_color,
        hovertemplate='%{x}<br>$%{y:,.2f}<extra></extra>',
    ))
    # Average cost reference line (if we know it and it's within chart range)
    if avg_cost and avg_cost == avg_cost and avg_cost > 0:
        lo, hi = min(prices), max(prices)
        if lo * 0.6 <= avg_cost <= hi * 1.4:
            fig.add_hline(y=avg_cost, line=dict(color='#555', width=1, dash='dot'),
                          annotation_text=f'Avg ${avg_cost:,.2f}',
                          annotation_position='top left',
                          annotation=dict(font=dict(size=12, color='#333')))

    # Trade markers (BUY ▲ green / SELL ▼ red) on trades whose date falls
    # within the plotted window.  `dates` are 'YYYY-MM-DD' strings.
    if trades and dates:
        date_set = set(dates)
        buys_x,  buys_y,  buys_hover  = [], [], []
        sells_x, sells_y, sells_hover = [], [], []
        for t in trades:
            tm = (t.get('time') or '')[:10]
            if not tm or tm not in date_set:
                continue
            try:
                y = prices[dates.index(tm)]
            except ValueError:
                continue
            side   = (t.get('side') or '').upper()
            shares = t.get('shares') or 0
            price_ = t.get('price') or 0
            hover  = f"{side} {shares:g} @ ${price_:,.2f}<br>{tm}"
            if side == 'BUY':
                buys_x.append(tm);  buys_y.append(y);  buys_hover.append(hover)
            elif side == 'SELL':
                sells_x.append(tm); sells_y.append(y); sells_hover.append(hover)
        if buys_x:
            fig.add_trace(go.Scatter(
                x=buys_x, y=buys_y, mode='markers', name='BUY',
                marker=dict(symbol='triangle-up', color='#16a34a',
                            size=24, line=dict(color='#fff', width=1)),
                hovertext=buys_hover, hoverinfo='text',
            ))
        if sells_x:
            fig.add_trace(go.Scatter(
                x=sells_x, y=sells_y, mode='markers', name='SELL',
                marker=dict(symbol='triangle-down', color='#dc2626',
                            size=24, line=dict(color='#fff', width=1)),
                hovertext=sells_hover, hoverinfo='text',
            ))
    pct_change = (last - first) / first * 100 if first else 0
    # Zoom y-axis to the actual price range with a small padding, and also
    # include avg_cost if it's on-chart so the dotted reference line stays
    # visible.  Without this, fill='tozeroy' anchors the axis at 0 and small
    # real ranges (e.g. $15→$17) render as a nearly-flat line.
    y_lo, y_hi = min(prices), max(prices)
    if avg_cost and avg_cost > 0 and y_lo * 0.6 <= avg_cost <= y_hi * 1.4:
        y_lo = min(y_lo, avg_cost)
        y_hi = max(y_hi, avg_cost)
    pad = (y_hi - y_lo) * 0.15 or y_hi * 0.02 or 1.0
    fig.update_layout(
        margin=dict(l=8, r=8, t=8, b=8), height=200,
        xaxis=dict(showgrid=False, showticklabels=False, title=None),
        yaxis=dict(showgrid=True, gridcolor='#f2f2f2', title=None,
                   tickfont=dict(size=12, color='#555'),
                   range=[y_lo - pad, y_hi + pad]),
        plot_bgcolor='#fff', paper_bgcolor='rgba(0,0,0,0)',
        showlegend=False,
        hoverlabel=dict(bgcolor='#fff', bordercolor='#378ADD',
                        font=dict(size=14, color='#111', family='Inter, system-ui, sans-serif')),
    )
    summary_line = html.Div([
        html.Span(f"{period} ", style={'color': '#555', 'fontSize': '13px',
                                        'fontWeight': '500',
                                        'letterSpacing': '0.05em'}),
        html.Span(f"{'+' if pct_change >= 0 else ''}{pct_change:.2f}%",
                  style={'color': line_color, 'fontSize': '13px',
                         'fontWeight': '600', 'marginLeft': '4px'}),
    ], style={'textAlign': 'right', 'marginTop': '2px'})
    return html.Div([
        dcc.Graph(figure=fig, config={'displayModeBar': False}),
        summary_line,
    ])


@app.callback(
    Output('selected-period', 'data'),
    Input({'type': 'period-btn', 'index': ALL}, 'n_clicks'),
    prevent_initial_call=True,
)
def update_selected_period(_):
    tid = ctx.triggered_id
    if isinstance(tid, dict) and tid.get('type') == 'period-btn':
        return tid.get('index') or no_update
    return no_update


@app.callback(
    Output('position-detail', 'children'),
    Input('selected-ticker', 'data'),
    Input('selected-period', 'data'),
    Input('uploaded-trades', 'data'),
    State('portfolio-data', 'data'),
)
def show_position_detail(ticker, period, uploaded, data):
    if not ticker or not data or 'positions' not in data:
        return None
    df = pd.DataFrame(data['positions'])
    matches = df[df['ticker'] == ticker]
    if matches.empty:
        return None
    r = matches.iloc[0]

    price        = r['current_price']
    daily_chg    = r.get('daily_change')
    daily_chg_pct = r.get('daily_change_pct')
    low_52w      = r.get('low_52w')
    high_52w     = r.get('high_52w')

    # Daily change header
    if daily_chg is not None and daily_chg == daily_chg:
        chg_color = '#16a34a' if daily_chg >= 0 else '#dc2626'
        chg_str   = f"{'▲' if daily_chg >= 0 else '▼'} ${abs(daily_chg):,.2f}  ({daily_chg_pct:+.2f}%)"
    else:
        chg_color, chg_str = '#555', '—'

    qty       = r.get('quantity') or 0
    mkt_val   = r.get('market_value')
    unreal    = r.get('unrealized_pnl')
    avg_cost  = r.get('avg_cost')

    if avg_cost and avg_cost == avg_cost and avg_cost > 0:
        cost_diff_pct = (price - avg_cost) / avg_cost * 100
        cost_color = '#16a34a' if cost_diff_pct >= 0 else '#dc2626'
        cost_str   = f"${avg_cost:,.2f}  ({cost_diff_pct:+.2f}%)"
    else:
        cost_color, cost_str = '#555', '—'

    qty_str    = f"{qty:,.0f}" if qty else '—'
    mkt_str    = f"${mkt_val:,.2f}" if mkt_val is not None else '—'
    if unreal is not None:
        unreal_color = '#16a34a' if unreal >= 0 else '#dc2626'
        unreal_str   = f"${unreal:+,.2f}"
    else:
        unreal_color, unreal_str = '#555', '—'

    stats = html.Div([
        _stat("Quantity", qty_str),
        _stat("Avg Cost", cost_str, cost_color),
        _stat("Market Value", mkt_str),
        _stat("Unrealized P&L", unreal_str, unreal_color),
        _stat("Daily Change", chg_str, chg_color),
    ], style={'display': 'flex', 'gap': '32px', 'flexWrap': 'wrap', 'marginTop': '17px'})

    # 52-week range
    if (low_52w and high_52w and low_52w == low_52w and high_52w == high_52w
            and high_52w > low_52w):
        range_section = html.Div([
            html.P("52-Week Range", style={
                'fontSize': '13px', 'color': '#555', 'margin': '0 0 0',
                'textTransform': 'uppercase', 'letterSpacing': '0.05em',
                'fontWeight': '600',
            }),
            _range_bar(low_52w, high_52w, price),
        ], style={'marginTop': '17px'})
    else:
        range_section = None

    # Collect trades for this ticker from live fills + uploaded CSV history.
    live_trades = (data.get('trades') or []) if isinstance(data, dict) else []
    all_trades  = list(uploaded or []) + list(live_trades)
    ticker_trades = [t for t in all_trades if (t.get('ticker') or '') == ticker]

    # Price history chart with period toggle + CSV upload button
    sel_period = period if period in _PERIOD_CHOICES else '1M'
    upload_btn = dcc.Upload(
        id={'type': 'position-trade-upload', 'index': 0},
        accept='.csv',
        multiple=False,
        children=html.Span("Upload trades CSV", style={
            'background': 'transparent', 'color': '#378ADD',
            'border': '1px solid #cfe0f5', 'borderRadius': '6px',
            'padding': '4px 10px', 'fontSize': '12px',
            'cursor': 'pointer', 'fontWeight': '500',
        }),
    )
    upload_help = html.Details([
        html.Summary("How to export from IBKR ▸", style={
            'cursor': 'pointer', 'color': '#378ADD', 'fontSize': '13px',
            'fontWeight': '600', 'marginTop': '6px',
        }),
        html.Ol([
            html.Li("Log in to the IBKR Client Portal."),
            html.Li("Go to Performance & Reports → Transaction History."),
            html.Li("Pick a date range and click the CSV / download icon."),
            html.Li("Drop the .csv file on the upload button above."),
        ], style={'fontSize': '13px', 'color': '#333', 'lineHeight': '1.7',
                  'fontWeight': '500',
                  'paddingLeft': '20px', 'margin': '6px 0 0'}),
    ])
    trade_count_note = (
        f"{len(ticker_trades)} trade{'s' if len(ticker_trades)!=1 else ''} plotted"
        if ticker_trades else "No trades on file for this ticker yet."
    )
    chart_section = html.Div([
        html.Div([
            html.P("Price History", style={
                'fontSize': '14px', 'color': '#555', 'margin': '0',
                'textTransform': 'uppercase', 'letterSpacing': '0.05em',
                'fontWeight': '600',
            }),
            html.Div([
                upload_btn,
                html.Div([_period_btn(p, p == sel_period) for p in _PERIOD_CHOICES],
                         style={'display': 'flex', 'gap': '4px'}),
            ], style={'display': 'flex', 'gap': '8px', 'alignItems': 'center'}),
        ], style={'display': 'flex', 'justifyContent': 'space-between',
                  'alignItems': 'center', 'marginBottom': '6px'}),
        _build_price_sparkline(ticker, sel_period, avg_cost, ticker_trades),
        html.Div([
            html.Span(trade_count_note, style={'fontSize': '13px', 'color': '#555', 'fontWeight': '500'}),
            html.Div(id={'type': 'position-upload-status', 'index': 0},
                     style={'fontSize': '12px'}),
        ], style={'display': 'flex', 'justifyContent': 'space-between',
                  'alignItems': 'center', 'marginTop': '2px'}),
        upload_help,
    ], style={'marginTop': '20px'})

    return html.Div([
        # Header row: ticker + price + daily change + quick links
        html.Div([
            html.Div([
                html.Span(ticker, style={'fontWeight': '700', 'fontSize': '17px', 'color': '#111'}),
                html.Span(f"${price:,.2f}",
                          style={'fontSize': '17px', 'color': '#111', 'marginLeft': '12px'}),
                html.Span(chg_str,
                          style={'fontSize': '15px', 'color': chg_color, 'marginLeft': '12px'}),
            ], style={'display': 'flex', 'alignItems': 'center'}),
            html.Div([
                html.Button(f"✨ Ask coach about {ticker}",
                            id={'type': 'position-ask-coach', 'index': 0},
                            n_clicks=0, style={
                    'fontSize': '13px', 'color': '#374151',
                    'background': '#f5f5f5', 'border': '1px solid #e5e7eb',
                    'borderRadius': '8px', 'padding': '5px 12px',
                    'cursor': 'pointer', 'fontWeight': '500',
                    'fontFamily': 'inherit', 'marginRight': '4px',
                }),
                html.A("Yahoo", href=f"https://finance.yahoo.com/quote/{ticker}",
                       target='_blank', style=_LINK_STYLE),
                html.A("TradingView", href=f"https://www.tradingview.com/symbols/{ticker}/",
                       target='_blank', style=_LINK_STYLE),
                html.Button("✕ Close", id={'type': 'position-close', 'index': 0}, n_clicks=0, style={
                    'fontSize': '13px', 'color': '#fff',
                    'background': '#dc2626', 'border': '1px solid #dc2626',
                    'borderRadius': '8px', 'padding': '5px 12px',
                    'cursor': 'pointer', 'marginLeft': '4px', 'fontWeight': '500',
                    'fontFamily': 'inherit',
                }),
            ], style={'display': 'flex', 'alignItems': 'center', 'gap': '6px'}),
        ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center'}),
        range_section,
        stats,
        chart_section,
    ], style={
        **CARD,
        'marginTop': '14px',
        'background': '#fff',
        'borderLeft': '3px solid #378ADD',
        'animation': 'slideInDown 0.25s ease-out',
    })


# ── Per-position CSV trade upload ─────────────────────────────────────────────
# The upload button lives inside the opened position detail card.  The user
# exports a Transaction History CSV from the IBKR Client Portal and drops it
# here.  Parsed trades are persisted to data/uploaded_trades.json (the same
# store used by the global trade history timeline) and plotted as BUY/SELL
# arrows on the per-position price chart.

@app.callback(
    Output('uploaded-trades', 'data'),
    Output({'type': 'position-upload-status', 'index': ALL}, 'children'),
    Input({'type': 'position-trade-upload', 'index': ALL}, 'contents'),
    State({'type': 'position-trade-upload', 'index': ALL}, 'filename'),
    prevent_initial_call=True,
)
def handle_position_trade_upload(contents_list, filenames_list):
    import base64

    contents = next((c for c in (contents_list or []) if c), None)
    filename = next((f for f in (filenames_list or []) if f), None)
    if not contents:
        return no_update, [no_update for _ in (contents_list or [])]

    def _status(msg, color):
        return html.Span(msg, style={'color': color, 'fontWeight': '500'})

    if not (filename or '').lower().endswith('.csv'):
        return no_update, [_status("Need a .csv file", '#dc2626')
                           for _ in (contents_list or [])]
    try:
        _, b64 = contents.split(',', 1)
        decoded = base64.b64decode(b64)
    except Exception:
        return no_update, [_status("Could not decode file", '#dc2626')
                           for _ in (contents_list or [])]

    parsed = parse_activity_csv(decoded)
    if not parsed:
        return no_update, [_status("No trades found in CSV", '#b45309')
                           for _ in (contents_list or [])]

    merged = save_uploaded_trades(parsed)
    msg = f"{len(parsed)} parsed · {len(merged)} total stored"
    return merged, [_status(msg, '#16a34a') for _ in (contents_list or [])]


# ── Dividends ─────────────────────────────────────────────────────────────────

@app.callback(
    Output('dividend-section', 'children'),
    Input('portfolio-data', 'data'),
    Input('refresh-interval', 'n_intervals'),
)
def update_dividends(data, *_):
    if not data or 'positions' not in data:
        return None

    positions = data['positions']
    div_data  = data.get('div_data', {})
    rate      = data.get('account', {}).get('eurusd_rate', 1.08)

    # Build per-position dividend enrichment
    div_positions = []
    for p in positions:
        sym  = p['ticker']
        d    = div_data.get(sym, {})
        n12  = d.get('next_12m')
        p12  = d.get('past_12m')
        price = p['current_price']
        qty   = p['quantity']
        if not (n12 or p12):
            continue
        annual_dps    = n12 or p12
        yield_pct     = round(annual_dps / price * 100, 2) if price else None
        annual_income = round(annual_dps * qty, 2)
        div_positions.append({
            'ticker':       sym,
            'yield_pct':    yield_pct,
            'annual_dps':   annual_dps,
            'annual_income':annual_income,
            'next_date':    d.get('next_date'),
            'next_amount':  d.get('next_amount'),
            'quantity':     qty,
        })

    annual_income = sum(p['annual_income'] for p in div_positions)

    if not div_positions:
        return html.Div(
            html.P("No dividend data — positions may not pay dividends or market data is unavailable.",
                   style={'fontSize': '15px', 'color': '#bbb', 'textAlign': 'center', 'padding': '24px 0'}),
            style=CARD)

    # ── Summary cards ────────────────────────────────────────────────────────
    def div_card(label, value, sub=None, color='#111'):
        return html.Div([
            html.P(label, style={'fontSize': '12px', 'color': '#999', 'margin': '0 0 6px',
                                 'textTransform': 'uppercase', 'letterSpacing': '0.05em',
                                 'fontWeight': '500'}),
            html.P(value, style={'fontSize': '20px', 'fontWeight': '600', 'margin': '0',
                                 'color': color, 'letterSpacing': '-0.5px'}),
            html.P(sub, style={'fontSize': '13px', 'color': '#888', 'margin': '3px 0 0'}) if sub else None,
        ], style={'background': '#fafafa', 'borderRadius': '12px', 'padding': '10px 14px',
                  'borderLeft': '3px solid #ebebeb'})

    portfolio_yield = round(annual_income / data['summary']['total_value'] * 100, 2) \
        if annual_income and data.get('summary', {}).get('total_value') else None

    summary_row = html.Div([
        div_card("Projected Annual Income", f"${annual_income:,.2f}",
                 sub=f"€{annual_income / rate:,.2f}"),
        div_card("Portfolio Yield",
                 f"{portfolio_yield:.2f}%" if portfolio_yield else "—",
                 sub="Based on next 12M dividends"),
    ], style={'display': 'grid', 'gridTemplateColumns': 'repeat(2, 1fr)',
              'gap': '12px', 'marginBottom': '20px'})

    # ── Per-position yield table ──────────────────────────────────────────────
    if div_positions:
        td_r = lambda v, **kw: html.Td(v, style={'textAlign': 'right', 'padding': '10px 12px', **kw})
        td_l = lambda v, **kw: html.Td(v, style={'textAlign': 'left',  'padding': '10px 12px', **kw})

        rows = []
        for p in sorted(div_positions, key=lambda x: x['annual_income'], reverse=True):
            nxt_date = p['next_date'] or '—'
            nxt_amt  = f"${p['next_amount']:,.4f}" if p['next_amount'] else '—'
            nxt_pay  = f"${p['next_amount'] * p['quantity']:,.2f}" \
                       if p['next_amount'] else '—'
            yield_color = '#16a34a' if (p['yield_pct'] or 0) >= 2 else '#111'
            rows.append(html.Tr([
                td_l(html.Span(p['ticker'], style={'fontWeight': '600', 'color': '#111'})),
                td_r(f"{p['yield_pct']:.2f}%" if p['yield_pct'] else '—', color=yield_color),
                td_r(f"${p['annual_dps']:,.4f}"),
                td_r(f"${p['annual_income']:,.2f}"),
                td_r(nxt_date, color='#666'),
                td_r(nxt_amt,  color='#666'),
                td_r(nxt_pay),
            ], style={'borderTop': '0.5px solid #f5f5f5'}))

        yield_table = html.Div([
            section_label("Yield per Position"),
            make_table(
                ['Ticker', 'Yield', 'Ann. DPS', 'Ann. Income', 'Next Ex-Date', 'Next Div/Share', 'Next Payout'],
                rows),
        ], style={'marginBottom': '20px'})
    else:
        yield_table = None

    return html.Div([
        section_label("Dividends"),
        summary_row,
        yield_table,
    ], style=CARD)





# ── Toast notifications ────────────────────────────────────────────────────────
# One callback covers all toast triggers.  Using a single Output avoids the
# "multiple callbacks with the same output" constraint in Dash.
#
# The child div receives a unique `key` (current timestamp) on every call so
# React unmounts and remounts it — which restarts the CSS animation even when
# the message text is identical to the previous one.

@app.callback(
    Output('toast', 'children'),
    Input('portfolio-data', 'data'),
    Input('export-pdf-btn', 'n_clicks'),
    prevent_initial_call=True,
)
def update_toast(*_):
    triggered = ctx.triggered_id
    ts = datetime.now().strftime('%H:%M:%S')
    messages = {
        'portfolio-data': f"Portfolio refreshed · {ts}",
        'export-pdf-btn': f"PDF downloaded · {ts}",
    }
    msg = messages.get(triggered)
    if not msg:
        return no_update
    return html.Div(
        msg,
        key=str(time.time()),
        className='toast-msg',
        style={
            'background': '#111', 'color': '#fff',
            'padding': '10px 18px', 'borderRadius': '12px',
            'fontSize': '15px', 'fontWeight': '500',
            'letterSpacing': '0.01em',
            'boxShadow': '0 4px 20px rgba(0,0,0,0.18)',
            'whiteSpace': 'nowrap',
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET INTELLIGENCE CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════════

# ── Populate market-intel store ────────────────────────────────────────────────
# Fires whenever portfolio-data updates. Calls yfinance-backed functions in
# parallel (sector/geo + earnings concurrently) so cold-start latency is
# bounded by the slower of the two rather than their sum (~10-30 s vs 20-60 s).
#
# The _last_intel_tickers guard returns no_update when the ticker list hasn't
# changed since the last call, preventing all four downstream rendering
# callbacks from unnecessarily rebuilding charts on every 60-second refresh.

_last_intel_tickers: tuple | None = None


@app.callback(
    Output('market-intel-data', 'data'),
    Input('portfolio-data', 'data'),
)
def populate_market_intel(data):
    global _last_intel_tickers
    if not data or 'positions' not in data:
        _last_intel_tickers = None
        return None

    tickers    = [p['ticker'] for p in data['positions']]
    ticker_key = tuple(sorted(tickers))

    # If tickers haven't changed the 4-hour cached values are still valid —
    # skip the fetch and leave the store (and all downstream renders) unchanged.
    # NOTE: _last_intel_tickers is only set AFTER a successful fetch so that a
    # failed/timed-out fetch is retried on the next 60-second refresh instead of
    # being silently skipped forever via no_update.
    if ticker_key == _last_intel_tickers:
        return no_update

    parallel = run_parallel({
        'sector_geo': lambda: get_sector_geo(tickers),
        'earnings':   lambda: get_earnings_data(tickers),
    })
    result = {
        'tickers':    tickers,
        'sector_geo': parallel.get('sector_geo') or {},
        'earnings':   parallel.get('earnings')   or {},
    }

    # Only mark tickers as done AFTER the fetch completes.  Setting this before
    # the fetch meant a failed fetch would permanently skip retries for the same
    # ticker set (no_update returned on every subsequent 60-second refresh).
    _last_intel_tickers = ticker_key
    return result


# ── Shared loading placeholder ─────────────────────────────────────────────────

def _intel_loading(label: str):
    return html.Div(
        html.P(f"Loading {label}…",
               style={'fontSize': '15px', 'color': '#bbb',
                      'textAlign': 'center', 'padding': '32px 0', 'margin': '0'}),
        style=CARD,
    )


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _intel_error(label: str, err: Exception):
    log.error("Market intel render error (%s): %s", label, err, exc_info=False)
    return html.Div(
        html.P(f"{label} temporarily unavailable — will retry on next refresh.",
               style={'fontSize': '15px', 'color': '#b45309',
                      'textAlign': 'center', 'padding': '24px 0', 'margin': '0'}),
        style={**CARD, 'background': '#fffbeb', 'borderLeft': '3px solid #fde68a'},
    )


# ── 1. Sector & Geography ──────────────────────────────────────────────────────
# Sector and country breakdown weighted by portfolio allocation_pct.
# Two charts side-by-side: sector donut on the left, country bar on the right.
# Below: a compact table listing which tickers belong to which sector.

@app.callback(
    Output('sector-geo-section', 'children'),
    Input('market-intel-data', 'data'),
    State('portfolio-data', 'data'),
)
def render_sector_geo(intel, port_data):
    try:
        return _render_sector_geo_inner(intel, port_data)
    except Exception as e:
        return _intel_error("Sector & Geography", e)


def _render_sector_geo_inner(intel, port_data):
    if not intel:
        return _intel_loading('sector & geography data')
    if not port_data or 'positions' not in port_data:
        return None

    sg = intel.get('sector_geo', {})
    positions = port_data['positions']

    # Build weighted sector and country aggregates.
    # ETFs with a Yahoo Finance sector_weights breakdown are distributed
    # across real sectors (Technology, Financials, …) proportionally to
    # their holdings — so an S&P 500 ETF contributes ~30% Technology,
    # ~15% Financials, etc. instead of a single 'ETF / Fund' slice.
    # If the ETF returned no sector_weights (e.g. a bond ETF, or yfinance
    # had no data), we fall back to the raw `sector` field as before.
    sector_val:     dict = {}
    country_val:    dict = {}
    sector_tickers: dict = {}

    for p in positions:
        sym  = p['ticker']
        val  = p['market_value']
        info = sg.get(sym, {})
        cty  = info.get('country', 'Unknown')
        country_val[cty] = country_val.get(cty, 0) + val

        weights = info.get('sector_weights') or {}
        if info.get('is_etf') and weights:
            wsum = sum(weights.values()) or 1.0
            for sec, w in weights.items():
                share = val * (w / wsum)
                sector_val[sec] = sector_val.get(sec, 0) + share
                sector_tickers.setdefault(sec, []).append(sym)
        else:
            sec = info.get('sector', 'Unknown')
            sector_val[sec]  = sector_val.get(sec, 0) + val
            sector_tickers.setdefault(sec, []).append(sym)

    # De-duplicate ticker lists (an ETF can hit the same sector twice if
    # its yfinance weights returned both 'realestate' and 'real_estate').
    for sec, tks in sector_tickers.items():
        seen: list = []
        for t in tks:
            if t not in seen:
                seen.append(t)
        sector_tickers[sec] = seen

    total = sum(sector_val.values()) or 1

    # ── Sector donut ────────────────────────────────────────────────────────
    # Donut shows every sector (full breakdown).  Legend below hides anything
    # under MIN_SECTOR_PCT to keep the list readable — sub-threshold sectors
    # are folded into an 'Other' row there, not in the donut.
    MIN_SECTOR_PCT = 5.0

    # Full sorted list for the donut
    sec_full_sorted = sorted(sector_val.items(), key=lambda x: x[1], reverse=True)
    sec_labels = [s[0] for s in sec_full_sorted]
    sec_values = [s[1] for s in sec_full_sorted]

    colors = ['#378ADD', '#f97316', '#a855f7', '#22c55e',
              '#eab308', '#ec4899', '#14b8a6', '#6366f1',
              '#84cc16', '#ef4444', '#06b6d4']
    sec_colors = [colors[i % len(colors)] for i in range(len(sec_labels))]
    # Color lookup shared between the donut and the legend so matching
    # sectors always use the same dot color.
    color_by_sec = dict(zip(sec_labels, sec_colors, strict=True))

    # Portfolio total for the donut center label.
    # market_value comes from IBKR in USD for US positions — convert to EUR
    # (matching the Total Value summary card) before formatting with the € sign.
    rate = (port_data.get('account') or {}).get('eurusd_rate') or 1.08
    total_val_raw = (port_data.get('summary') or {}).get('total_value') or total
    total_val_eur = to_eur(total_val_raw, rate)
    if total_val_eur >= 1_000_000:
        center_val = f"€{total_val_eur/1_000_000:.2f}M"
    elif total_val_eur >= 1_000:
        center_val = f"€{total_val_eur/1_000:.1f}K"
    else:
        center_val = f"€{total_val_eur:,.0f}"

    sec_values_eur = [to_eur(v, rate) for v in sec_values]
    donut = go.Figure(go.Pie(
        labels=sec_labels, values=sec_values, hole=0.62,
        textposition='none', sort=False,
        marker=dict(colors=sec_colors),
        customdata=sec_values_eur,
        hovertemplate='<b>%{label}</b><br>%{percent:.1%}  ·  €%{customdata:,.0f}<extra></extra>',
    ))
    donut.update_layout(
        margin=dict(t=0, b=0, l=0, r=0),
        showlegend=False,
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        height=200,
        annotations=[dict(
            text=(f"<span style='font-size:18px;color:#111;font-weight:600'>"
                  f"{center_val}</span><br>"
                  f"<span style='font-size:11px;color:#888;"
                  f"letter-spacing:0.05em;text-transform:uppercase'>Portfolio</span>"),
            x=0.5, y=0.5, showarrow=False, align='center',
        )],
    )

    # ── Country chart ────────────────────────────────────────────────────────
    # When the portfolio has ≤2 countries a horizontal bar looks silly
    # (one giant stripe), so fall back to the same donut style as Sector.
    cty_sorted = sorted(country_val.items(), key=lambda x: x[1], reverse=True)[:8]
    cty_labels = [c[0] for c in cty_sorted]
    cty_values = [c[1] / total * 100 for c in cty_sorted]

    country_colors = ['#378ADD', '#22c55e', '#f97316', '#a855f7',
                      '#eab308', '#ec4899', '#14b8a6', '#6366f1']

    if len(cty_labels) <= 2:
        bar = go.Figure(go.Pie(
            labels=cty_labels, values=cty_values, hole=0.62,
            textposition='none',
            marker=dict(colors=country_colors[:len(cty_labels)]),
            hovertemplate='<b>%{label}</b><br>%{percent:.1%}<extra></extra>',
        ))
        center_text = (f"{cty_values[0]:.0f}%<br>"
                       f"<span style='font-size:11px;color:#888'>{cty_labels[0]}</span>"
                       if len(cty_labels) == 1 else '')
        bar.update_layout(
            margin=dict(t=0, b=0, l=0, r=0),
            showlegend=(len(cty_labels) > 1),
            legend=dict(orientation='h', yanchor='bottom', y=-0.15,
                        xanchor='center', x=0.5, font=dict(size=11)),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            height=200,
            annotations=[dict(text=center_text, x=0.5, y=0.5,
                              font=dict(size=20, color='#333'),
                              showarrow=False)] if center_text else [],
        )
    else:
        bar = go.Figure(go.Bar(
            x=cty_values, y=cty_labels,
            orientation='h',
            marker=dict(color='#378ADD', opacity=0.75),
            hovertemplate='%{y}: <b>%{x:.1f}%</b><extra></extra>',
            text=[f'{v:.1f}%' for v in cty_values],
            textposition='outside',
            textfont=dict(size=11, color='#555'),
        ))
        bar.update_layout(
            margin=dict(t=0, b=0, l=0, r=80),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(visible=False),
            yaxis=dict(tickfont=dict(size=11), autorange='reversed'),
            height=200,
        )

    # ── Sector breakdown legend table ────────────────────────────────────────
    # Only sectors ≥MIN_SECTOR_PCT shown as rows; everything smaller is
    # rolled into a single 'Other' row so the list stays scannable.
    # Colors match the donut slices (via color_by_sec).  'Other' is grey.
    legend_sectors: list = []
    other_val = 0.0
    other_names: list = []
    for sec, val in sec_full_sorted:
        if (val / total * 100) >= MIN_SECTOR_PCT:
            legend_sectors.append((sec, val, color_by_sec[sec]))
        else:
            other_val += val
            other_names.append(sec)
    if other_val / total * 100 >= 1.0:
        other_tickers = sorted({
            t for s in other_names for t in sector_tickers.get(s, [])
        })
        sector_tickers['Other'] = other_tickers
        legend_sectors.append(('Other', other_val, '#b8b8b8'))

    legend_rows = []
    for sec, val, dot_color in legend_sectors:
        pct = val / total * 100
        tickers_str = ', '.join(sorted(sector_tickers.get(sec, [])))
        legend_rows.append(html.Tr([
            html.Td(html.Div(style={
                'width': '8px', 'height': '8px', 'borderRadius': '50%',
                'background': dot_color, 'display': 'inline-block',
            }), style={'padding': '6px 8px 6px 0', 'width': '17px'}),
            html.Td(sec,         style={'padding': '6px 12px 6px 0',
                                        'fontSize': '15px', 'fontWeight': '500'}),
            html.Td(f'{pct:.1f}%', style={'padding': '6px 12px', 'textAlign': 'right',
                                           'fontSize': '15px', 'color': '#555'}),
            html.Td(tickers_str,   style={'padding': '6px 0', 'fontSize': '14px',
                                           'color': '#888'}),
        ], style={'borderTop': '0.5px solid #f5f5f5'}))

    sector_legend = html.Table(
        [html.Tbody(legend_rows)],
        style={'width': '100%', 'borderCollapse': 'collapse', 'marginTop': '17px'},
    )

    country_is_donut = len(cty_labels) <= 2

    return html.Div([
        section_label("Sector & Geography Exposure"),
        html.Div([
            # Left: sector donut
            html.Div([
                html.P("Sector", style={'fontSize': '14px', 'color': '#555',
                                        'textTransform': 'uppercase',
                                        'letterSpacing': '0.04em', 'margin': '0 0 8px'}),
                dcc.Graph(figure=donut, config={'displayModeBar': False}),
            ], style={'flex': '2', 'minWidth': '200px'}),

            # Right: country chart (label top-left like Sector)
            html.Div([
                html.P("Country", style={'fontSize': '14px', 'color': '#555',
                                          'textTransform': 'uppercase',
                                          'letterSpacing': '0.04em',
                                          'margin': '0 0 8px'}),
                html.Div(
                    dcc.Graph(figure=bar, config={'displayModeBar': False}),
                    style={'maxWidth': '280px'} if country_is_donut else {},
                ),
            ], style={'flex': '2' if country_is_donut else '3',
                      'minWidth': '200px'}),
        ], style={'display': 'flex', 'gap': '24px', 'flexWrap': 'wrap',
                  'alignItems': 'flex-start'}),
        sector_legend,
    ], style=CARD)


# ── 3. Earnings calendar ───────────────────────────────────────────────────────
# Shows the next earnings date for each equity holding.  ETFs are skipped.
# The avg post-earnings move column gives a sense of how volatile each stock
# typically is around earnings — a genuinely novel view vs the IBKR portal.

@app.callback(
    Output('earnings-section', 'children'),
    Input('market-intel-data', 'data'),
    State('portfolio-data', 'data'),
)
def render_earnings(intel, port_data):
    try:
        return _render_earnings_inner(intel, port_data)
    except Exception as e:
        return _intel_error("Earnings", e)


def _render_earnings_inner(intel, port_data):
    if not intel:
        return _intel_loading('earnings data')

    earnings = intel.get('earnings', {})
    positions = (port_data or {}).get('positions', [])
    alloc_map = {p['ticker']: p['allocation_pct'] for p in positions}

    today = date.today()

    rows_data = []
    for sym, e in earnings.items():
        if not e.get('next_date'):
            continue
        try:
            earn_date = date.fromisoformat(e['next_date'])
        except Exception:
            continue
        days = (earn_date - today).days
        rows_data.append({
            'ticker':    sym,
            'next_date': e['next_date'],
            'days':      days,
            'avg_move':  e.get('avg_1d_move'),
            'moves':     e.get('last_1d_moves', []),
            'weight':    alloc_map.get(sym, 0),
        })

    if not rows_data:
        return html.Div(
            html.P("No upcoming earnings dates found — holdings may be ETFs or "
                   "data is unavailable.",
                   style={'fontSize': '15px', 'color': '#bbb',
                          'textAlign': 'center', 'padding': '24px 0'}),
            style=CARD)

    rows_data.sort(key=lambda x: x['days'])

    td_l = lambda v, **kw: html.Td(v, style={'padding': '10px 12px',
                                               'textAlign': 'left', **kw})
    td_r = lambda v, **kw: html.Td(v, style={'padding': '10px 12px',
                                               'textAlign': 'right', **kw})

    table_rows = []
    for r in rows_data:
        days      = r['days']
        imminent  = days <= 14
        soon      = days <= 30

        if days < 0:
            days_str  = f"{abs(days)}d ago"
            days_color = '#bbb'
        elif days == 0:
            days_str  = 'Today'
            days_color = '#dc2626'
        else:
            days_str  = f'in {days}d'
            days_color = '#dc2626' if imminent else ('#b45309' if soon else '#888')

        weight_str = f"{r['weight']:.1f}%"
        row_bg     = '#fff8f0' if imminent else 'transparent'

        table_rows.append(html.Tr([
            td_l(html.Span(r['ticker'], style={'fontWeight': '600'})),
            td_r(r['next_date'], color='#555'),
            td_r(days_str,       color=days_color,
                 fontWeight='600' if imminent else '400'),
            td_r(weight_str,     color='#555'),
        ], style={'borderTop': '0.5px solid #f5f5f5',
                  'backgroundColor': row_bg}))

    table = make_table(
        ['Ticker', 'Earnings Date', 'When', 'Weight'],
        table_rows)

    return html.Div([
        section_label("Earnings Calendar"),
        table,
    ], style=CARD)


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET VALUATION CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════════

@app.callback(
    Output('valuation-data', 'data'),
    Input('refresh-interval', 'n_intervals'),
)
def populate_valuation_data(_):
    """
    Fetch all three valuation metrics in parallel and store them.
    Each getter has its own 4-hour cache, so real network calls are rare.
    On cold start the three HTTP requests (World Bank, multpl.com x2) run
    concurrently — total latency is bounded by the slowest one, not all three.
    Failures are isolated per metric; one failing doesn't block the others.
    """
    return run_parallel({
        'buffett':  get_buffett_indicator,
        'sp500_pe': get_sp500_pe,
        'cape':     get_shiller_cape,
        'treasury': get_treasury_yield,
    })


@app.callback(
    Output('market-valuation-section', 'children'),
    Input('valuation-data', 'data'),
)
def render_market_valuation(data):
    try:
        return _render_market_valuation_inner(data)
    except Exception as e:
        return _intel_error("Market Valuation", e)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _val_zone_bar(value: float, segments: list, display_max: float):
    """
    Horizontal colour-band bar with a dark needle at the current value.
    Labels are rendered below the bar so they are never clipped.

    segments: [(label, upper_bound, hex_color), ...]
              The last segment's upper bound is display_max.
    """
    clamped = min(max(value, 0), display_max)
    needle_pct = clamped / display_max * 100

    # Colour bands — no text inside, never any clipping
    seg_divs = []
    prev = 0.0
    for _label, seg_max, color in segments:
        width_pct = (seg_max - prev) / display_max * 100
        seg_divs.append(html.Div(style={
            'width': f'{width_pct}%', 'height': '16px',
            'backgroundColor': color, 'flexShrink': '0',
        }))
        prev = seg_max

    bar = html.Div(seg_divs, style={
        'display': 'flex', 'borderRadius': '5px',
        'overflow': 'hidden', 'height': '16px',
    })

    needle = html.Div(style={
        'position': 'absolute',
        'left': f'calc({needle_pct:.2f}% - 1.5px)',
        'top': '-4px', 'width': '3px', 'height': '24px',
        'backgroundColor': '#111', 'borderRadius': '2px', 'zIndex': '10',
    })

    # Zone labels below the bar — flex cells mirror the colour bands so
    # labels can never overlap.  Suppress text in cells narrower than 13 %.
    label_cells = []
    prev = 0.0
    for label, seg_max, color in segments:
        width_pct = (seg_max - prev) / display_max * 100
        label_cells.append(html.Div(
            label if width_pct >= 13 else None,
            style={
                'width': f'{width_pct:.2f}%',
                'flexShrink': '0',
                'textAlign': 'center',
                'fontSize': '10px',
                'color': color,
                'fontWeight': '600',
                'whiteSpace': 'nowrap',
                'overflow': 'hidden',
            },
        ))
        prev = seg_max

    labels_row = html.Div(label_cells, style={
        'display': 'flex',
        'height': '15px',
        'marginTop': '4px',
    })

    return html.Div([
        html.Div([bar, needle], style={'position': 'relative', 'marginTop': '12px'}),
        labels_row,
    ])


def _val_unavailable():
    return html.Div([
        html.P("—", style={'fontSize': '28px', 'fontWeight': '600',
                           'color': '#ddd', 'margin': '0 0 4px'}),
        html.Span("Data unavailable",
                  style={'fontSize': '13px', 'color': '#bbb'}),
    ])


def _render_market_valuation_inner(data):
    if data is None:
        return _intel_loading('valuation data')

    buffett_d  = data.get('buffett')
    pe_d       = data.get('sp500_pe')
    cape_d     = data.get('cape')
    treasury_d = data.get('treasury')

    # ── Card builder ──────────────────────────────────────────────────────────
    def metric_card(title, subtitle, body, footer=None):
        return html.Div([
            html.P(title, style={
                'fontSize': '13px', 'color': '#555', 'margin': '0 0 2px',
                'textTransform': 'uppercase', 'letterSpacing': '0.06em', 'fontWeight': '600',
            }),
            html.P(subtitle, style={
                'fontSize': '13px', 'color': '#888', 'margin': '0 0 16px',
            }),
            body,
            html.P(footer, style={
                'fontSize': '13px', 'color': '#777', 'margin': '14px 0 0',
                'lineHeight': '1.5',
            }) if footer else None,
        ], style={**CARD, 'flex': '1'})

    def zone_badge(label, color):
        bg      = color + '18'   # 10% opacity hex
        border  = color + '55'   # 33% opacity hex
        return html.Span(label, style={
            'fontSize': '14px', 'fontWeight': '600', 'color': color,
            'background': bg, 'border': f'0.5px solid {border}',
            'padding': '3px 10px', 'borderRadius': '20px',
            'display': 'inline-block', 'marginBottom': '8px',
        })

    def big_value(text, color='#111'):
        return html.P(text, style={
            'fontSize': '36px', 'fontWeight': '700', 'margin': '0 0 6px',
            'letterSpacing': '-1px', 'color': color,
        })

    # ── 1. Buffett Indicator ──────────────────────────────────────────────────
    if buffett_d:
        bv     = buffett_d['value']
        blabel, bcolor = buffett_zone(bv)
        # How far above the modern ~127% trend line we are
        modern_trend    = 127
        above_trend_pct = round(bv - modern_trend, 0)

        b_body = html.Div([
            big_value(f'{bv:.1f}%', bcolor),
            zone_badge(blabel, bcolor),
            _val_zone_bar(bv, [
                ('Well Below Norms',   75,  '#16a34a'),
                ('Fairly Valued',     110,  '#22c55e'),
                ('Modern',            150,  '#84cc16'),
                ('Hot',               190,  '#f97316'),
                ('Stretched',         280,  '#dc2626'),
            ], display_max=280),
            html.Div([
                html.Span(f"Mkt Cap  ${buffett_d['market_cap_t']:.1f}T",
                          style={'fontSize': '13px', 'color': '#666'}),
                html.Span('  \u00b7  ', style={'color': '#bbb', 'fontSize': '13px'}),
                html.Span(f"GDP  ${buffett_d['gdp_t']:.1f}T",
                          style={'fontSize': '13px', 'color': '#999'}),
            ], style={'marginTop': '8px'}),
            html.Div([
                html.Span(
                    f'The market is {bv/100:.1f}\u00d7 the size of the US economy'
                    f' \u2014 {above_trend_pct:.0f}% above the modern trend line.',
                    style={'fontSize': '12px', 'color': '#555', 'fontStyle': 'italic'},
                ),
                html.Br(),
                html.Span(
                    f'GDP as of {buffett_d["gdp_quarter"]} ({buffett_d["gdp_source"]})'
                    ' \u2014 1\u20132 quarter lag is normal.',
                    style={'fontSize': '11px', 'color': '#bbb'},
                ),
            ], style={'marginTop': '6px'}),
        ])
        b_foot = (
            'Buffett: \u201cThe best single measure of where valuations stand at any given moment.\u201d '
        )
    else:
        b_body = _val_unavailable()
        b_foot = None

    # ── 2. S&P 500 P/E ratio ─────────────────────────────────────────────────
    if pe_d:
        trailing = pe_d.get('trailing_pe')
        forward  = pe_d.get('forward_pe')
        main_val = trailing or forward
        main_lbl = 'Trailing P/E' if trailing else 'Forward P/E'

        if main_val:
            plabel, pcolor = pe_zone(main_val)
            pe_body = html.Div([
                big_value(f'{main_val:.1f}×', pcolor),
                zone_badge(plabel, pcolor),
                _val_zone_bar(main_val, [
                    ('Cheap',              15, '#16a34a'),
                    ('Fairly Valued',      20, '#22c55e'),
                    ('Expensive',          25, '#eab308'),
                    ('Very Expensive',     30, '#f97316'),
                    ('Extremely Exp.',     45, '#dc2626'),
                ], display_max=45),
                html.Div([
                    html.Span(f'{main_lbl}: {main_val:.1f}×',
                              style={'fontSize': '13px', 'color': '#666'}),
                    (html.Span(f'  ·  Forward: {forward:.1f}×',
                               style={'fontSize': '13px', 'color': '#666'})
                     if forward and trailing else None),
                ], style={'marginTop': '8px'}),
            ])
        else:
            pe_body = _val_unavailable()

        pe_foot = (
            'Compares the S&P 500 price to its aggregate earnings. '
            'Long-run average: ~16×. Above 25× historically correlates '
            'with below-average 10-year forward returns.'
        )
    else:
        pe_body = _val_unavailable()
        pe_foot = None

    # ── 3. Shiller CAPE ───────────────────────────────────────────────────────
    if cape_d:
        cv     = cape_d['value']
        clabel, ccolor = cape_zone(cv)
        cape_body = html.Div([
            big_value(f'{cv:.1f}×', ccolor),
            zone_badge(clabel, ccolor),
            _val_zone_bar(cv, [
                ('Undervalued',       15, '#16a34a'),
                ('Fairly Valued',     20, '#22c55e'),
                ('Overvalued',        25, '#eab308'),
                ('Highly Overval.',   30, '#f97316'),
                ('Extremely Overv.',  50, '#dc2626'),
            ], display_max=50),
            html.Div([
                html.Span(f"Hist. mean {cape_d['hist_mean']:.1f}×",
                          style={'fontSize': '13px', 'color': '#666'}),
                html.Span(' · ', style={'color': '#bbb', 'fontSize': '13px'}),
                html.Span(f"Median {cape_d['hist_median']:.1f}×",
                          style={'fontSize': '13px', 'color': '#666'}),
                html.Span(f"  ·  as of {cape_d['last_date']}",
                          style={'fontSize': '13px', 'color': '#888'}),
            ], style={'marginTop': '8px'}),
        ])
        cape_foot = (
            'Uses 10 years of inflation-adjusted earnings to smooth short-term noise. '
            f'100-year average: ~{cape_d["hist_mean"]:.0f}\u00d7. '
            'The modern (20-year) average is ~25\u00d7, reflecting higher structural '
            'valuations since the tech era. The chart marks major crashes to show that '
            'elevated readings did eventually matter \u2014 just not on a fixed timeline.'
        )
    else:
        cape_body = _val_unavailable()
        cape_foot = None

    cards = html.Div([
        metric_card('Buffett Indicator',
                    'Stock market size vs the economy',
                    b_body, b_foot),
        metric_card('S&P 500 P/E Ratio',
                    'How much you pay per $1 of profit',
                    pe_body, pe_foot),
        metric_card('Shiller CAPE',
                    'P/E smoothed over 10 years (more reliable)',
                    cape_body, cape_foot),
    ], style={'display': 'flex', 'gap': '14px', 'alignItems': 'stretch'})

    # ── Yield Gap ─────────────────────────────────────────────────────────────
    # Ties the P/E and Treasury yield together into a single verdict:
    #   Earnings Yield (= 1/PE) tells you what stocks "pay" per dollar invested.
    #   Yield Gap = Earnings Yield - 10yr Bond Yield.
    #   Positive → stocks still offer more than "safe" bonds.
    #   Negative → bonds pay more than stocks earn → valuations hard to justify.
    trailing_pe = pe_d.get('trailing_pe') if pe_d else None
    tv          = treasury_d['value']     if treasury_d else None

    if trailing_pe and tv:
        earnings_yield = round(100 / trailing_pe, 2)
        gap            = round(earnings_yield - tv, 2)

        if   gap >  2:   gap_label, gap_color = 'Stocks strongly favoured',  '#16a34a'
        elif gap >  0:   gap_label, gap_color = 'Stocks slightly favoured',   '#22c55e'
        elif gap > -1:   gap_label, gap_color = 'Roughly equal',              '#eab308'
        elif gap > -2:   gap_label, gap_color = 'Bonds competitive',          '#f97316'
        else:            gap_label, gap_color = 'Bonds clearly favoured',     '#dc2626'

        gap_sign = '+' if gap >= 0 else ''

        context_note = html.Div([
            html.Div([
                # Left: formula breakdown
                html.Div([
                    html.Span('Yield Gap', style={
                        'fontWeight': '700', 'fontSize': '13px', 'color': '#333',
                        'display': 'block', 'marginBottom': '4px',
                    }),
                    html.Span(
                        f'S&P earnings yield ({earnings_yield:.2f}%) '
                        f'\u2212 10-yr bond yield ({tv:.2f}%)',
                        style={'fontSize': '14px', 'color': '#555'},
                    ),
                ]),
                # Right: result
                html.Div([
                    html.Span(f'{gap_sign}{gap:.2f}%', style={
                        'fontSize': '22px', 'fontWeight': '700',
                        'color': gap_color, 'marginRight': '10px',
                    }),
                    html.Span(gap_label, style={
                        'fontSize': '13px', 'fontWeight': '600',
                        'color': gap_color,
                        'background': gap_color + '18',
                        'padding': '3px 10px', 'borderRadius': '99px',
                    }),
                ], style={'display': 'flex', 'alignItems': 'center'}),
            ], style={
                'display': 'flex', 'justifyContent': 'space-between',
                'alignItems': 'center',
            }),
            html.Div(
                'When this number is positive, stocks are earning more per dollar than '
                'government bonds \u2014 a sign investors are still being rewarded for '
                'the extra risk. When it turns negative, bonds pay more than stocks earn, '
                'which makes expensive valuations harder to justify.',
                style={'fontSize': '14px', 'color': '#555',
                       'marginTop': '8px', 'lineHeight': '1.5'},
            ),
        ], style={
            'background': '#f8f9fa', 'borderLeft': f'3px solid {gap_color}',
            'padding': '12px 16px', 'borderRadius': '4px', 'marginTop': '18px',
        })
    else:
        context_note = None

    # ── CAPE historical chart ─────────────────────────────────────────────────
    if cape_d and cape_d.get('dates'):
        mean_val   = cape_d['hist_mean']
        dates_plot = cape_d['dates']
        vals_plot  = cape_d['values']

        # Modern mean: last 20 years = last 240 monthly data points
        modern_slice  = vals_plot[-240:] if len(vals_plot) >= 240 else vals_plot
        modern_mean   = round(sum(modern_slice) / len(modern_slice), 1)

        fig = go.Figure()

        # Zone background bands
        bands = [
            (0,   15, 'rgba(22,163,74,0.20)'),
            (15,  20, 'rgba(34,197,94,0.20)'),
            (20,  25, 'rgba(234,179,8,0.20)'),
            (25,  30, 'rgba(249,115,22,0.20)'),
            (30,  55, 'rgba(220,38,38,0.20)'),
        ]
        for y0, y1, fill in bands:
            fig.add_hrect(y0=y0, y1=y1, fillcolor=fill,
                          layer='below', line_width=0)

        # Major crash / drawdown periods
        crashes = [
            ('1987-08', '1987-12', 'Black Monday',    'top left'),
            ('2000-03', '2002-10', 'Dot-com bust',    'top left'),
            ('2007-10', '2009-03', 'Financial crisis','top left'),
            ('2020-02', '2020-04', 'COVID crash',     'top right'),
            ('2022-01', '2022-10', '2022 bear',       'top left'),
        ]
        for x0, x1, label, apos in crashes:
            if x0 >= dates_plot[0]:
                fig.add_vrect(
                    x0=x0, x1=x1,
                    fillcolor='rgba(100,100,100,0.10)',
                    layer='below', line_width=0,
                    annotation_text=f'<b>{label}</b>',
                    annotation_position=apos,
                    annotation_font=dict(size=11, color='#777'),
                )

        # All-time historical mean line (dotted, grey)
        fig.add_hline(y=mean_val, line_dash='dot',
                      line_color='#bbb', line_width=1,
                      annotation_text=f'100-yr mean {mean_val:.0f}\u00d7',
                      annotation_position='top left',
                      annotation_font=dict(size=10, color='#bbb'))

        # Modern mean line (dashed, blue-grey) — last 20 years
        fig.add_hline(y=modern_mean, line_dash='dash',
                      line_color='#378ADD', line_width=1,
                      annotation_text=f'20-yr mean {modern_mean:.0f}\u00d7',
                      annotation_position='bottom left',
                      annotation_font=dict(size=10, color='#378ADD'))

        # CAPE line
        fig.add_trace(go.Scatter(
            x=dates_plot, y=vals_plot,
            mode='lines',
            line=dict(color='#378ADD', width=2),
            name='Shiller CAPE',
            hovertemplate='%{x}  \u00b7  CAPE <b>%{y:.1f}\u00d7</b><extra></extra>',
        ))

        # Current value dot
        fig.add_trace(go.Scatter(
            x=[dates_plot[-1]], y=[vals_plot[-1]],
            mode='markers+text',
            marker=dict(color=ccolor if cape_d else '#378ADD', size=10,
                        line=dict(color='#fff', width=2)),
            text=[f'  {vals_plot[-1]:.1f}\u00d7'],
            textposition='middle right',
            textfont=dict(size=11, color=ccolor if cape_d else '#378ADD'),
            showlegend=False,
            hoverinfo='skip',
        ))

        fig.update_layout(
            margin=dict(t=8, b=8, l=0, r=40),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            showlegend=False,
            hovermode='x unified',
            xaxis=dict(showgrid=False, zeroline=False,
                       tickfont=dict(size=10, color='#bbb'),
                       tickangle=-30),
            yaxis=dict(showgrid=True, gridcolor='#f5f5f5', zeroline=False,
                       tickfont=dict(size=10, color='#bbb'),
                       ticksuffix='\u00d7', title=None),
            height=260,
        )

        cape_chart = html.Div([
            html.P("Shiller CAPE \u2014 50-year history", style={
                'fontSize': '14px', 'color': '#000', 'margin': '20px 0 4px',
                'textTransform': 'uppercase', 'letterSpacing': '0.05em',
            }),
            html.P([
                html.Span("\u25ae Undervalued (<15)",        style={'color': '#16a34a'}),
                html.Span("  \u00b7  ", style={'color': '#bbb'}),
                html.Span("\u25ae Fairly Valued (15\u201320)",  style={'color': '#22c55e'}),
                html.Span("  \u00b7  ", style={'color': '#bbb'}),
                html.Span("\u25ae Overvalued (20\u201325)",      style={'color': '#eab308'}),
                html.Span("  \u00b7  ", style={'color': '#bbb'}),
                html.Span("\u25ae Highly Overvalued (25\u201330)", style={'color': '#f97316'}),
                html.Span("  \u00b7  ", style={'color': '#bbb'}),
                html.Span("\u25ae Extremely Overvalued (30+)",  style={'color': '#dc2626'}),
            ], style={'fontSize': '14px', 'margin': '0 0 6px'}),
            dcc.Graph(figure=fig, config={'displayModeBar': False}),
        ])
    else:
        cape_chart = None

    return html.Div([
        section_label("Market Valuation"),
        cards,
        context_note,
        cape_chart,
    ], style=CARD)


# ═══════════════════════════════════════════════════════════════════════════════
# AI COACH
# ═══════════════════════════════════════════════════════════════════════════════
#
# Single unified panel shown below Holdings when the ✨ Ask button is clicked.
# Top section: 5 rules-based scenarios from coach.py — pure Python, no network.
# Bottom section: optional API key unlocks 6 deeper preset questions + a
# free-form "ask anything" input. Key stored in browser localStorage only.

_COACH_BTN = {
    'fontSize': '13px', 'color': '#555', 'background': '#f5f5f5',
    'border': '0.5px solid #ddd', 'borderRadius': '8px',
    'padding': '6px 14px', 'cursor': 'pointer',
    'transition': 'background 120ms ease, color 120ms ease',
}

_COACH_BTN_PRIMARY = {
    **_COACH_BTN,
    'background': '#111', 'color': '#fff', 'border': '0.5px solid #111',
}

_COACH_INPUT = {
    'width': '100%', 'padding': '8px 10px', 'fontSize': '14px',
    'border': '0.5px solid #ddd', 'borderRadius': '8px', 'marginBottom': '8px',
}

_COACH_SECTION_LABEL = {
    'fontSize': '12px', 'color': '#888', 'margin': '0 0 10px',
    'textTransform': 'uppercase', 'letterSpacing': '0.08em', 'fontWeight': '600',
}


# ── Thread helpers ────────────────────────────────────────────────────────────
# A "thread" is one named conversation:
#   {id: str, title: str, created: iso, history: [{q, a, error?, followups?}]}
# Threads are persisted in browser localStorage via the coach-threads store.

def _new_thread(history: list | None = None) -> dict:
    h = list(history or [])
    return {
        'id':      uuid.uuid4().hex[:12],
        'title':   _thread_title(h),
        'created': datetime.utcnow().isoformat() + 'Z',
        'history': h,
    }


def _thread_title(history: list) -> str:
    if not history:
        return 'New chat'
    first_q = (history[0].get('q') or '').strip().replace('\n', ' ')
    if not first_q:
        return 'New chat'
    return first_q[:40] + ('…' if len(first_q) > 40 else '')


def _find_thread(threads: list, thread_id: str | None) -> dict | None:
    if not thread_id:
        return None
    for t in threads or []:
        if t.get('id') == thread_id:
            return t
    return None


def _active_history(threads: list, active_id: str | None) -> list:
    t = _find_thread(threads, active_id)
    return list(t.get('history') or []) if t else []


def _commit_history(threads: list, active_id: str | None,
                    history: list) -> tuple[list, str]:
    """Write `history` into the active thread, creating one if needed.
    Returns (threads, active_id).  Auto-titles from the first user message."""
    threads = list(threads or [])
    t = _find_thread(threads, active_id)
    if t is None:
        t = _new_thread(history)
        threads.insert(0, t)
        active_id = t['id']
    else:
        t['history'] = list(history)
        # Only retitle if the title is still the default placeholder.
        if t.get('title', 'New chat') in ('New chat', ''):
            t['title'] = _thread_title(history)
    return threads, active_id


_USER_BUBBLE = {
    'maxWidth': '80%', 'padding': '10px 14px', 'borderRadius': '16px 16px 4px 16px',
    'background': '#111', 'color': '#fff', 'fontSize': '14px', 'lineHeight': '1.5',
    'whiteSpace': 'pre-wrap', 'wordBreak': 'break-word',
}
_ASSIST_BUBBLE = {
    'maxWidth': '88%', 'padding': '12px 16px', 'borderRadius': '16px 16px 16px 4px',
    'background': '#f4f4f5', 'color': '#111', 'fontSize': '14px', 'lineHeight': '1.55',
    'wordBreak': 'break-word',
}
_ASSIST_ERR = {**_ASSIST_BUBBLE, 'background': '#fffbeb',
               'border': '0.5px solid #fde68a', 'color': '#b45309'}

_CHIP_STYLE = {
    'fontSize': '12px', 'padding': '6px 12px', 'cursor': 'pointer',
    'background': '#fff', 'border': '0.5px solid #e5e7eb', 'borderRadius': '999px',
    'color': '#374151', 'transition': 'all 120ms ease', 'textAlign': 'left',
    'lineHeight': '1.3',
}

_ICON_BTN = {
    'background': 'transparent', 'border': 'none', 'cursor': 'pointer',
    'color': '#888', 'fontSize': '12px', 'padding': '2px 6px',
    'borderRadius': '6px', 'marginLeft': '6px',
}

_STARTER_PROMPTS = [
    "Give me a full portfolio health check in 5 bullet points.",
    "What would a conservative investor change here?",
    "Where would you put €500 more today?",
    "Three realistic risks in the next 12 months?",
]


def _user_row(text: str, is_last: bool = False):
    edit_btn = None
    if is_last:
        edit_btn = html.Button(
            "✎ Edit", id='coach-edit-btn', n_clicks=0, title="Edit this question",
            className='coach-icon-btn',
            style={**_ICON_BTN, 'marginTop': '4px'})
    return html.Div([
        html.Div(text, style=_USER_BUBBLE),
        edit_btn,
    ], style={'display': 'flex', 'flexDirection': 'column',
              'alignItems': 'flex-end', 'marginBottom': '10px'})


def _assistant_row(text: str, idx: int, err: bool = False, is_last: bool = False,
                   followups: list[str] | None = None):
    body = (
        html.Div(text, style={**_ASSIST_ERR, 'whiteSpace': 'pre-wrap'})
        if err else
        html.Div(dcc.Markdown(text, style={'margin': '0'}), style=_ASSIST_BUBBLE)
    )
    actions = None
    if not err:
        action_btns = [
            html.Button("Copy", id={'type': 'coach-copy', 'index': idx},
                        n_clicks=0, title="Copy answer",
                        className='coach-icon-btn', style=_ICON_BTN),
        ]
        if is_last:
            action_btns.append(html.Button(
                "↻ Regenerate", id='coach-regenerate-btn', n_clicks=0,
                title="Retry this question", className='coach-icon-btn',
                style=_ICON_BTN))
        actions = html.Div(action_btns, style={
            'display': 'flex', 'marginTop': '4px',
            'marginLeft': '6px', 'opacity': '0.7'})

    chips = None
    if is_last and followups:
        chips = html.Div([
            html.Div("Suggested follow-ups", style={
                'fontSize': '11px', 'color': '#888', 'margin': '10px 4px 6px',
                'letterSpacing': '0.04em', 'textTransform': 'uppercase',
                'fontWeight': '600',
            }),
            html.Div([
                html.Button(f, id={'type': 'coach-followup', 'index': i},
                            n_clicks=0, className='coach-chip', style=_CHIP_STYLE)
                for i, f in enumerate(followups)
            ], style={'display': 'flex', 'flexWrap': 'wrap', 'gap': '6px',
                      'marginLeft': '2px'}),
        ])

    return html.Div([
        html.Div([body, actions] if actions else [body],
                 style={'display': 'flex', 'flexDirection': 'column',
                        'alignItems': 'flex-start'}),
        chips,
    ], style={'display': 'flex', 'flexDirection': 'column',
              'alignItems': 'flex-start', 'marginBottom': '14px'})


def _thinking_bubble():
    return html.Div(
        html.Div([
            html.Span(".", style={'animation': 'coachPulse 1.2s infinite', 'animationDelay': '0s'}),
            html.Span(".", style={'animation': 'coachPulse 1.2s infinite', 'animationDelay': '0.2s', 'marginLeft': '2px'}),
            html.Span(".", style={'animation': 'coachPulse 1.2s infinite', 'animationDelay': '0.4s', 'marginLeft': '2px'}),
            html.Span("Thinking…", style={'marginLeft': '8px', 'color': '#888',
                                           'fontSize': '13px'}),
        ], style={**_ASSIST_BUBBLE, 'display': 'flex', 'alignItems': 'center',
                  'color': '#666'}),
        style={'display': 'flex', 'justifyContent': 'flex-start', 'marginBottom': '10px'},
    )


def _starter_panel():
    return html.Div([
        html.Div([
            html.Button(p, id={'type': 'coach-starter', 'index': i},
                        n_clicks=0, className='coach-chip', style=_CHIP_STYLE)
            for i, p in enumerate(_STARTER_PROMPTS)
        ], style={'display': 'flex', 'flexWrap': 'wrap', 'gap': '6px',
                  'justifyContent': 'center'}),
    ])


def _chat_bubbles(history: list[dict], pending: str | None = None):
    """Render the chat log — ChatGPT-style split bubbles."""
    if not history and not pending:
        return _starter_panel()

    rows: list = []
    last_idx = len(history) - 1
    for i, turn in enumerate(history):
        q = turn.get('q', '')
        a = turn.get('a', '')
        err = bool(turn.get('error'))
        fups = turn.get('followups') or []
        # "Edit" shows on the last user turn only when no request is in flight
        # and the last turn has a completed answer.
        rows.append(_user_row(q, is_last=(i == last_idx and not pending)))
        rows.append(_assistant_row(a, idx=i, err=err,
                                   is_last=(i == last_idx and not pending),
                                   followups=fups))
    if pending:
        rows.append(_user_row(pending))
        rows.append(_thinking_bubble())

    return html.Div(rows)


@app.callback(
    Output('coach-open', 'data'),
    Input('coach-toggle-btn', 'n_clicks'),
    State('coach-open', 'data'),
    prevent_initial_call=True,
)
def toggle_coach(n, is_open):
    if not n:
        return no_update
    return not bool(is_open)


@app.callback(
    Output('coach-open', 'data', allow_duplicate=True),
    Input('coach-close-btn', 'n_clicks'),
    prevent_initial_call=True,
)
def close_coach(n):
    if not n:
        return no_update
    return False


@app.callback(
    Output('coach-active-id', 'data'),
    Input({'type': 'coach-preset-btn', 'index': ALL}, 'n_clicks'),
    prevent_initial_call=True,
)
def pick_scenario(clicks):
    trig = ctx.triggered_id
    if not isinstance(trig, dict):
        return no_update
    # Guard against pattern-matching phantom triggers on mount.
    if not any(clicks or []):
        return no_update
    return trig.get('index') or no_update


@app.callback(
    Output('coach-mode', 'data'),
    Input('coach-mode-preset-btn', 'n_clicks'),
    Input('coach-mode-ai-btn', 'n_clicks'),
    prevent_initial_call=True,
)
def switch_mode(p, a):
    trig = ctx.triggered_id
    if trig == 'coach-mode-ai-btn' and a:
        return 'ai'
    if trig == 'coach-mode-preset-btn' and p:
        return 'preset'
    return no_update


@app.callback(
    Output('coach-api-key', 'data'),
    Input('coach-save-key-btn',  'n_clicks'),
    Input('coach-clear-key-btn', 'n_clicks'),
    State('coach-key-input', 'value'),
    prevent_initial_call=True,
)
def save_or_clear_key(save, clear, value):
    trig = ctx.triggered_id
    if trig == 'coach-clear-key-btn' and clear:
        return ''
    if trig == 'coach-save-key-btn' and save:
        return (value or '').strip()
    return no_update


# ── Chat: submit → pending-q, then run_llm consumes pending-q ────────────────

@app.callback(
    Output('coach-pending-q', 'data'),
    Output('coach-input', 'value'),
    Output('coach-input', 'disabled'),
    Output('coach-send-btn', 'disabled'),
    Output('coach-send-btn', 'children'),
    Input('coach-send-btn', 'n_clicks'),
    Input('coach-input', 'n_submit'),
    Input({'type': 'coach-starter',  'index': ALL}, 'n_clicks'),
    Input({'type': 'coach-followup', 'index': ALL}, 'n_clicks'),
    State('coach-input', 'value'),
    State({'type': 'coach-starter',  'index': ALL}, 'children'),
    State({'type': 'coach-followup', 'index': ALL}, 'children'),
    State('coach-pending-q', 'data'),
    prevent_initial_call=True,
)
def submit_question(send_n, submit_n, starter_clicks, fup_clicks,
                    text, starter_labels, fup_labels, pending):
    noop = (no_update,) * 5

    # If a request is already in flight, ignore new submissions.
    if pending:
        return noop

    trig = ctx.triggered_id
    q = None
    if trig == 'coach-send-btn' or trig == 'coach-input':
        q = (text or '').strip()
    elif isinstance(trig, dict):
        t = trig.get('type')
        i = trig.get('index')
        # Guard against the phantom trigger that pattern-matching Inputs
        # sometimes fire on mount (n_clicks is None/0 in that case).
        clicks = starter_clicks if t == 'coach-starter' else fup_clicks
        labels = starter_labels if t == 'coach-starter' else fup_labels
        if i is None or i >= len(clicks) or not clicks[i]:
            return noop
        q = (labels[i] if i < len(labels) else '').strip()

    if not q:
        return noop

    # Set pending-q → triggers run_llm via its Input. Disable input/button
    # while the request is in flight; run_llm re-enables them on completion.
    return q, '', True, True, "Sending…"


@app.callback(
    Output('coach-threads', 'data', allow_duplicate=True),
    Output('coach-active-thread-id', 'data', allow_duplicate=True),
    Output('coach-pending-q', 'data', allow_duplicate=True),
    Output('coach-input', 'disabled', allow_duplicate=True),
    Output('coach-send-btn', 'disabled', allow_duplicate=True),
    Output('coach-send-btn', 'children', allow_duplicate=True),
    Input('coach-pending-q', 'data'),
    State('coach-api-key', 'data'),
    State('coach-threads', 'data'),
    State('coach-active-thread-id', 'data'),
    State('portfolio-data', 'data'),
    State('market-intel-data', 'data'),
    State('valuation-data', 'data'),
    prevent_initial_call=True,
)
def run_llm(question, key, threads, active_id, port, intel, val):
    # Triggered when submit_question writes a question into pending-q.
    # Also fires when we clear pending-q at the end — we bail on that.
    log.info("coach.run_llm fired: question=%r key_present=%s",
             (question or '')[:60], bool(key))
    if not question:
        return no_update, no_update, no_update, no_update, no_update, no_update

    try:
        history = _active_history(threads, active_id)
        for turn in history:
            turn.pop('followups', None)

        def _commit_and_return(h):
            new_threads, new_active = _commit_history(threads, active_id, h)
            return new_threads, new_active, None, False, False, "Send ↑"

        if not key:
            history.append({'q': question,
                            'a': "No API key saved. Paste one to enable chat.",
                            'error': True})
            return _commit_and_return(history)

        try:
            provider = ai_provider.detect_provider(key)
            log.info("coach: calling provider=%s", provider)
            context_json = ai_provider.build_portfolio_context(port, intel, val)
            t0 = time.time()
            answer, followups = ai_provider.ask(key, context_json, question,
                                                history=history)
            log.info("coach: provider reply in %.1fs, %d chars",
                     time.time() - t0, len(answer or ''))
            if not (answer or '').strip():
                history.append({'q': question,
                                'a': "The provider returned an empty response. "
                                     "Try again or switch to a different model.",
                                'error': True})
            else:
                history.append({'q': question, 'a': answer, 'followups': followups})
        except requests.HTTPError as e:
            body = ''
            try:
                body = e.response.text[:300]
            except Exception:
                pass
            status = e.response.status_code if e.response else '?'
            log.warning("coach: HTTPError %s: %s", status, body)
            history.append({
                'q': question,
                'a': f"Provider returned an error ({status}). "
                     f"Check that the key is valid and has credit.\n{body}",
                'error': True,
            })
        except Exception as e:
            log.exception("coach: provider call failed")
            history.append({'q': question,
                            'a': f"Couldn't reach the provider: {type(e).__name__}: {e}",
                            'error': True})
        return _commit_and_return(history)
    except Exception:
        log.exception("coach.run_llm crashed")
        # Return a safe state so the UI unsticks from "Thinking…"
        return no_update, no_update, None, False, False, "Send ↑"


# ── Derived: chat-history = active thread's history ──────────────────────────

@app.callback(
    Output('coach-chat-history', 'data'),
    Input('coach-threads', 'data'),
    Input('coach-active-thread-id', 'data'),
)
def _derive_chat_history(threads, active_id):
    return _active_history(threads, active_id)


# ── Render the thread-tabs row. Kept separate from render_coach so sending a
#    message doesn't rebuild the whole panel.

@app.callback(
    Output('coach-tabs-row', 'children'),
    Input('coach-threads', 'data'),
    Input('coach-active-thread-id', 'data'),
)
def render_thread_tabs(threads, active_thread_id):
    threads = threads or []
    tabs: list = []
    for t in threads:
        is_active = (t.get('id') == active_thread_id)
        tab_label = t.get('title') or 'New chat'
        tabs.append(html.Div([
            html.Button(
                tab_label,
                id={'type': 'coach-thread-tab', 'index': t['id']},
                n_clicks=0,
                title=tab_label,
                style={
                    'background': '#111' if is_active else '#fff',
                    'color':      '#fff' if is_active else '#374151',
                    'border':     '0.5px solid ' + ('#111' if is_active else '#e5e7eb'),
                    'borderRadius': '999px 0 0 999px',
                    'padding': '4px 10px', 'fontSize': '12px',
                    'cursor': 'pointer', 'maxWidth': '180px',
                    'overflow': 'hidden', 'textOverflow': 'ellipsis',
                    'whiteSpace': 'nowrap', 'fontWeight': '500',
                }),
            html.Button(
                '×',
                id={'type': 'coach-thread-del', 'index': t['id']},
                n_clicks=0, title='Delete chat',
                style={
                    'background': '#111' if is_active else '#fff',
                    'color':      '#fff' if is_active else '#9ca3af',
                    'border':     '0.5px solid ' + ('#111' if is_active else '#e5e7eb'),
                    'borderLeft': 'none',
                    'borderRadius': '0 999px 999px 0',
                    'padding': '4px 8px', 'fontSize': '12px',
                    'cursor': 'pointer', 'fontWeight': '600',
                }),
        ], style={'display': 'flex', 'alignItems': 'center', 'marginRight': '6px'}))
    return tabs


# ── Thread management: clear / new / switch / delete ─────────────────────────

@app.callback(
    Output('coach-threads', 'data', allow_duplicate=True),
    Output('coach-active-thread-id', 'data', allow_duplicate=True),
    Input('coach-clear-chat-btn', 'n_clicks'),
    State('coach-threads', 'data'),
    State('coach-active-thread-id', 'data'),
    prevent_initial_call=True,
)
def clear_chat(n, threads, active_id):
    """Clear the current thread's history (keep the thread; fresh canvas)."""
    if not n:
        return no_update, no_update
    threads = list(threads or [])
    t = _find_thread(threads, active_id)
    if t is None:
        return threads, active_id
    t['history'] = []
    t['title']   = 'New chat'
    return threads, active_id


@app.callback(
    Output('coach-threads', 'data', allow_duplicate=True),
    Output('coach-active-thread-id', 'data', allow_duplicate=True),
    Input('coach-new-thread-btn', 'n_clicks'),
    State('coach-threads', 'data'),
    prevent_initial_call=True,
)
def new_thread(n, threads):
    if not n:
        return no_update, no_update
    threads = list(threads or [])
    # If the current first thread is already empty, just reuse it.
    if threads and not (threads[0].get('history') or []):
        return threads, threads[0]['id']
    t = _new_thread([])
    threads.insert(0, t)
    return threads, t['id']


@app.callback(
    Output('coach-active-thread-id', 'data', allow_duplicate=True),
    Input({'type': 'coach-thread-tab', 'index': ALL}, 'n_clicks'),
    prevent_initial_call=True,
)
def switch_thread(clicks):
    trig = ctx.triggered_id
    if not isinstance(trig, dict):
        return no_update
    if not any(clicks or []):
        return no_update
    return trig.get('index') or no_update


@app.callback(
    Output('coach-threads', 'data', allow_duplicate=True),
    Output('coach-active-thread-id', 'data', allow_duplicate=True),
    Input({'type': 'coach-thread-del', 'index': ALL}, 'n_clicks'),
    State('coach-threads', 'data'),
    State('coach-active-thread-id', 'data'),
    prevent_initial_call=True,
)
def delete_thread(clicks, threads, active_id):
    trig = ctx.triggered_id
    if not isinstance(trig, dict) or not any(clicks or []):
        return no_update, no_update
    tid = trig.get('index')
    threads = [t for t in (threads or []) if t.get('id') != tid]
    if active_id == tid:
        active_id = threads[0]['id'] if threads else None
    return threads, active_id


# ── Regenerate last answer: pop last turn and resubmit its question ──────────

@app.callback(
    Output('coach-threads', 'data', allow_duplicate=True),
    Output('coach-active-thread-id', 'data', allow_duplicate=True),
    Output('coach-pending-q', 'data', allow_duplicate=True),
    Output('coach-input', 'disabled', allow_duplicate=True),
    Output('coach-send-btn', 'disabled', allow_duplicate=True),
    Output('coach-send-btn', 'children', allow_duplicate=True),
    Input('coach-regenerate-btn', 'n_clicks'),
    State('coach-threads', 'data'),
    State('coach-active-thread-id', 'data'),
    State('coach-pending-q', 'data'),
    prevent_initial_call=True,
)
def regenerate_last(n, threads, active_id, pending):
    if not n or pending:
        return (no_update,) * 6
    history = _active_history(threads, active_id)
    if not history:
        return (no_update,) * 6
    last_q = (history[-1].get('q') or '').strip()
    if not last_q:
        return (no_update,) * 6
    history = history[:-1]
    new_threads, new_active = _commit_history(threads, active_id, history)
    return new_threads, new_active, last_q, True, True, "Sending…"


# ── Edit last question: pop last turn and pre-fill the input ─────────────────

@app.callback(
    Output('coach-threads', 'data', allow_duplicate=True),
    Output('coach-active-thread-id', 'data', allow_duplicate=True),
    Output('coach-prefill', 'data', allow_duplicate=True),
    Input('coach-edit-btn', 'n_clicks'),
    State('coach-threads', 'data'),
    State('coach-active-thread-id', 'data'),
    prevent_initial_call=True,
)
def edit_last(n, threads, active_id):
    if not n:
        return no_update, no_update, no_update
    history = _active_history(threads, active_id)
    if not history:
        return no_update, no_update, no_update
    last_q = (history[-1].get('q') or '')
    history = history[:-1]
    new_threads, new_active = _commit_history(threads, active_id, history)
    return new_threads, new_active, last_q


# ── Chat output: separate callback so typing/scrolling doesn't rebuild panel ─

@app.callback(
    Output('coach-chat-output', 'children'),
    Input('coach-chat-history', 'data'),
    Input('coach-pending-q', 'data'),
)
def render_chat(history, pending):
    return _chat_bubbles(history or [], pending)


# ── Clientside: copy an answer to clipboard ─────────────────────────────────

app.clientside_callback(
    """
    function(clicks, history) {
        const ctx = window.dash_clientside.callback_context;
        if (!ctx.triggered || !ctx.triggered.length) return window.dash_clientside.no_update;
        const trig = ctx.triggered[0];
        if (!trig.value) return window.dash_clientside.no_update;
        try {
            const id = JSON.parse(trig.prop_id.split('.')[0]);
            const turn = (history || [])[id.index] || {};
            const txt = turn.a || '';
            if (navigator.clipboard && txt) { navigator.clipboard.writeText(txt); }
        } catch (e) {}
        return (Date.now());
    }
    """,
    Output('coach-copy-signal', 'data'),
    Input({'type': 'coach-copy', 'index': ALL}, 'n_clicks'),
    State('coach-chat-history', 'data'),
    prevent_initial_call=True,
)


# ── Clientside: auto-scroll the chat area to the bottom on new content ──────

app.clientside_callback(
    """
    function(_children) {
        setTimeout(function() {
            var el = document.getElementById('coach-chat-output');
            if (el) { el.scrollTop = el.scrollHeight; }
        }, 30);
        return Date.now();
    }
    """,
    Output('coach-scroll-signal', 'data'),
    Input('coach-chat-output', 'children'),
    prevent_initial_call=True,
)


# ── Clientside: smooth-scroll to a panel when it opens ──────────────────────
# Target-Y math: let the BROWSER do it. We call scrollIntoView({block:'start'})
# synchronously, read the resulting pageYOffset (which the browser has placed
# exactly where it should land, respecting scroll-margin-top from custom.css),
# then immediately revert to the original scroll position. Both writes happen
# in the same JS task so the browser only paints the final state — the jump
# is invisible. This eliminates any manual math (offsetTop chains, transform
# interference, flex/grid edge cases) and produces always-correct target Y.
#
# Speed: browser-native scrollIntoView({behavior:'smooth'}) is fixed and fast.
# We replace it with a requestAnimationFrame tween using easeInOutCubic over
# ~800 ms for a more deliberate feel. Reduced-motion users get an instant
# jump via the matchMedia check.

_SMOOTH_SCROLL_JS_TMPL = """
function(%(trigger)s) {
    %(guard)s
    setTimeout(function() {
        var el = document.getElementById('%(element_id)s');
        if (!el) { return; }

        // Capture the browser's own idea of the correct scroll target by
        // jumping there synchronously, reading pageYOffset, then reverting.
        // Both scroll writes happen in one JS task — nothing is painted
        // between them, so the jump-and-revert is invisible to the user.
        var startY = window.pageYOffset;
        el.scrollIntoView({ block: 'start' });
        var targetY = window.pageYOffset;
        window.scrollTo(0, startY);

        var dy = targetY - startY;
        if (Math.abs(dy) < 2) { return; }

        // Respect OS "reduce motion" preference — skip the animation.
        var reduce = window.matchMedia
            && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (reduce) { window.scrollTo(0, targetY); return; }

        var duration = 800;
        var start = performance.now();
        function step(now) {
            var t = (now - start) / duration;
            if (t > 1) { t = 1; }
            // easeInOutCubic
            var ease = t < 0.5
                ? 4 * t * t * t
                : 1 - Math.pow(-2 * t + 2, 3) / 2;
            window.scrollTo(0, startY + dy * ease);
            if (t < 1) { requestAnimationFrame(step); }
        }
        requestAnimationFrame(step);
    }, %(delay)d);
    return Date.now();
}
"""

# Position-detail: trigger on the rendered children (DOM element exists),
# skip when children is null (panel closed).
app.clientside_callback(
    _SMOOTH_SCROLL_JS_TMPL % {
        'trigger':    'children',
        'guard':      'if (!children) { return window.dash_clientside.no_update; }',
        'element_id': 'position-detail',
        'delay':      280,   # wait for slideInDown (250 ms) + a safety margin
    },
    Output('position-detail-scroll-signal', 'data'),
    Input('position-detail', 'children'),
    prevent_initial_call=True,
)

# Coach panel: trigger on coach-open flipping to True. NOT on coach-panel.children
# (that would yank the page up on every chat keystroke during a re-render).
app.clientside_callback(
    _SMOOTH_SCROLL_JS_TMPL % {
        'trigger':    'isOpen',
        'guard':      'if (!isOpen) { return window.dash_clientside.no_update; }',
        'element_id': 'coach-panel',
        'delay':      80,    # coach render is server-side only; no slide animation
    },
    Output('coach-panel-scroll-signal', 'data'),
    Input('coach-open', 'data'),
    prevent_initial_call=True,
)


# ── Prefill: cleared on the next tick after it's consumed by render_coach ───
# coach-input reads prefill as its initial value. We clear the store right
# after so the next panel render doesn't re-insert the same text.

@app.callback(
    Output('coach-prefill', 'data', allow_duplicate=True),
    Input('coach-panel', 'children'),
    State('coach-prefill', 'data'),
    prevent_initial_call=True,
)
def clear_prefill_after_render(_children, prefill):
    return '' if prefill else no_update


# ── Ask coach about a specific ticker ────────────────────────────────────────
# Button lives inside the position-detail panel. Clicking it opens the coach,
# switches to AI mode, and pre-fills the input with a specific, actionable
# question about the ticker so the user can send immediately or lightly edit.

_ASK_COACH_TEMPLATE = (
    "Analyse my {ticker} position: is my position size appropriate given my "
    "overall portfolio, what are the main risks to watch, and does anything "
    "about the current valuation stand out?"
)

# Alternative templates — swap into _ASK_COACH_TEMPLATE if preferred:
#   "What should I know about {ticker} right now? Cover the thesis, any recent"
#   " news worth flagging, and how it fits with my other holdings."
#
#   "Give me a quick {ticker} health check: recent performance, fundamentals,"
#   " and whether I should consider trimming, holding, or adding."
#
#   "Explain {ticker} to me like I'm new to investing — what does the company"
#   " do, why might someone hold it, and what are the biggest risks?"


@app.callback(
    Output('coach-open',    'data', allow_duplicate=True),
    Output('coach-mode',    'data', allow_duplicate=True),
    Output('coach-prefill', 'data', allow_duplicate=True),
    Input({'type': 'position-ask-coach', 'index': ALL}, 'n_clicks'),
    State('selected-ticker', 'data'),
    prevent_initial_call=True,
)
def ask_coach_about_position(clicks, ticker):
    if not any(clicks or []) or not ticker:
        return no_update, no_update, no_update
    return True, 'ai', _ASK_COACH_TEMPLATE.format(ticker=ticker)


def _mode_btn_style(active: bool) -> dict:
    # Constant fontWeight (600) across active/inactive prevents width jitter
    # between states. minWidth ensures "Preset" and "AI" occupy identical
    # space so the pill never reshuffles when the user toggles modes.
    base = {
        'padding':        '6px 16px',
        'fontSize':       '12px',
        'fontWeight':     '600',
        'minWidth':       '62px',
        'textAlign':      'center',
        'lineHeight':     '1.4',
        'border':         'none',
        'outline':        'none',
        'borderRadius':   '5px',
        'letterSpacing': '0.02em',
        'fontFamily':     'inherit',
        'cursor':         'pointer',
        'boxSizing':      'border-box',
        'transition':     'background 120ms ease, color 120ms ease',
    }
    if active:
        return {**base, 'color': '#fff', 'background': '#111'}
    return {**base, 'color': '#666', 'background': 'transparent'}


@app.callback(
    Output('coach-panel', 'children'),
    Input('coach-open', 'data'),
    Input('coach-mode', 'data'),
    Input('coach-active-id', 'data'),
    Input('coach-api-key', 'data'),
    State('coach-threads', 'data'),
    State('coach-active-thread-id', 'data'),
    State('coach-chat-history', 'data'),
    State('coach-prefill', 'data'),
    State('portfolio-data', 'data'),
    State('market-intel-data', 'data'),
    State('valuation-data', 'data'),
)
def render_coach(is_open, mode, active_id, key, threads, active_thread_id,
                 chat_history, prefill, port, intel, val):
    if not is_open:
        return None

    mode = mode or 'preset'

    # ── Header: title left, mode toggle + close button right ──────────────────
    toggle = html.Div([
        html.Button("Preset", id='coach-mode-preset-btn',
                    style=_mode_btn_style(mode == 'preset')),
        html.Button("AI", id='coach-mode-ai-btn',
                    style=_mode_btn_style(mode == 'ai')),
    ], style={'display': 'flex', 'gap': '2px', 'padding': '3px',
              'background': '#f5f5f5', 'borderRadius': '7px'})

    header = html.Div([
        html.Div([
            html.Span("✨", style={'fontSize': '18px', 'marginRight': '8px'}),
            html.Span("Portfolio coach",
                      style={'fontSize': '16px', 'fontWeight': '600', 'color': '#111'}),
        ], style={'display': 'flex', 'alignItems': 'center'}),
        html.Div([
            toggle,
            html.Button("✕", id='coach-close-btn', title="Close", style={
                'width': '32px', 'height': '28px', 'background': '#fff',
                'border': '0.5px solid #ddd', 'borderRadius': '6px',
                'cursor': 'pointer', 'fontSize': '14px', 'color': '#666',
                'padding': '0', 'lineHeight': '1', 'marginLeft': '10px',
            }),
        ], style={'display': 'flex', 'alignItems': 'center'}),
    ], style={'display': 'flex', 'justifyContent': 'space-between',
              'alignItems': 'center', 'marginBottom': '14px',
              'paddingBottom': '14px', 'borderBottom': '0.5px solid #ebebeb'})

    provider    = ai_provider.detect_provider(key or '')
    key_present = bool(provider)

    children: list = [header]

    # Hidden ids registered once per branch below so callbacks (regenerate,
    # edit, new-thread) always have a target even when the relevant widget
    # isn't visible. suppress_callback_exceptions lets Dash tolerate missing
    # ids, but pattern-matching callbacks are happier with them present.
    _hidden_always = html.Div([
        html.Button(id='coach-regenerate-btn', n_clicks=0, style={'display': 'none'}),
        html.Button(id='coach-edit-btn',       n_clicks=0, style={'display': 'none'}),
        html.Button(id='coach-new-thread-btn', n_clicks=0, style={'display': 'none'}),
        html.Div(id='coach-tabs-row', style={'display': 'none'}),
    ], style={'display': 'none'})

    if mode == 'preset':
        # ── Preset: grid of clickable question chips + answer box ─────────────
        def _preset_chip_style(selected: bool) -> dict:
            base = {
                'padding': '10px 14px', 'fontSize': '13px', 'cursor': 'pointer',
                'borderRadius': '10px', 'textAlign': 'left', 'lineHeight': '1.35',
                'transition': 'all 120ms ease', 'fontWeight': '500',
            }
            if selected:
                return {**base, 'background': '#111', 'color': '#fff',
                        'border': '0.5px solid #111'}
            return {**base, 'background': '#fff', 'color': '#374151',
                    'border': '0.5px solid #e5e7eb'}

        children.append(html.Div([
            html.Button(
                s['label'],
                id={'type': 'coach-preset-btn', 'index': s['id']},
                n_clicks=0,
                className='coach-chip',
                style=_preset_chip_style(s['id'] == active_id),
            )
            for s in SCENARIOS
        ], style={'display': 'grid',
                  'gridTemplateColumns': 'repeat(auto-fill, minmax(220px, 1fr))',
                  'gap': '8px'}))

        if active_id:
            children.append(html.Div(
                render_scenario(active_id, port, intel, val),
                style={'marginTop': '14px'},
            ))
        # Hidden AI-only ids (keep registered for callbacks)
        children.append(html.Div([
            dcc.Input(id='coach-key-input', type='password', style={'display': 'none'}),
            html.Button(id='coach-save-key-btn',  style={'display': 'none'}),
            html.Button(id='coach-clear-key-btn', style={'display': 'none'}),
            dcc.Input(id='coach-input', type='text', style={'display': 'none'}),
            html.Button(id='coach-send-btn',        style={'display': 'none'}),
            html.Button(id='coach-clear-chat-btn',  style={'display': 'none'}),
            html.Div(id='coach-chat-output', style={'display': 'none'}),
        ], style={'display': 'none'}))
        children.append(_hidden_always)

    elif not key_present:
        # ── AI mode without key: key-entry form ───────────────────────────────
        children.append(html.Div([
            html.Div([
                dcc.Input(
                    id='coach-key-input', type='password', value='',
                    placeholder='Paste API key: sk-ant-… / xai-… / sk-…',
                    n_submit=0,
                    style={**_COACH_INPUT, 'marginBottom': '0', 'flex': '1'},
                ),
                html.Button("Save", id='coach-save-key-btn',
                            style={**_COACH_BTN_PRIMARY, 'marginLeft': '8px'}),
            ], style={'display': 'flex', 'alignItems': 'stretch'}),
            html.P("Stored in your browser only — never uploaded.",
                   style={'color': '#999', 'fontSize': '12px',
                          'margin': '6px 0 0', 'lineHeight': '1.5'}),
        ]))
        children.append(html.Div([
            html.Button(id='coach-clear-key-btn',   style={'display': 'none'}),
            dcc.Input(id='coach-input', type='text', style={'display': 'none'}),
            html.Button(id='coach-send-btn',        style={'display': 'none'}),
            html.Button(id='coach-clear-chat-btn',  style={'display': 'none'}),
            html.Div(id='coach-chat-output',        style={'display': 'none'}),
        ], style={'display': 'none'}))
        children.append(_hidden_always)

    else:
        # ── AI mode with key: full chat UI ────────────────────────────────────
        # Thread tabs row — its children are rendered by a separate callback
        # (render_thread_tabs) so sending a message doesn't rebuild the whole
        # panel (which would unmount the chat output mid-request).
        # When there are no threads yet, collapse the whole row (including its
        # border) so the panel doesn't show an empty gap above the status bar.
        has_threads = bool(threads)
        tabs_row = html.Div([
            html.Div(id='coach-tabs-row', style={
                'display': 'flex', 'overflowX': 'auto', 'flex': '1',
                'alignItems': 'center', 'gap': '0',
                'scrollbarWidth': 'thin',
            }),
            html.Button("＋ New", id='coach-new-thread-btn', n_clicks=0, style={
                'background': '#fff', 'color': '#111',
                'border': '0.5px solid #e5e7eb', 'borderRadius': '999px',
                'padding': '4px 12px', 'fontSize': '12px',
                'cursor': 'pointer', 'marginLeft': '8px', 'fontWeight': '500',
                'whiteSpace': 'nowrap',
            }),
        ], style={
            'display': 'flex' if has_threads else 'none',
            'alignItems': 'center',
            'marginBottom': '10px', 'paddingBottom': '10px',
            'borderBottom': '0.5px solid #f0f0f0',
        })

        # Status bar: connection chip + clear chat + clear key
        has_history = bool(chat_history)
        status_bar = html.Div([
            html.Span([
                html.Span("●", style={'color': '#16a34a', 'marginRight': '6px'}),
                f"{ai_provider.provider_label(provider)} connected",
            ], style={'fontSize': '12px', 'color': '#16a34a',
                      'background': '#f0fdf4', 'padding': '3px 10px',
                      'borderRadius': '999px',
                      'border': '0.5px solid #bbf7d0'}),
            html.Div([
                html.Button("Clear chat", id='coach-clear-chat-btn', n_clicks=0,
                            style={**_COACH_BTN, 'padding': '3px 10px',
                                   'fontSize': '12px', 'marginRight': '6px',
                                   'opacity': '1' if has_history else '0.4',
                                   'pointerEvents': 'auto' if has_history else 'none'}),
                html.Button("Clear key", id='coach-clear-key-btn', n_clicks=0,
                            style={**_COACH_BTN, 'padding': '3px 10px',
                                   'fontSize': '12px'}),
            ]),
        ], style={'display': 'flex', 'justifyContent': 'space-between',
                  'alignItems': 'center', 'marginBottom': '10px'})

        # Chat scroll area (updated by render_chat callback on new messages)
        chat_area = html.Div(
            _chat_bubbles(chat_history or [], None),
            id='coach-chat-output',
            className='coach-chat-output',
            style={'maxHeight': '420px',
                   'overflowY': 'auto', 'padding': '12px',
                   'background': '#fafafa',
                   'border': '0.5px solid #ebebeb', 'borderRadius': '10px',
                   'marginBottom': '10px'},
        )

        # Input row: text box + send button (Enter to send)
        input_row = html.Div([
            dcc.Input(
                id='coach-input', type='text', value=prefill or '', n_submit=0,
                placeholder='Press enter to send',
                autoComplete='off', debounce=False,
                style={'flex': '1', 'padding': '10px 14px', 'fontSize': '14px',
                       'lineHeight': '1.5', 'boxSizing': 'border-box',
                       'border': '0.5px solid #ddd', 'borderRadius': '10px',
                       'background': '#fff', 'color': '#111',
                       'transition': 'border-color 120ms ease'},
            ),
            html.Button("Send ↑", id='coach-send-btn', n_clicks=0,
                        className='coach-send-btn',
                        style={**_COACH_BTN_PRIMARY, 'marginLeft': '8px',
                               'padding': '11px 18px', 'fontSize': '13px',
                               'fontWeight': '600', 'borderRadius': '10px'}),
        ], className='coach-input-row',
           style={'display': 'flex', 'alignItems': 'stretch'})

        children.append(tabs_row)
        children.append(status_bar)
        children.append(chat_area)
        children.append(input_row)

        # Hidden key-input ids (keep registered)
        children.append(html.Div([
            dcc.Input(id='coach-key-input', type='password',
                      value=key or '', style={'display': 'none'}),
            html.Button(id='coach-save-key-btn', style={'display': 'none'}),
        ], style={'display': 'none'}))

    return html.Div(
        children,
        id='coach-panel-card',
        className='coach-panel-card',
        style={**CARD, 'marginTop': '14px'},
    )

