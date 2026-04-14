import logging
import time
import dash
from dash import dcc, html, no_update, dash_table
from dash.dependencies import Input, Output, State
from dash import ctx
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import io
from config import cfg
from ibkr_client import fetch_all_data, connection_status
from data_processor import process_positions, get_summary
from analytics import get_dividend_data_yf
from ai_analyst import analyse_portfolio
from market_intel import (get_sector_geo,
                           get_earnings_data, compute_efficient_frontier)
from market_valuation import (get_buffett_indicator, get_sp500_pe,
                               get_shiller_cape,
                               buffett_zone, pe_zone, cape_zone)

log = logging.getLogger(__name__)

app = dash.Dash(__name__, suppress_callback_exceptions=True)

# ── Helpers ────────────────────────────────────────────────────────────────────

def to_eur(usd, rate):
    return usd / rate if rate else usd

CARD = {'border': '0.5px solid #ebebeb', 'borderRadius': '14px', 'padding': '24px'}

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
            html.Div(id='connection-badge'),
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
        ], style={**CARD, 'width': '260px', 'display': 'flex', 'flexDirection': 'column', 'justifyContent': 'center'}),
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
        html.P("Sector & geography · Earnings · Dividends · "
               "Historical scenarios · Efficient frontier",
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

    # Historical Scenarios
    html.Div(id='scenarios-section', style={
        'marginTop': '32px', 'paddingTop': '28px',
        'borderTop': '0.5px solid #f0f0f0',
    }),

    # Efficient Frontier
    html.Div(id='frontier-section', style={
        'marginTop': '32px', 'paddingTop': '28px',
        'borderTop': '0.5px solid #f0f0f0',
    }),

    # Market Valuation (Buffett / S&P PE / Shiller CAPE)
    html.Div(id='market-valuation-section', style={
        'marginTop': '32px', 'paddingTop': '28px',
        'borderTop': '0.5px solid #f0f0f0',
    }),

    # AI analysis
    html.Div([
        html.Div([
            html.Div([
                section_label("AI Analysis"),
                html.Span("Get a natural language summary of your portfolio's risk, "
                          "performance and key opportunities.",
                          style={'fontSize': '14px', 'color': '#888',
                                 'marginTop': '-12px', 'display': 'block',
                                 'marginBottom': '6px', 'lineHeight': '1.5',
                                 'maxWidth': '420px'}),
                html.Span("Powered by Claude",
                          style={'fontSize': '13px', 'color': '#bbb',
                                 'display': 'block', 'marginBottom': '17px'}),
            ]),
            html.Button("✦ Analyse Portfolio", id='ai-analyse-btn', n_clicks=0, style={
                'background': '#378ADD', 'border': 'none', 'borderRadius': '8px',
                'padding': '8px 20px', 'fontSize': '15px', 'cursor': 'pointer',
                'color': '#fff', 'fontFamily': 'inherit', 'fontWeight': '500',
                'letterSpacing': '0.02em',
            }),
        ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'flex-start'}),
        html.Div(id='ai-analysis-output'),
    ], style={**CARD, 'marginTop': '24px'}),

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
    raw = fetch_all_data()
    if not raw or not raw['positions']:
        status = 'no_positions' if (raw is not None) else connection_status()
        return {}, status
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
    Input('connection-status', 'data'),
    Input('portfolio-data', 'data'),
)
def update_status(status, data):
    ts = f"Updated {datetime.now().strftime('%H:%M:%S')}"

    if status == 'loading':
        return status_banner("⏳", "Connecting to TWS...",
                             "Fetching your portfolio data. This takes a few seconds.", '#fafafa'), \
               badge("Connecting...", '#888', '#f5f5f5', '#e0e0e0'), ""

    if status == 'disconnected':
        return status_banner("🔌", "Not connected to TWS",
                             "Make sure TWS is open and logged in — the dashboard reconnects automatically.\n"
                             "In TWS: Edit → Global Configuration → API → Settings → Enable ActiveX and Socket Clients → Port 7497.",
                             '#fef2f2'), \
               badge("● Disconnected", '#dc2626', '#fef2f2', '#fecaca'), ts

    if status == 'no_positions':
        return status_banner("📭", "No positions found",
                             "Connected to TWS successfully, but your account has no open positions.", '#fafafa'), \
               badge("● Connected", '#16a34a', '#f0fdf4', '#bbf7d0'), ts

    return None, badge(f"● Live · {_REFRESH_MS // 1000}s", '#16a34a', '#f0fdf4', '#bbf7d0'), ts


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
    total_val  = s.get('total_value', 0)
    unreal_pnl = s.get('total_unrealized_pnl', 0)
    daily_pnl  = a.get('daily_pnl') or s.get('total_daily_pnl', 0) or 0
    summary_data = [
        ['Metric', 'USD', 'EUR'],
        ['Total Value',    f"${total_val:,.2f}",  f"€{total_val/rate:,.2f}"],
        ['Unrealized P&L', f"${unreal_pnl:+,.2f}", f"€{unreal_pnl/rate:+,.2f}"],
        ["Today's P&L",    f"${daily_pnl:+,.2f}",  f"€{daily_pnl/rate:+,.2f}"],
        ['Cash',           '—',                   f"€{a.get('cash_eur', 0):,.2f}"],
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
    story.append(Spacer(1, 10*mm))

    # Holdings table
    story.append(Paragraph("Holdings", ParagraphStyle(
        'h2', parent=styles['Heading2'], fontSize=12, spaceAfter=6)))
    hold_data = [['Ticker', 'Qty', 'Avg Cost', 'Price', 'Mkt Value', 'P&L', 'P&L %', 'Weight']]
    for _, row in df.iterrows():
        hold_data.append([
            row['ticker'],
            str(int(row['quantity'])),
            f"${row['avg_cost']:,.2f}",
            f"${row['current_price']:,.2f}",
            f"${row['market_value']:,.2f}",
            f"${row['unrealized_pnl']:+,.2f}",
            f"{row['pnl_pct']:+.2f}%",
            f"{row['allocation_pct']:.1f}%",
        ])
    ht = Table(hold_data, colWidths=[20*mm, 14*mm, 22*mm, 22*mm, 26*mm, 24*mm, 17*mm, 17*mm])
    ht.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f5f5f5')),
        ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0, 0), (-1, -1), 8),
        ('ALIGN',      (1, 0), (-1, -1), 'RIGHT'),
        ('GRID',       (0, 0), (-1, -1), 0.25, colors.HexColor('#e0e0e0')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fafafa')]),
        ('TOPPADDING',  (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(ht)

    doc.build(story)
    buf.seek(0)
    filename = f"portfolio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    return dcc.send_bytes(buf.read(), filename)


# ── Position detail (click to expand) ─────────────────────────────────────────

@app.callback(
    Output('selected-ticker', 'data'),
    Input('holdings-datatable', 'active_cell'),
    Input('kb-escape-btn', 'n_clicks'),   # triggered when user presses Escape
    State('holdings-datatable', 'data'),
    State('selected-ticker', 'data'),
    prevent_initial_call=True,
)
def select_ticker(active_cell, _, table_data, current):
    # Escape key: always close the detail panel regardless of what's selected.
    if ctx.triggered_id == 'kb-escape-btn':
        return None
    if not active_cell or not table_data:
        return no_update
    ticker = table_data[active_cell['row']]['ticker']
    return None if current == ticker else ticker


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
            html.Span(f"${low:,.2f}", style={'fontSize': '13px', 'color': '#aaa'}),
            html.Span(f"{pct:.0f}% of range",
                      style={'fontSize': '13px', 'color': '#aaa', 'position': 'absolute',
                             'left': '50%', 'transform': 'translateX(-50%)'}),
            html.Span(f"${high:,.2f}", style={'fontSize': '13px', 'color': '#aaa'}),
        ], style={'display': 'flex', 'justifyContent': 'space-between', 'position': 'relative'}),
    ])


