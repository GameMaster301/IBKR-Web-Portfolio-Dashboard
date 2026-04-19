import logging
import time
import dash
from dash import dcc, html, no_update, dash_table
from dash.dependencies import Input, Output, State
from dash import ctx, ALL
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import io
from config import cfg
from ibkr_client import fetch_all_data, connection_status, request_retry
from data_processor import process_positions, get_summary
from analytics import get_dividend_data_yf
from market_intel import (get_sector_geo,
                           get_earnings_data,
                           get_price_history)
from trade_history import (parse_activity_csv,
                           save_uploaded_trades, load_uploaded_trades,
                           clear_uploaded_trades)
from market_valuation import (get_buffett_indicator, get_sp500_pe,
                               get_shiller_cape, get_treasury_yield,
                               buffett_zone, pe_zone, cape_zone)

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

# ── Helpers ────────────────────────────────────────────────────────────────────

def to_eur(usd, rate):
    return usd / rate if rate else usd

CARD = {'border': '0.5px solid #ebebeb', 'borderRadius': '14px', 'padding': '24px'}

_LINK_STYLE = {
    'fontSize': '13px', 'color': '#378ADD', 'textDecoration': 'none',
    'padding': '5px 12px', 'border': '1px solid #cfe0f5', 'borderRadius': '8px',
    'fontWeight': '500', 'transition': 'background 0.15s ease',
}

def section_label(text):
    return html.P(text, style={
        'fontSize': '14px', 'color': '#555', 'margin': '0 0 16px',
        'textTransform': 'uppercase', 'letterSpacing': '0.07em', 'fontWeight': '600',
    })

def make_table(cols, rows):
    th = {'fontSize': '13px', 'color': '#666', 'fontWeight': '600',
          'padding': '0 12px 12px', 'textTransform': 'uppercase', 'letterSpacing': '0.04em',
          'borderBottom': '0.5px solid #f0f0f0'}
    header = html.Tr([
        html.Th(c, style={**th, 'textAlign': 'right' if i > 0 else 'left'})
        for i, c in enumerate(cols)
    ])
    return html.Table([html.Thead(header), html.Tbody(rows)],
                      style={'width': '100%', 'borderCollapse': 'collapse', 'fontSize': '16px'})

def badge(text, color, bg, border):
    return html.Span(text, style={
        'fontSize': '14px', 'color': color, 'background': bg,
        'padding': '4px 10px', 'borderRadius': '20px', 'border': f'0.5px solid {border}',
    })

def status_banner(icon, title, body, color):
    return html.Div([
        html.Div(icon, style={'fontSize': '32px', 'marginBottom': '14px'}),
        html.P(title, style={'fontSize': '17px', 'fontWeight': '600', 'color': '#111', 'margin': '0 0 6px'}),
        html.P(body, style={'fontSize': '15px', 'color': '#888', 'margin': '0', 'lineHeight': '1.6'}),
    ], style={
        'textAlign': 'center', 'padding': '48px 32px',
        'background': color, 'borderRadius': '14px',
        'border': '0.5px solid #ebebeb',
    })

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
                html.Button("↓ PDF", id='export-pdf-btn', n_clicks=0, style={
                    'fontSize': '13px', 'color': '#555', 'background': '#f5f5f5',
                    'border': '0.5px solid #ddd', 'borderRadius': '8px',
                    'padding': '6px 14px', 'cursor': 'pointer',
                }),
            ], style={'display': 'flex', 'alignItems': 'center', 'gap': '12px'}),
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

    # Retry-connection button — shown only when status='disconnected'.
    # Lives in the static layout so its callback always registers.
    html.Div(
        html.Button("↻ Retry connection", id='retry-connection-btn', n_clicks=0, style={
            'fontSize': '14px', 'fontWeight': '500', 'color': '#fff',
            'background': '#dc2626', 'border': 'none', 'borderRadius': '8px',
            'padding': '10px 22px', 'cursor': 'pointer',
        }),
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
                    section_label("Holdings"),
                    html.Span(id='stale-price-badge'),
                ], style={'display': 'flex', 'alignItems': 'center', 'gap': '12px', 'marginBottom': '0px'}),
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

    # Position detail panel (shown on row click)
    html.Div(id='position-detail'),

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
    Input('connection-status', 'data'),
    Input('portfolio-data', 'data'),
)
def update_status(status, data):
    ts = f"Updated {datetime.now().strftime('%H:%M:%S')}"
    retry_hidden = {'display': 'none', 'textAlign': 'center', 'marginBottom': '24px'}
    retry_shown  = {'display': 'block', 'textAlign': 'center', 'marginBottom': '24px'}

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
        return banner, badge("Connecting...", '#888', '#f5f5f5', '#e0e0e0'), "", retry_hidden

    if status == 'disconnected':
        return status_banner("🔌", "Not connected to IBKR",
                             "Make sure IB Gateway or TWS is open and logged in — the dashboard auto-detects the port and reconnects automatically.\n"
                             "IB Gateway: Configure → Settings → API → Settings → Enable ActiveX and Socket Clients (Port 4002 paper / 4001 live).\n"
                             "TWS: Edit → Global Configuration → API → Settings → Enable ActiveX and Socket Clients (Port 7497 paper / 7496 live).",
                             '#fef2f2'), \
               badge("● Disconnected", '#dc2626', '#fef2f2', '#fecaca'), ts, retry_shown

    if status == 'no_positions':
        return status_banner("📭", "No positions found",
                             "Connected to IBKR successfully, but your account has no open positions.", '#fafafa'), \
               badge("● Connected", '#16a34a', '#f0fdf4', '#bbf7d0'), ts, retry_hidden

    return None, badge(f"● Live · {_REFRESH_MS // 1000}s", '#16a34a', '#f0fdf4', '#bbf7d0'), ts, retry_hidden


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

