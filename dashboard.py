import dash
from dash import dcc, html
from dash.dependencies import Input, Output
from datetime import datetime
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from ibkr_client import fetch_all_data
from data_processor import process_positions, get_summary
from database import init_db, save_snapshot

app = dash.Dash(__name__)

# ── Helpers ────────────────────────────────────────────────────────────────────

def to_eur(usd, rate):
    return usd / rate if rate else usd

CARD = {'border': '0.5px solid #ebebeb', 'borderRadius': '12px', 'padding': '20px'}

def section_label(text):
    return html.P(text, style={
        'fontSize': '11px', 'color': '#bbb', 'margin': '0 0 16px',
        'textTransform': 'uppercase', 'letterSpacing': '0.06em',
    })

def make_table(cols, rows):
    th = {'fontSize': '11px', 'color': '#bbb', 'fontWeight': '400',
          'padding': '0 12px 12px', 'textTransform': 'uppercase', 'letterSpacing': '0.04em',
          'borderBottom': '0.5px solid #f0f0f0'}
    header = html.Tr([
        html.Th(c, style={**th, 'textAlign': 'right' if i > 0 else 'left'})
        for i, c in enumerate(cols)
    ])
    return html.Table([html.Thead(header), html.Tbody(rows)],
                      style={'width': '100%', 'borderCollapse': 'collapse', 'fontSize': '13px'})

def badge(text, color, bg, border):
    return html.Span(text, style={
        'fontSize': '12px', 'color': color, 'background': bg,
        'padding': '4px 10px', 'borderRadius': '20px', 'border': f'0.5px solid {border}',
    })

def status_banner(icon, title, body, color):
    return html.Div([
        html.Div(icon, style={'fontSize': '32px', 'marginBottom': '12px'}),
        html.P(title, style={'fontSize': '16px', 'fontWeight': '600', 'color': '#111', 'margin': '0 0 6px'}),
        html.P(body, style={'fontSize': '13px', 'color': '#888', 'margin': '0', 'lineHeight': '1.6'}),
    ], style={
        'textAlign': 'center', 'padding': '48px 32px',
        'background': color, 'borderRadius': '12px',
        'border': '0.5px solid #ebebeb',
    })

# ── Layout ─────────────────────────────────────────────────────────────────────