def _stat(label, value, accent=None):
    return html.Div([
        html.P(label, style={
            'fontSize': '12px', 'color': '#bbb', 'margin': '0 0 4px',
            'textTransform': 'uppercase', 'letterSpacing': '0.05em',
        }),
        html.P(value, style={
            'fontSize': '16px', 'fontWeight': '500', 'margin': '0',
            'color': accent or '#111',
        }),
    ], style={'minWidth': '90px'})


@app.callback(
    Output('position-detail', 'children'),
    Input('selected-ticker', 'data'),
    State('portfolio-data', 'data'),
)
def show_position_detail(ticker, data):
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
    vwap         = r.get('vwap')
    spread       = r.get('spread')
    low_52w      = r.get('low_52w')
    high_52w     = r.get('high_52w')
    volume       = r.get('volume')

    # Daily change header
    if daily_chg is not None and daily_chg == daily_chg:
        chg_color = '#16a34a' if daily_chg >= 0 else '#dc2626'
        chg_str   = f"{'▲' if daily_chg >= 0 else '▼'} ${abs(daily_chg):,.2f}  ({daily_chg_pct:+.2f}%)"
    else:
        chg_color, chg_str = '#bbb', '—'

    # VWAP vs price
    if vwap and vwap == vwap:
        vwap_diff  = price - vwap
        vwap_color = '#16a34a' if vwap_diff >= 0 else '#dc2626'
        vwap_str   = f"${vwap:,.2f}  ({vwap_diff:+.2f})"
    else:
        vwap_color, vwap_str = '#bbb', '—'

    spread_str = f"${spread:,.4f}" if spread and spread == spread else '—'
    vol_str    = f"{int(volume):,}"  if volume and volume == volume else '—'

    stats = html.Div([
        _stat("Daily Change", chg_str, chg_color),
        _stat("VWAP", vwap_str, vwap_color),
        _stat("Spread", spread_str),
        _stat("Volume", vol_str),
    ], style={'display': 'flex', 'gap': '32px', 'flexWrap': 'wrap', 'marginTop': '17px'})

    # 52-week range
    if (low_52w and high_52w and low_52w == low_52w and high_52w == high_52w
            and high_52w > low_52w):
        range_section = html.Div([
            html.P("52-Week Range", style={
                'fontSize': '12px', 'color': '#bbb', 'margin': '0 0 0',
                'textTransform': 'uppercase', 'letterSpacing': '0.05em',
            }),
            _range_bar(low_52w, high_52w, price),
        ], style={'marginTop': '17px'})
    else:
        range_section = None

    return html.Div([
        # Header
        html.Div([
            html.Span(ticker, style={'fontWeight': '700', 'fontSize': '17px', 'color': '#111'}),
            html.Span(f"${price:,.2f}",
                      style={'fontSize': '17px', 'color': '#111', 'marginLeft': '12px'}),
            html.Span(chg_str,
                      style={'fontSize': '15px', 'color': chg_color, 'marginLeft': '12px'}),
        ], style={'display': 'flex', 'alignItems': 'center'}),
        range_section,
        stats,
    ], style={
        **CARD,
        'marginTop': '14px',
        'background': '#fafafa',
        'borderLeft': '3px solid #378ADD',
    })


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