@app.callback(
    Output('download-pdf', 'data'),
    Input('export-pdf-btn', 'n_clicks'),
    State('portfolio-data', 'data'),
    prevent_initial_call=True,
)
def export_pdf(_, data):
    if not data or 'positions' not in data:
        return no_update
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm

    df = pd.DataFrame(data['positions'])
    s = data.get('summary', {})
    a = data.get('account', {})
    rate = a.get('eurusd_rate', 1.08)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm)
    styles = getSampleStyleSheet()
    story = []

    # Title
    story.append(Paragraph("Portfolio Snapshot", ParagraphStyle(
        'title', parent=styles['Heading1'], fontSize=18, spaceAfter=4)))
    story.append(Paragraph(
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ParagraphStyle('sub', parent=styles['Normal'], fontSize=9,
                       textColor=colors.HexColor('#888888'), spaceAfter=14)))

    # Summary table
    total_val   = s.get('total_value', 0)
    unreal_pnl  = s.get('total_unrealized_pnl', 0)
    real_pnl    = s.get('total_realized_pnl', 0) or 0
    daily_pnl   = a.get('daily_pnl') or s.get('total_daily_pnl', 0) or 0
    summary_data = [
        ['Metric', 'USD', 'EUR'],
        ['Total Value',    f"${total_val:,.2f}",   f"€{total_val/rate:,.2f}"],
        ['Unrealized P&L', f"${unreal_pnl:+,.2f}", f"€{unreal_pnl/rate:+,.2f}"],
        ['Realized P&L',   f"${real_pnl:+,.2f}",   f"€{real_pnl/rate:+,.2f}"],
        ["Today's P&L",    f"${daily_pnl:+,.2f}",  f"€{daily_pnl/rate:+,.2f}"],
        ['Cash',           f"${a.get('cash_usd', 0) or 0:,.2f}", f"€{a.get('cash_eur', 0):,.2f}"],
    ]
    t = Table(summary_data, colWidths=[80*mm, 40*mm, 40*mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f5f5f5')),
        ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0, 0), (-1, -1), 9),
        ('ALIGN',      (1, 0), (-1, -1), 'RIGHT'),
        ('GRID',       (0, 0), (-1, -1), 0.25, colors.HexColor('#e0e0e0')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fafafa')]),
        ('TOPPADDING',  (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(t)
    story.append(Spacer(1, 6*mm))

    # Best / worst performer callout
    best   = s.get('best_performer', '—')
    worst  = s.get('worst_performer', '—')
    best_row  = df[df['ticker'] == best].iloc[0]  if best  != '—' and best  in df['ticker'].values else None
    worst_row = df[df['ticker'] == worst].iloc[0] if worst != '—' and worst in df['ticker'].values else None
    best_str  = f"{best} ({best_row['pnl_pct']:+.2f}%)"   if best_row  is not None else best
    worst_str = f"{worst} ({worst_row['pnl_pct']:+.2f}%)" if worst_row is not None else worst
    perf_data = [
        ['Best Performer', 'Worst Performer'],
        [best_str, worst_str],
    ]
    pt = Table(perf_data, colWidths=[82*mm, 82*mm])
    pt.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, 0), colors.HexColor('#f5f5f5')),
        ('FONTNAME',      (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',      (0, 0), (-1, -1), 9),
        ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
        ('GRID',          (0, 0), (-1, -1), 0.25, colors.HexColor('#e0e0e0')),
        ('TEXTCOLOR',     (0, 1), (0, 1), colors.HexColor('#166534')),  # best = green
        ('TEXTCOLOR',     (1, 1), (1, 1), colors.HexColor('#991b1b')),  # worst = red
        ('FONTNAME',      (0, 1), (-1, 1), 'Helvetica-Bold'),
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(pt)
    story.append(Spacer(1, 10*mm))

    # Holdings table — includes daily change
    story.append(Paragraph("Holdings", ParagraphStyle(
        'h2', parent=styles['Heading2'], fontSize=12, spaceAfter=6)))
    hold_data = [['Ticker', 'Qty', 'Avg Cost', 'Price', 'Day %', 'Mkt Value', 'P&L %', 'Weight']]
    for _, row in df.iterrows():
        day_pct = row.get('daily_change_pct')
        day_str = f"{day_pct:+.2f}%" if pd.notna(day_pct) and day_pct is not None else '—'
        hold_data.append([
            row['ticker'],
            str(int(row['quantity'])),
            f"${row['avg_cost']:,.2f}",
            f"${row['current_price']:,.2f}",
            day_str,
            f"${row['market_value']:,.2f}",
            f"{row['pnl_pct']:+.2f}%",
            f"{row['allocation_pct']:.1f}%",
        ])
    ht = Table(hold_data, colWidths=[20*mm, 12*mm, 22*mm, 22*mm, 16*mm, 26*mm, 16*mm, 16*mm])
    # colour positive/negative day % and P&L % cells per row
    ht_style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f5f5f5')),
        ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0, 0), (-1, -1), 8),
        ('ALIGN',      (1, 0), (-1, -1), 'RIGHT'),
        ('ALIGN',      (0, 0), (0, -1), 'LEFT'),
        ('GRID',       (0, 0), (-1, -1), 0.25, colors.HexColor('#e0e0e0')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fafafa')]),
        ('TOPPADDING',  (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    for i, row in enumerate(df.itertuples(), start=1):
        day_pct = getattr(row, 'daily_change_pct', None)
        if day_pct is not None and pd.notna(day_pct):
            col = colors.HexColor('#166534') if day_pct >= 0 else colors.HexColor('#991b1b')
            ht_style.append(('TEXTCOLOR', (4, i), (4, i), col))
        pnl = getattr(row, 'pnl_pct', 0) or 0
        col = colors.HexColor('#166534') if pnl >= 0 else colors.HexColor('#991b1b')
        ht_style.append(('TEXTCOLOR', (6, i), (6, i), col))
    ht.setStyle(TableStyle(ht_style))
    story.append(ht)

    doc.build(story)
    buf.seek(0)
    filename = f"portfolio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    return dcc.send_bytes(buf.read(), filename)


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
                html.A("Yahoo", href=f"https://finance.yahoo.com/quote/{ticker}",
                       target='_blank', style=_LINK_STYLE),
                html.A("TradingView", href=f"https://www.tradingview.com/symbols/{ticker}/",
                       target='_blank', style=_LINK_STYLE),
                html.Span("Esc", style={
                    'fontSize': '13px', 'color': '#555',
                    'padding': '5px 12px', 'border': '1px solid #cfe0f5',
                    'borderRadius': '8px', 'background': '#fff',
                    'fontWeight': '500', 'marginLeft': '8px',
                }),
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

    weights = [p.get('allocation_pct', 0) for p in data['positions']]
    result  = {'tickers': tickers, 'sector_geo': {}, 'earnings': {}}

    def _fetch_sector():
        return 'sector_geo', get_sector_geo(tickers)

    def _fetch_earnings():
        return 'earnings', get_earnings_data(tickers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_fetch_sector),
            pool.submit(_fetch_earnings),
        ]
        for f in futures:
            try:
                key, val = f.result()
                result[key] = val
            except Exception as e:
                log.warning("Market intel fetch failed: %s", e)

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
    color_by_sec = dict(zip(sec_labels, sec_colors))

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

    # When the country chart is a donut (≤2 countries) both the label and
    # the chart are centered in their column so the block doesn't look
    # like it's floating against the left edge of a too-wide flex cell.
    country_is_donut = len(cty_labels) <= 2
    country_align    = 'center' if country_is_donut else 'left'

    return html.Div([
        section_label("Sector & Geography Exposure"),
        html.Div([
            # Left: sector donut
            html.Div([
                html.P("Sector", style={'fontSize': '13px', 'color': '#777',
                                        'textTransform': 'uppercase',
                                        'letterSpacing': '0.04em', 'margin': '0 0 8px'}),
                dcc.Graph(figure=donut, config={'displayModeBar': False}),
            ], style={'flex': '2', 'minWidth': '200px'}),

            # Right: country chart (donut centered, bar left-aligned)
            html.Div([
                html.P("Country", style={'fontSize': '13px', 'color': '#777',
                                          'textTransform': 'uppercase',
                                          'letterSpacing': '0.04em',
                                          'margin': '0 0 8px',
                                          'textAlign': country_align}),
                html.Div(
                    dcc.Graph(figure=bar, config={'displayModeBar': False}),
                    style={'maxWidth': '280px', 'margin': '0 auto'}
                            if country_is_donut else {},
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
    def _fetch(key, fn):
        try:
            return key, fn()
        except Exception as e:
            log.warning('Valuation fetch failed (%s): %s', key, e)
            return key, None

    result = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(_fetch, 'buffett',  get_buffett_indicator),
            pool.submit(_fetch, 'sp500_pe', get_sp500_pe),
            pool.submit(_fetch, 'cape',     get_shiller_cape),
            pool.submit(_fetch, 'treasury', get_treasury_yield),
        ]
        for f in futures:
            key, val = f.result()
            result[key] = val
    return result


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