app.layout = html.Div([

    # Header
    html.Div([
        html.Div([
            html.H1("Portfolio", style={'margin': '0', 'fontSize': '22px', 'fontWeight': '600', 'color': '#111'}),
            html.P(id='last-updated', style={'margin': '4px 0 0', 'color': '#bbb', 'fontSize': '12px'}),
        ]),
        html.Div(id='connection-badge'),
    ], style={'marginBottom': '28px', 'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'flex-start'}),

    # Status / loading banner (hidden when data loaded)
    html.Div(id='status-banner', style={'marginBottom': '24px'}),

    # 4 summary cards
    html.Div(id='summary-cards', style={
        'display': 'grid', 'gridTemplateColumns': 'repeat(4, 1fr)',
        'gap': '12px', 'marginBottom': '24px',
    }),

    # Holdings + Donut
    html.Div([
        html.Div([
            html.Div([
                section_label("Holdings"),
                html.Span(id='positions-count', style={
                    'fontSize': '12px', 'color': '#bbb',
                    'marginTop': '-12px', 'display': 'block', 'marginBottom': '16px',
                }),
            ]),
            html.Div(id='holdings-table'),
        ], style={**CARD, 'flex': '1', 'alignSelf': 'flex-start'}),

        html.Div([
            section_label("Allocation"),
            dcc.Graph(id='donut-chart', config={'displayModeBar': False}, style={'height': '260px'}),
        ], style={**CARD, 'width': '260px', 'alignSelf': 'flex-start'}),
    ], style={'display': 'flex', 'gap': '12px'}),

    dcc.Interval(id='refresh-interval', interval=60000, n_intervals=0),
    dcc.Store(id='portfolio-data'),
    dcc.Store(id='connection-status', data='loading'),

], style={
    'fontFamily': '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    'padding': '48px 64px',
    'maxWidth': '1400px',
    'margin': '0 auto',
    'backgroundColor': '#fff',
    'color': '#111',
})


# ── Data fetch ─────────────────────────────────────────────────────────────────

@app.callback(
    Output('portfolio-data', 'data'),
    Output('connection-status', 'data'),
    Input('refresh-interval', 'n_intervals'),
)
def fetch_data(n):
    raw = fetch_all_data()
    if not raw or not raw['positions']:
        status = 'no_positions' if raw else 'disconnected'
        return {}, status
    df = process_positions(raw['positions'], raw.get('market_data', {}))
    if df.empty:
        return {}, 'no_positions'
    summary = get_summary(df)
    save_snapshot(summary['total_value'], summary['total_unrealized_pnl'])
    return {
        'positions':   df.to_dict('records'),
        'summary':     summary,
        'account':     raw['account'],
        'orders':      raw['orders'],
        'trade_stats': raw['trade_stats'],
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

    return None, badge("● Live", '#16a34a', '#f0fdf4', '#bbf7d0'), ts


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

    def card(label, eur_val, pnl_pct=None, is_pnl=False):
        positive = eur_val >= 0
        accent = ('#16a34a' if positive else '#dc2626') if is_pnl else '#111'
        val_str = f"€{eur_val:+,.2f}" if is_pnl else f"€{eur_val:,.2f}"
        usd_str = f"${eur_val * rate:,.2f}"
        return html.Div([
            html.P(label, style={
                'fontSize': '11px', 'color': '#bbb', 'margin': '0 0 10px',
                'textTransform': 'uppercase', 'letterSpacing': '0.05em',
            }),
            html.P(val_str, style={
                'fontSize': '24px', 'fontWeight': '600', 'margin': '0',
                'color': accent if is_pnl else '#111', 'letterSpacing': '-0.5px',
            }),
            html.Div([
                html.Span(usd_str, style={'fontSize': '12px', 'color': '#ccc'}),
                html.Span(f" · {pnl_pct:+.2f}%",
                          style={'fontSize': '12px', 'color': accent}) if pnl_pct is not None else None,
            ], style={'marginTop': '4px'}),
        ], style={
            'background': '#fafafa', 'borderRadius': '10px', 'padding': '18px',
            'borderLeft': f'3px solid {"#ebebeb" if not is_pnl else accent}',
        })

    return [
        card("Total Value",    to_eur(total_val, rate)),
        card("Unrealized P&L", to_eur(unreal_pnl, rate), pnl_pct=pnl_pct, is_pnl=True),
        card("Today's P&L",    to_eur(daily_pnl, rate) if daily_pnl is not None else 0, is_pnl=True),
        card("Cash",           cash_eur),
    ]


# ── Holdings ───────────────────────────────────────────────────────────────────

@app.callback(
    Output('holdings-table', 'children'),
    Output('positions-count', 'children'),
    Input('portfolio-data', 'data'),
)
def update_holdings(data):
    if not data or 'positions' not in data:
        return html.P("—", style={'color': '#ccc', 'fontSize': '13px'}), ''
    df = pd.DataFrame(data['positions'])
    rate = data.get('account', {}).get('eurusd_rate', 1.08)
    count = f"{len(df)} positions"

    td_r = lambda val, **kw: html.Td(val, style={'textAlign': 'right', 'padding': '11px 12px', **kw})
    td_l = lambda val, **kw: html.Td(val, style={'textAlign': 'left',  'padding': '11px 12px', **kw})

    rows = []
    for _, row in df.iterrows():
        pnl_pct  = row['pnl_pct']
        unreal   = row['unrealized_pnl']
        positive = pnl_pct >= 0
        pill_color = '#16a34a' if positive else '#dc2626'
        pill_bg    = '#f0fdf4' if positive else '#fef2f2'

        rows.append(html.Tr([
            td_l(html.Span(row['ticker'], style={'fontWeight': '600', 'color': '#111'})),
            td_r(str(int(row['quantity'])), color='#666'),
            td_r(f"${row['avg_cost']:,.2f}", color='#666'),
            td_r(f"${row['current_price']:,.2f}", color='#111'),
            td_r([
                html.Span(f"${row['market_value']:,.2f}",
                          style={'fontWeight': '500', 'color': '#111', 'display': 'block'}),
                html.Span(f"€{to_eur(row['market_value'], rate):,.2f}",
                          style={'fontSize': '11px', 'color': '#ccc'}),
            ]),
            td_r([
                html.Span(f"{pnl_pct:+.2f}%", style={
                    'background': pill_bg, 'color': pill_color,
                    'padding': '3px 9px', 'borderRadius': '20px',
                    'fontSize': '12px', 'fontWeight': '500',
                }),
                html.Span(f"${unreal:+,.2f}",
                          style={'fontSize': '11px', 'color': '#ccc', 'display': 'block', 'marginTop': '3px'}),
            ]),
            td_r(f"{row['allocation_pct']:.1f}%", color='#999'),
        ], style={'borderTop': '0.5px solid #f5f5f5'}))

    return make_table(['Ticker', 'Qty', 'Avg Cost', 'Price', 'Value', 'P&L', 'Weight'], rows), count


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