# ── AI analysis ────────────────────────────────────────────────────────────────

@app.callback(
    Output('ai-analysis-output', 'children'),
    Input('ai-analyse-btn', 'n_clicks'),
    State('portfolio-data', 'data'),
    prevent_initial_call=True,
)
def run_ai_analysis(_, data):
    if not data or not data.get('positions'):
        return html.P(
            "No portfolio data available — connect to TWS and wait for the first refresh.",
            style={'fontSize': '15px', 'color': '#bbb', 'margin': '16px 0 0'},
        )

    text = analyse_portfolio(
        positions=data['positions'],
        summary=data.get('summary', {}),
        account=data.get('account', {}),
    )

    ts = datetime.now().strftime('%H:%M:%S')

    # Split on newlines; render each non-empty line as its own paragraph
    paragraphs = [line.strip() for line in text.split('\n') if line.strip()]

    return html.Div([
        html.Div([
            html.Span("✦ Claude", style={
                'color': '#378ADD', 'fontWeight': '600', 'fontSize': '14px',
            }),
            html.Span(f" · {ts}", style={'color': '#888', 'fontSize': '13px'}),
        ], style={
            'marginBottom': '17px', 'marginTop': '17px',
            'paddingTop': '17px', 'borderTop': '0.5px solid #f0f0f0',
        }),
        *[html.P(p, style={
            'fontSize': '15px', 'lineHeight': '1.75',
            'margin': '0 0 10px', 'color': '#111',
        }) for p in paragraphs],
    ])


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

    tickers     = [p['ticker'] for p in data['positions']]
    ticker_key  = tuple(sorted(tickers))

    # If tickers haven't changed the 4-hour cached values are still valid —
    # skip the fetch and leave the store (and all downstream renders) unchanged.
    if ticker_key == _last_intel_tickers:
        return no_update

    _last_intel_tickers = ticker_key

    result = {'tickers': tickers, 'sector_geo': {}, 'earnings': {}}

    def _fetch_sector():
        return 'sector_geo', get_sector_geo(tickers)

    def _fetch_earnings():
        return 'earnings', get_earnings_data(tickers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_fetch_sector), pool.submit(_fetch_earnings)]
        for f in futures:
            try:
                key, val = f.result()
                result[key] = val
            except Exception as e:
                log.warning("Market intel fetch failed: %s", e)

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

    # Build weighted sector and country aggregates
    sector_val:  dict = {}
    country_val: dict = {}
    sector_tickers: dict = {}

    for p in positions:
        sym  = p['ticker']
        val  = p['market_value']
        info = sg.get(sym, {})
        sec  = info.get('sector',  'Unknown')
        cty  = info.get('country', 'Unknown')

        sector_val[sec]  = sector_val.get(sec, 0)  + val
        country_val[cty] = country_val.get(cty, 0) + val
        sector_tickers.setdefault(sec, []).append(sym)

    total = sum(sector_val.values()) or 1

    # ── Sector donut ────────────────────────────────────────────────────────
    sec_labels = list(sector_val.keys())
    sec_values = [sector_val[s] for s in sec_labels]

    colors = ['#378ADD', '#f97316', '#a855f7', '#22c55e',
              '#eab308', '#ec4899', '#14b8a6', '#6366f1',
              '#84cc16', '#ef4444', '#06b6d4']

    donut = go.Figure(go.Pie(
        labels=sec_labels, values=sec_values, hole=0.62,
        textposition='none',
        marker=dict(colors=colors[:len(sec_labels)]),
        hovertemplate='<b>%{label}</b><br>%{percent:.1%}  ·  $%{value:,.0f}<extra></extra>',
    ))
    donut.update_layout(
        margin=dict(t=0, b=0, l=0, r=0),
        showlegend=False,
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        height=200,
    )

    # ── Country bar chart ────────────────────────────────────────────────────
    cty_sorted = sorted(country_val.items(), key=lambda x: x[1], reverse=True)[:8]
    cty_labels = [c[0] for c in cty_sorted]
    cty_values = [c[1] / total * 100 for c in cty_sorted]

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
    legend_rows = []
    for i, (sec, val) in enumerate(sorted(sector_val.items(),
                                           key=lambda x: x[1], reverse=True)):
        pct = val / total * 100
        dot_color = colors[i % len(colors)]
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

            # Right: country bar
            html.Div([
                html.P("Country", style={'fontSize': '13px', 'color': '#777',
                                          'textTransform': 'uppercase',
                                          'letterSpacing': '0.04em', 'margin': '0 0 8px'}),
                dcc.Graph(figure=bar, config={'displayModeBar': False}),
            ], style={'flex': '3', 'minWidth': '200px'}),
        ], style={'display': 'flex', 'gap': '24px', 'flexWrap': 'wrap'}),
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



# ── 5. Efficient frontier ──────────────────────────────────────────────────────
# Monte Carlo simulation: 2500 random weight combinations for the current
# holdings, coloured by Sharpe ratio.  The user's actual allocation is shown
# as a star.  This reveals whether the current weighting sits on the efficient
# envelope or whether a simple reallocation could improve risk-adjusted returns.

@app.callback(
    Output('frontier-section', 'children'),
    Input('market-intel-data', 'data'),
    State('portfolio-data', 'data'),
)
def render_frontier(intel, port_data):
    try:
        return _render_frontier_inner(intel, port_data)
    except Exception as e:
        return _intel_error("Efficient Frontier", e)


def _render_frontier_inner(intel, port_data):
    if not intel:
        return _intel_loading('efficient frontier data')
    if not port_data or 'positions' not in port_data:
        return None

    tickers = intel.get('tickers', [])
    weights = [p['allocation_pct'] for p in port_data['positions']]

    result = compute_efficient_frontier(tickers, weights)
    if result is None:
        return html.Div(
            html.P("Need at least 2 positions with 90+ days of history.",
                   style={'fontSize': '15px', 'color': '#bbb',
                          'textAlign': 'center', 'padding': '24px 0'}),
            style=CARD)

    portfolios = result['portfolios']
    current    = result['current']

    vols    = [p['vol']    for p in portfolios]
    rets    = [p['ret']    for p in portfolios]
    sharpes = [p['sharpe'] for p in portfolios]

    fig = go.Figure()

    # Random portfolios cloud
    fig.add_trace(go.Scatter(
        x=vols, y=rets,
        mode='markers',
        marker=dict(
            color=sharpes,
            colorscale='Viridis',
            size=4,
            opacity=0.5,
            colorbar=dict(title=dict(text='Sharpe', side='right'),
                          thickness=10, len=0.7,
                          tickfont=dict(size=10)),
        ),
        hovertemplate=(
            'Vol: <b>%{x:.1f}%</b><br>'
            'Return: <b>%{y:.1f}%</b><br>'
            'Sharpe: <b>%{marker.color:.2f}</b>'
            '<extra>Random portfolio</extra>'
        ),
        name='2 500 random portfolios',
    ))

    # Current portfolio star — marker + persistent annotation
    fig.add_trace(go.Scatter(
        x=[current['vol']], y=[current['ret']],
        mode='markers',
        marker=dict(color='#ff3b30', size=16, symbol='star',
                    line=dict(color='#fff', width=1.5)),
        hovertemplate=(
            f"<b>Your portfolio</b><br>"
            f"Vol: {current['vol']:.1f}%<br>"
            f"Return: {current['ret']:.1f}%<br>"
            f"Sharpe: {current['sharpe']:.2f}"
            "<extra></extra>"
        ),
        name='★ Your portfolio',
    ))
    fig.add_annotation(
        x=current['vol'],
        y=current['ret'],
        text=f"<b>Your portfolio</b>  Sharpe: {current['sharpe']:.2f}",
        showarrow=True,
        arrowhead=2,
        arrowcolor='#ff3b30',
        arrowwidth=1.5,
        ax=50,
        ay=-40,
        font=dict(size=11, color='#ff3b30'),
        bgcolor='rgba(255,255,255,0.88)',
        bordercolor='#ff3b30',
        borderwidth=1,
        borderpad=4,
        xanchor='left',
    )

    fig.update_layout(
        margin=dict(t=40, b=8, l=0, r=0),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        showlegend=True,
        legend=dict(orientation='h', x=0, y=1.0, xanchor='left', yanchor='bottom',
                    font=dict(size=12, color='#888'),
                    bgcolor='rgba(0,0,0,0)'),
        hovermode='closest',
        xaxis=dict(title=dict(text='Annualised Volatility (%)', font=dict(size=12)),
                   showgrid=True, gridcolor='#f5f5f5', zeroline=False,
                   tickfont=dict(size=11, color='#bbb')),
        yaxis=dict(title=dict(text='Annualised Return (%)', font=dict(size=12)),
                   showgrid=True, gridcolor='#f5f5f5', zeroline=False,
                   tickfont=dict(size=11, color='#bbb')),
        height=340,
    )

    # Percentile rank of current Sharpe vs the simulated cloud
    all_sharpes = sorted(sharpes)
    rank = sum(1 for s in all_sharpes if s < current['sharpe']) / len(all_sharpes) * 100
    rank_color = '#16a34a' if rank >= 60 else ('#b45309' if rank >= 40 else '#dc2626')
    sharpe_val = current['sharpe']

    # Plain-language interpretation of the result
    if rank >= 75:
        interpretation = (
            f"Your portfolio's Sharpe of {sharpe_val:.2f} places it in the top {100-rank:.0f}% "
            f"of all {len(all_sharpes):,} tested weightings. Your current allocation is "
            f"well-optimised — you are capturing strong returns relative to the risk you carry."
        )
    elif rank >= 50:
        interpretation = (
            f"Your portfolio's Sharpe of {sharpe_val:.2f} is above the median of all "
            f"{len(all_sharpes):,} tested weightings. Your allocation is reasonably efficient, "
            f"though a modest rebalancing could push you closer to the efficient frontier."
        )
    elif rank >= 25:
        interpretation = (
            f"Your portfolio's Sharpe of {sharpe_val:.2f} sits in the lower half of "
            f"{len(all_sharpes):,} tested weightings. There is meaningful room to improve "
            f"risk-adjusted returns by reweighting your current holdings."
        )
    else:
        interpretation = (
            f"Your portfolio's Sharpe of {sharpe_val:.2f} is in the bottom quarter of "
            f"{len(all_sharpes):,} tested weightings. The current allocation carries more "
            f"risk than return relative to alternative weightings of the same holdings."
        )

    sharpe_explainer = html.Div([
        html.Div([
            html.P("What is the Sharpe ratio?", style={
                'fontSize': '14px', 'fontWeight': '600', 'color': '#111',
                'margin': '0 0 6px',
            }),
            html.P(
                "The Sharpe ratio measures how much return your portfolio earns per unit of risk. "
                "It is calculated as: (Portfolio Return − Risk-Free Rate) ÷ Annualised Volatility. "
                "A Sharpe above 1.0 is generally considered good; above 2.0 is excellent. "
                "Negative values mean the portfolio underperforms even a risk-free asset.",
                style={'fontSize': '14px', 'color': '#555', 'margin': '0',
                       'lineHeight': '1.65'},
            ),
        ], style={
            'padding': '16px', 'background': '#fafafa',
            'borderRadius': '10px', 'marginTop': '20px',
            'borderLeft': '3px solid #e0e0e0',
        }),
        html.Div([
            html.P("Your result", style={
                'fontSize': '14px', 'fontWeight': '600', 'color': '#111',
                'margin': '0 0 6px',
            }),
            html.P(interpretation, style={
                'fontSize': '14px', 'color': '#555', 'margin': '0',
                'lineHeight': '1.65',
            }),
        ], style={
            'padding': '16px', 'background': '#fafafa',
            'borderRadius': '10px', 'marginTop': '10px',
            'borderLeft': f'3px solid {rank_color}',
        }),
    ])

    return html.Div([
        section_label("Efficient Frontier (90-day)"),
        html.P("2,500 randomly-weighted versions of your holdings · "
               "colour = Sharpe ratio (darker is higher) · ★ = your actual allocation",
               style={'fontSize': '14px', 'color': '#777', 'margin': '-8px 0 12px'}),
        dcc.Graph(figure=fig, config={'displayModeBar': False}),
        sharpe_explainer,
    ], style=CARD)



# ── 7. Historical Scenario Stress Test ─────────────────────────────────────────
# Preset crisis scenarios with their known drawdowns by asset class.
# Applied to the current portfolio using sector/geography mapping.

_SCENARIOS = [
    {
        'name':       '2008 Global Financial Crisis',
        'period':     'Sep 2008 – Mar 2009',
        'shocks': {
            'Financials':          -77, 'Real Estate':         -68,
            'Industrials':         -52, 'Consumer Discretionary': -50,
            'Materials':           -48, 'Technology':          -47,
            'Energy':              -45, 'Health Care':         -28,
            'Consumer Staples':    -25, 'Utilities':           -33,
            'Communication Services': -42, 'Unknown':          -45,
        },
        'market_drop': -56,
    },
    {
        'name':       'COVID-19 Crash',
        'period':     'Feb – Mar 2020',
        'shocks': {
            'Energy':              -55, 'Financials':          -40,
            'Industrials':         -40, 'Consumer Discretionary': -38,
            'Real Estate':         -35, 'Materials':           -33,
            'Technology':          -25, 'Communication Services': -22,
            'Health Care':         -18, 'Consumer Staples':    -15,
            'Utilities':           -20, 'Unknown':             -30,
        },
        'market_drop': -34,
    },
    {
        'name':       'Dot-com Bust',
        'period':     'Mar 2000 – Oct 2002',
        'shocks': {
            'Technology':          -82, 'Communication Services': -72,
            'Consumer Discretionary': -42, 'Industrials':        -30,
            'Financials':          -20, 'Materials':             -22,
            'Energy':              +10, 'Health Care':           -25,
            'Consumer Staples':     -5, 'Utilities':             -55,
            'Real Estate':          +5, 'Unknown':               -40,
        },
        'market_drop': -49,
    },
    {
        'name':       '2022 Rate Hike Bear Market',
        'period':     'Jan – Dec 2022',
        'shocks': {
            'Technology':          -38, 'Communication Services': -40,
            'Consumer Discretionary': -37, 'Real Estate':        -28,
            'Financials':          -15, 'Industrials':           -10,
            'Materials':            -8, 'Energy':               +59,
            'Health Care':          -6, 'Consumer Staples':      -4,
            'Utilities':            -1, 'Unknown':              -20,
        },
        'market_drop': -19,
    },
    {
        'name':       'Black Monday 1987',
        'period':     'Oct 19, 1987',
        'shocks': {
            'Financials':          -30, 'Technology':           -28,
            'Industrials':         -25, 'Consumer Discretionary': -24,
            'Materials':           -22, 'Energy':               -20,
            'Health Care':         -18, 'Consumer Staples':     -16,
            'Communication Services': -25, 'Utilities':         -20,
            'Real Estate':         -22, 'Unknown':              -22,
        },
        'market_drop': -22,
    },
]


@app.callback(
    Output('scenarios-section', 'children'),
    Input('market-intel-data', 'data'),
    State('portfolio-data', 'data'),
)
def render_scenarios(intel, port_data):
    try:
        return _render_scenarios_inner(intel, port_data)
    except Exception as e:
        return _intel_error("Historical Scenarios", e)


def _render_scenarios_inner(intel, port_data):
    if not intel:
        return _intel_loading('scenario data')
    if not port_data or 'positions' not in port_data:
        return None

    sg        = intel.get('sector_geo', {})
    positions = port_data['positions']
    rate      = port_data.get('account', {}).get('eurusd_rate', 1.08)
    total_val = sum(p['market_value'] for p in positions) or 1

    # Compute scenario impacts
    scenario_results = []
    for sc in _SCENARIOS:
        total_impact = 0.0
        position_impacts = []
        for p in positions:
            sym    = p['ticker']
            val    = p['market_value']
            sector = sg.get(sym, {}).get('sector', 'Unknown')
            shock  = sc['shocks'].get(sector, sc['market_drop'])
            impact = round(val * shock / 100, 0)
            total_impact += impact
            position_impacts.append((sym, sector, shock, impact))

        pct_impact = round(total_impact / total_val * 100, 1)
        worst_pos  = min(position_impacts, key=lambda x: x[3])
        scenario_results.append({
            'name':       sc['name'],
            'period':     sc['period'],
            'market_drop': sc['market_drop'],
            'total_impact': round(total_impact, 0),
            'pct_impact':  pct_impact,
            'positions':   position_impacts,
            'worst':       worst_pos,
        })

    # Sort by total impact (worst first)
    scenario_results.sort(key=lambda x: x['total_impact'])

    # ── Summary card for each scenario ───────────────────────────────────────
    def scenario_card(s):
        imp_color  = '#dc2626' if s['total_impact'] < 0 else '#16a34a'
        imp_bg     = '#fef2f2' if s['total_impact'] < 0 else '#f0fdf4'
        imp_brd    = '#fecaca' if s['total_impact'] < 0 else '#bbf7d0'

        worst_sym, _, worst_shock, worst_imp = s['worst']

        return html.Div([
            html.Div([
                html.Span(s['name'], style={'fontSize': '15px', 'fontWeight': '600',
                                            'color': '#111'}),
                html.Span(f"  ·  {s['period']}", style={'fontSize': '13px', 'color': '#888'}),
            ], style={'marginBottom': '8px'}),

            html.Div([
                html.Div([
                    html.P("Portfolio Impact", style={'fontSize': '12px', 'color': '#777',
                                                       'margin': '0 0 1px',
                                                       'textTransform': 'uppercase',
                                                       'letterSpacing': '0.05em'}),
                    html.Span(f"${s['total_impact']:+,.0f}",
                              style={'fontSize': '16px', 'fontWeight': '600',
                                     'color': imp_color}),
                    html.Span(f"  {s['pct_impact']:+.1f}%",
                              style={'fontSize': '13px', 'color': '#888'}),
                ]),
                html.Div([
                    html.P("S&P 500", style={'fontSize': '12px', 'color': '#777',
                                             'margin': '0 0 1px',
                                             'textTransform': 'uppercase',
                                             'letterSpacing': '0.05em'}),
                    html.Span(f"{s['market_drop']:+.0f}%",
                              style={'fontSize': '16px', 'fontWeight': '600',
                                     'color': '#dc2626'}),
                ]),
                html.Div([
                    html.P("Hardest Hit", style={'fontSize': '12px', 'color': '#777',
                                                  'margin': '0 0 1px',
                                                  'textTransform': 'uppercase',
                                                  'letterSpacing': '0.05em'}),
                    html.Span(worst_sym, style={'fontSize': '16px', 'fontWeight': '600',
                                                'color': '#111'}),
                    html.Span(f"  {worst_shock:+.0f}%",
                              style={'fontSize': '13px', 'color': '#888'}),
                ]),
            ], style={'display': 'flex', 'gap': '24px'}),
        ], style={
            'background': imp_bg, 'borderRadius': '8px', 'padding': '10px 14px',
            'border': f'0.5px solid {imp_brd}', 'marginBottom': '8px',
        })

    scenario_cards = [scenario_card(s) for s in scenario_results]

    # ── Worst scenario bar chart ──────────────────────────────────────────────
    names   = [s['name'].split(' (')[0][:30] for s in scenario_results]
    impacts = [s['pct_impact'] for s in scenario_results]
    colors  = ['#dc2626' if v < 0 else '#16a34a' for v in impacts]

    bar_fig = go.Figure(go.Bar(
        x=impacts, y=names,
        orientation='h',
        marker_color=colors,
        text=[f'{v:+.1f}%' for v in impacts],
        textposition='outside',
        textfont=dict(size=11),
        hovertemplate='%{y}<br>Portfolio impact: <b>%{x:+.1f}%</b><extra></extra>',
    ))
    bar_fig.add_vline(x=0, line_color='#e0e0e0', line_width=1)
    bar_fig.update_layout(
        margin=dict(t=8, b=8, l=0, r=60),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        showlegend=False,
        xaxis=dict(showgrid=True, gridcolor='#f5f5f5', zeroline=False,
                   tickfont=dict(size=10, color='#bbb'), ticksuffix='%'),
        yaxis=dict(tickfont=dict(size=11, color='#555'), autorange='reversed'),
        height=max(120, len(scenario_results) * 28 + 16),
    )

    return html.Div([
        section_label("Historical Scenario Analysis"),
        html.P("Estimated portfolio impact based on sector-level drawdowns from past crises. "
               "Shocks are applied per sector — positions without sector data use the broad market drop.",
               style={'fontSize': '14px', 'color': '#777', 'margin': '-8px 0 16px',
                      'lineHeight': '1.6'}),
        dcc.Graph(figure=bar_fig, config={'displayModeBar': False}),
        html.Div(style={'marginTop': '17px'}),
        html.Div(scenario_cards, style={
            'display': 'grid',
            'gridTemplateColumns': 'repeat(2, 1fr)',
            'gap': '8px',
        }),
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
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(_fetch, 'buffett',  get_buffett_indicator),
            pool.submit(_fetch, 'sp500_pe', get_sp500_pe),
            pool.submit(_fetch, 'cape',     get_shiller_cape),
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

    # Zone labels below the bar, centred within each segment
    label_spans = []
    prev = 0.0
    for label, seg_max, color in segments:
        mid_pct = ((prev + seg_max) / 2) / display_max * 100
        label_spans.append(html.Span(label, style={
            'position': 'absolute',
            'left': f'{mid_pct:.2f}%',
            'transform': 'translateX(-50%)',
            'fontSize': '10px',
            'color': color,
            'fontWeight': '600',
            'whiteSpace': 'nowrap',
            'letterSpacing': '0.01em',
        }))
        prev = seg_max

    labels_row = html.Div(label_spans, style={
        'position': 'relative',
        'height': '15px',
        'marginTop': '4px',
        'overflow': 'visible',
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

    buffett_d = data.get('buffett')
    pe_d      = data.get('sp500_pe')
    cape_d    = data.get('cape')

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
        b_body = html.Div([
            big_value(f'{bv:.1f}%', bcolor),
            zone_badge(blabel, bcolor),
            _val_zone_bar(bv, [
                ('Undervalued',        75,  '#16a34a'),
                ('Fairly Valued',     100,  '#22c55e'),
                ('Modestly Overval.', 120,  '#eab308'),
                ('Overvalued',        150,  '#f97316'),
                ('Strongly Overval.', 200,  '#dc2626'),
            ], display_max=200),
            html.Div([
                html.Span(f"Mkt Cap  ${buffett_d['market_cap_t']:.1f}T",
                          style={'fontSize': '13px', 'color': '#666'}),
                html.Span(' · ', style={'color': '#bbb', 'fontSize': '13px'}),
                html.Span(f"GDP  ${buffett_d['gdp_t']:.1f}T ({buffett_d['gdp_year']})",
                          style={'fontSize': '13px', 'color': '#999'}),
            ], style={'marginTop': '8px'}),
        ])
        b_foot = (
            'Warren Buffett\'s favourite macro yardstick: '
            '"The best single measure of where valuations stand at any given moment." '
            'Historical fair value: ~100%. Above 150% signals significant overvaluation.'
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
            'Uses 10 years of inflation-adjusted earnings to smooth cycles. '
            f'Historical average: ~{cape_d["hist_mean"]:.0f}×. '
            'Developed by Robert Shiller (Yale). '
            'Above 30× has historically preceded periods of low returns.'
        )
    else:
        cape_body = _val_unavailable()
        cape_foot = None

    cards = html.Div([
        metric_card('Buffett Indicator', 'Total US Mkt Cap / GDP', b_body, b_foot),
        metric_card('S&P 500 P/E Ratio', 'Price-to-earnings (trailing 12M)', pe_body, pe_foot),
        metric_card('Shiller CAPE', 'Cyclically Adjusted P/E (10-yr)', cape_body, cape_foot),
    ], style={'display': 'flex', 'gap': '14px', 'alignItems': 'flex-start'})

    # ── CAPE historical chart ─────────────────────────────────────────────────
    if cape_d and cape_d.get('dates'):
        mean_val   = cape_d['hist_mean']
        dates_plot = cape_d['dates']
        vals_plot  = cape_d['values']

        fig = go.Figure()

        # Zone background bands
        bands = [
            (0,   15, 'rgba(22,163,74,0.07)'),
            (15,  20, 'rgba(34,197,94,0.07)'),
            (20,  25, 'rgba(234,179,8,0.07)'),
            (25,  30, 'rgba(249,115,22,0.07)'),
            (30,  55, 'rgba(220,38,38,0.07)'),
        ]
        for y0, y1, fill in bands:
            fig.add_hrect(y0=y0, y1=y1, fillcolor=fill,
                          layer='below', line_width=0)

        # Historical mean line
        fig.add_hline(y=mean_val, line_dash='dot',
                      line_color='#aaa', line_width=1,
                      annotation_text=f'Hist. mean {mean_val:.0f}×',
                      annotation_position='top left',
                      annotation_font=dict(size=10, color='#aaa'))

        # CAPE line
        fig.add_trace(go.Scatter(
            x=dates_plot, y=vals_plot,
            mode='lines',
            line=dict(color='#378ADD', width=2),
            name='Shiller CAPE',
            hovertemplate='%{x}  ·  CAPE <b>%{y:.1f}×</b><extra></extra>',
        ))

        # Current value dot
        fig.add_trace(go.Scatter(
            x=[dates_plot[-1]], y=[vals_plot[-1]],
            mode='markers+text',
            marker=dict(color=ccolor if cape_d else '#378ADD', size=10,
                        line=dict(color='#fff', width=2)),
            text=[f'  {vals_plot[-1]:.1f}×'],
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
                       ticksuffix='×', title=None),
            height=220,
        )

        cape_chart = html.Div([
            html.P("Shiller CAPE — 50-year history", style={
                'fontSize': '13px', 'color': '#666', 'margin': '20px 0 4px',
                'textTransform': 'uppercase', 'letterSpacing': '0.05em',
            }),
            html.P("Green = undervalued  ·  Yellow = overvalued  ·  Red = extremely overvalued",
                   style={'fontSize': '13px', 'color': '#888', 'margin': '0 0 6px'}),
            dcc.Graph(figure=fig, config={'displayModeBar': False}),
        ])
    else:
        cape_chart = None

    return html.Div([
        section_label("Market Valuation"),
        cards,
        cape_chart,
    ], style=CARD)
