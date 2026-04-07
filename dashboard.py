import dash
from dash import dcc, html, no_update, dash_table
from dash.dependencies import Input, Output, State, ALL
from dash import ctx
from datetime import datetime
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import io
from ibkr_client import fetch_all_data
from data_processor import process_positions, get_summary
from database import (save_snapshot, get_all_snapshots, bulk_insert_snapshots,
                      save_dividend_events, get_dividend_events,
                      save_rebalance_targets, get_rebalance_targets)
from analytics import calculate_analytics, get_benchmark_series, get_dividend_data_yf

app = dash.Dash(__name__, suppress_callback_exceptions=True)

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
                html.Div([
                    section_label("Holdings"),
                    html.Span(id='stale-price-badge'),
                ], style={'display': 'flex', 'alignItems': 'center', 'gap': '10px', 'marginBottom': '0px'}),
                html.Span(id='positions-count', style={
                    'fontSize': '12px', 'color': '#bbb',
                    'display': 'block', 'marginBottom': '16px',
                }),
            ]),
            html.Div(id='holdings-table'),
        ], style={**CARD, 'flex': '1', 'alignSelf': 'flex-start'}),

        html.Div([
            section_label("Allocation"),
            dcc.Graph(id='donut-chart', config={'displayModeBar': False}, style={'height': '260px'}),
        ], style={**CARD, 'width': '260px', 'alignSelf': 'flex-start'}),
    ], style={'display': 'flex', 'gap': '12px'}),

    # Position detail panel (shown on row click)
    html.Div(id='position-detail'),

    # Performance analytics
    html.Div([
        html.Div([
            html.Span("Compare vs", style={
                'fontSize': '11px', 'color': '#bbb',
                'textTransform': 'uppercase', 'letterSpacing': '0.05em',
                'marginRight': '10px',
            }),
            dcc.Dropdown(
                id='benchmark-selector',
                options=[
                    {'label': 'S&P 500 (SPY)',       'value': 'SPY'},
                    {'label': 'Nasdaq 100 (QQQ)',     'value': 'QQQ'},
                    {'label': 'MSCI World (IWDA.L)',  'value': 'IWDA.L'},
                    {'label': 'DAX (EXS1.DE)',        'value': 'EXS1.DE'},
                    {'label': 'None',                 'value': ''},
                ],
                value='SPY',
                clearable=False,
                style={'width': '210px', 'fontSize': '13px'},
            ),
        ], style={'display': 'flex', 'alignItems': 'center',
                  'justifyContent': 'flex-end', 'marginBottom': '8px'}),
        html.Div(id='performance-section'),
    ], style={'marginTop': '24px'}),

    # Dividends
    html.Div(id='dividend-section', style={'marginTop': '24px'}),

    # Action buttons row
    html.Div([
        html.Button("↓ Export PDF", id='export-pdf-btn', n_clicks=0, style={
            'background': '#fff', 'border': '0.5px solid #d0d0d0', 'borderRadius': '8px',
            'padding': '8px 18px', 'fontSize': '13px', 'cursor': 'pointer',
            'color': '#333', 'fontFamily': 'inherit',
        }),
        html.Button("⚖ Rebalance", id='rebalance-toggle-btn', n_clicks=0, style={
            'background': '#fff', 'border': '0.5px solid #d0d0d0', 'borderRadius': '8px',
            'padding': '8px 18px', 'fontSize': '13px', 'cursor': 'pointer',
            'color': '#333', 'fontFamily': 'inherit',
        }),
    ], style={'display': 'flex', 'gap': '8px', 'marginTop': '20px'}),

    # Rebalancing calculator (toggled by button)
    html.Div(id='rebalance-section', style={'marginTop': '16px'}),

    dcc.Download(id='download-pdf'),
    dcc.Interval(id='refresh-interval', interval=60000, n_intervals=0),
    dcc.Store(id='portfolio-data'),
    dcc.Store(id='connection-status', data='loading'),
    dcc.Store(id='selected-ticker', data=None),
    dcc.Store(id='rebalance-open', data=False),

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
    # IBKR tick-59 data first; yfinance fills any gaps
    div_data = raw.get('div_data', {})
    tickers  = [p['ticker'] for p in raw['positions']]
    missing  = [t for t in tickers if t not in div_data]
    if missing:
        div_data.update(get_dividend_data_yf(missing))
    if div_data:
        save_dividend_events(div_data, raw['positions'])
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
    Output('stale-price-badge', 'children'),
    Input('portfolio-data', 'data'),
)
def update_holdings(data):
    if not data or 'positions' not in data:
        return html.P("—", style={'color': '#ccc', 'fontSize': '13px'}), '', None
    df = pd.DataFrame(data['positions'])
    rate = data.get('account', {}).get('eurusd_rate', 1.08)
    count = f"{len(df)} positions"
    any_stale = df.get('price_stale', pd.Series(False)).any()
    stale_badge = html.Span("● Market closed · last-close prices",
                            style={
                                'fontSize': '11px', 'color': '#b45309',
                                'background': '#fffbeb', 'border': '0.5px solid #fde68a',
                                'padding': '3px 9px', 'borderRadius': '20px',
                                'marginTop': '-10px',
                            }) if any_stale else None

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
            td_r(
                [html.Span(f"~${row['current_price']:,.2f}", style={'color': '#b45309'})]
                if row.get('price_stale') else f"${row['current_price']:,.2f}",
                color='#111'
            ),
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
        ],
        id={'type': 'pos-row', 'index': row['ticker']},
        n_clicks=0,
        style={'borderTop': '0.5px solid #f5f5f5', 'cursor': 'pointer'}))

    return make_table(['Ticker', 'Qty', 'Avg Cost', 'Price', 'Value', 'P&L', 'Weight'], rows), count, stale_badge


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


# ── Export ─────────────────────────────────────────────────────────────────────

@app.callback(
    Output('download-pdf', 'data'),
    Input('export-pdf-btn', 'n_clicks'),
    State('portfolio-data', 'data'),
    prevent_initial_call=True,
)
def export_pdf(n, data):
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
    Input({'type': 'pos-row', 'index': ALL}, 'n_clicks'),
    State('selected-ticker', 'data'),
    prevent_initial_call=True,
)
def select_ticker(clicks, current):
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return no_update
    ticker = ctx.triggered_id['index']
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
                'width': '12px', 'height': '12px', 'borderRadius': '50%',
                'background': '#378ADD', 'border': '2px solid #fff',
                'boxShadow': '0 0 0 1.5px #378ADD',
                'transform': 'translateX(-50%)',
            }),
        ], style={'position': 'relative', 'height': '12px', 'margin': '8px 0'}),
        html.Div([
            html.Span(f"${low:,.2f}", style={'fontSize': '11px', 'color': '#aaa'}),
            html.Span(f"{pct:.0f}% of range",
                      style={'fontSize': '11px', 'color': '#aaa', 'position': 'absolute',
                             'left': '50%', 'transform': 'translateX(-50%)'}),
            html.Span(f"${high:,.2f}", style={'fontSize': '11px', 'color': '#aaa'}),
        ], style={'display': 'flex', 'justifyContent': 'space-between', 'position': 'relative'}),
    ])


def _stat(label, value, accent=None):
    return html.Div([
        html.P(label, style={
            'fontSize': '10px', 'color': '#bbb', 'margin': '0 0 4px',
            'textTransform': 'uppercase', 'letterSpacing': '0.05em',
        }),
        html.P(value, style={
            'fontSize': '15px', 'fontWeight': '500', 'margin': '0',
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
    ], style={'display': 'flex', 'gap': '32px', 'flexWrap': 'wrap', 'marginTop': '16px'})

    # 52-week range
    if (low_52w and high_52w and low_52w == low_52w and high_52w == high_52w
            and high_52w > low_52w):
        range_section = html.Div([
            html.P("52-Week Range", style={
                'fontSize': '10px', 'color': '#bbb', 'margin': '0 0 0',
                'textTransform': 'uppercase', 'letterSpacing': '0.05em',
            }),
            _range_bar(low_52w, high_52w, price),
        ], style={'marginTop': '16px'})
    else:
        range_section = None

    return html.Div([
        # Header
        html.Div([
            html.Span(ticker, style={'fontWeight': '700', 'fontSize': '17px', 'color': '#111'}),
            html.Span(f"${price:,.2f}",
                      style={'fontSize': '17px', 'color': '#111', 'marginLeft': '10px'}),
            html.Span(chg_str,
                      style={'fontSize': '13px', 'color': chg_color, 'marginLeft': '10px'}),
        ], style={'display': 'flex', 'alignItems': 'center'}),
        range_section,
        stats,
    ], style={
        **CARD,
        'marginTop': '12px',
        'background': '#fafafa',
        'borderLeft': '3px solid #378ADD',
    })


# ── Rebalancing toggle ────────────────────────────────────────────────────────

@app.callback(
    Output('rebalance-open', 'data'),
    Output('rebalance-toggle-btn', 'children'),
    Output('rebalance-toggle-btn', 'style'),
    Input('rebalance-toggle-btn', 'n_clicks'),
    State('rebalance-open', 'data'),
    prevent_initial_call=True,
)
def toggle_rebalance(n, is_open):
    now_open = not is_open
    label = "✕ Close Rebalancer" if now_open else "⚖ Rebalance"
    style = {
        'background': '#111' if now_open else '#fff',
        'border': '0.5px solid #d0d0d0', 'borderRadius': '8px',
        'padding': '8px 18px', 'fontSize': '13px', 'cursor': 'pointer',
        'color': '#fff' if now_open else '#333', 'fontFamily': 'inherit',
    }
    return now_open, label, style


# ── Rebalancing calculator ────────────────────────────────────────────────────

_TH = {
    'fontSize': '11px', 'color': '#bbb', 'fontWeight': '400',
    'textTransform': 'uppercase', 'letterSpacing': '0.04em',
    'backgroundColor': '#fff', 'borderBottom': '0.5px solid #f0f0f0',
    'padding': '0 12px 12px',
}
_TD = {
    'fontFamily': '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    'fontSize': '13px', 'padding': '11px 12px',
    'borderBottom': '0.5px solid #f5f5f5', 'backgroundColor': '#fff',
}
_BTN = {
    'background': '#fff', 'border': '0.5px solid #d0d0d0', 'borderRadius': '8px',
    'padding': '7px 14px', 'fontSize': '12px', 'cursor': 'pointer',
    'color': '#333', 'fontFamily': 'inherit',
}
_BTN_PRIMARY = {**_BTN, 'background': '#111', 'color': '#fff', 'border': 'none'}


@app.callback(
    Output('rebalance-section', 'children'),
    Input('portfolio-data', 'data'),
    Input('rebalance-open', 'data'),
)
def render_rebalance_shell(data, is_open):
    """Render the rebalance calculator when open, nothing when closed."""
    if not is_open or not data or 'positions' not in data:
        return None

    positions  = data['positions']
    saved      = get_rebalance_targets()
    n          = len(positions)
    equal_pct  = round(100 / n, 2) if n else 0

    rows = []
    for p in sorted(positions, key=lambda x: x['market_value'], reverse=True):
        t   = p['ticker']
        cur = round(p['allocation_pct'], 2)
        tgt = round(saved.get(t, cur), 2)
        rows.append({
            'ticker':      t,
            'current_pct': cur,
            'target_pct':  tgt,
            'price':       p['current_price'],
            'market_value':p['market_value'],
            'quantity':    p['quantity'],
        })

    table = dash_table.DataTable(
        id='rebalance-table',
        data=rows,
        columns=[
            {'name': 'Ticker',    'id': 'ticker',      'editable': False},
            {'name': 'Current %', 'id': 'current_pct', 'editable': False, 'type': 'numeric'},
            {'name': 'Target %',  'id': 'target_pct',  'editable': True,  'type': 'numeric'},
            # hidden cols used in summary calc
            {'name': 'price',        'id': 'price',        'editable': False},
            {'name': 'market_value', 'id': 'market_value', 'editable': False},
            {'name': 'quantity',     'id': 'quantity',     'editable': False},
        ],
        hidden_columns=['price', 'market_value', 'quantity'],
        style_as_list_view=True,
        style_header=_TH,
        style_cell=_TD,
        style_cell_conditional=[
            {'if': {'column_id': 'ticker'},      'textAlign': 'left', 'fontWeight': '600'},
            {'if': {'column_id': 'current_pct'}, 'textAlign': 'right', 'color': '#999'},
            {'if': {'column_id': 'target_pct'},  'textAlign': 'right', 'backgroundColor': '#f8faff'},
        ],
        style_data_conditional=[
            {'if': {'column_id': 'target_pct'}, 'cursor': 'text'},
        ],
    )

    return html.Div([
        # Header
        html.Div([
            html.Div([
                section_label("Rebalancing Calculator"),
                html.Span(f"{n} positions",
                          style={'fontSize': '11px', 'color': '#ccc',
                                 'marginTop': '-12px', 'display': 'block', 'marginBottom': '16px'}),
            ]),
            html.Div([
                html.Button("Equal Weight", id='preset-equal',  n_clicks=0, style=_BTN),
                html.Button("Reset",        id='preset-reset',  n_clicks=0, style=_BTN),
                html.Button("Save Targets", id='save-targets',  n_clicks=0, style=_BTN_PRIMARY),
            ], style={'display': 'flex', 'gap': '8px', 'alignItems': 'center'}),
        ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'flex-start'}),

        # Editable allocation table
        table,

        # Trade summary (filled by callback)
        html.Div(id='rebalance-summary', style={'marginTop': '20px'}),

        # Save feedback
        html.Span(id='save-feedback',
                  style={'fontSize': '12px', 'color': '#16a34a', 'marginLeft': '8px'}),
    ], style=CARD)


@app.callback(
    Output('rebalance-table', 'data'),
    Input('preset-equal', 'n_clicks'),
    Input('preset-reset', 'n_clicks'),
    State('rebalance-table', 'data'),
    State('portfolio-data', 'data'),
    prevent_initial_call=True,
)
def apply_preset(eq_clicks, reset_clicks, table_data, port_data):
    if not table_data:
        return no_update
    triggered = ctx.triggered_id
    rows = []
    n    = len(table_data)
    for row in table_data:
        r = dict(row)
        if triggered == 'preset-equal':
            r['target_pct'] = round(100 / n, 2)
        elif triggered == 'preset-reset':
            r['target_pct'] = r['current_pct']
        rows.append(r)
    return rows


@app.callback(
    Output('rebalance-summary', 'children'),
    Input('rebalance-table', 'data'),
    State('portfolio-data', 'data'),
)
def compute_rebalance(table_data, port_data):
    if not table_data or not port_data:
        return None

    account      = port_data.get('account', {})
    rate         = account.get('eurusd_rate', 1.08)
    cash_usd     = account.get('cash_usd', 0) or 0
    total_equity = sum(r['market_value'] for r in table_data)
    total_target = sum(r.get('target_pct') or 0 for r in table_data)

    trades   = []
    buys_val = sells_val = 0.0

    for row in table_data:
        tgt_pct    = row.get('target_pct') or 0
        price      = row['price']
        cur_val    = row['market_value']
        tgt_val    = total_equity * tgt_pct / 100
        delta_val  = tgt_val - cur_val
        if price <= 0:
            continue
        # Round toward zero (don't overshoot budget)
        import math
        delta_sh = math.trunc(delta_val / price)
        actual_val = round(delta_sh * price, 2)
        if delta_sh > 0:
            action = 'BUY'
            buys_val += actual_val
        elif delta_sh < 0:
            action = 'SELL'
            sells_val += abs(actual_val)
        else:
            action = None
        trades.append({
            'ticker':     row['ticker'],
            'action':     action,
            'shares':     delta_sh,
            'est_value':  actual_val,
            'drift':      round(tgt_pct - row['current_pct'], 2),
        })

    net_cash = sells_val - buys_val
    cash_after = cash_usd + net_cash
    alloc_ok   = abs(total_target - 100) < 0.1
    cash_ok    = cash_after >= 0

    # ── Allocation warning ────────────────────────────────────────────────────
    if not alloc_ok:
        warn_color = '#dc2626' if total_target > 100 else '#b45309'
        warn_bg    = '#fef2f2' if total_target > 100 else '#fffbeb'
        warn_brd   = '#fecaca' if total_target > 100 else '#fde68a'
        alloc_warn = html.Span(
            f"Targets sum to {total_target:.1f}% — adjust to reach 100%",
            style={'fontSize': '12px', 'color': warn_color, 'background': warn_bg,
                   'border': f'0.5px solid {warn_brd}', 'borderRadius': '20px',
                   'padding': '3px 10px', 'marginBottom': '16px', 'display': 'inline-block'})
    else:
        alloc_warn = None

    # ── Trade rows ────────────────────────────────────────────────────────────
    action_rows = [t for t in trades if t['action']]
    if not action_rows:
        trade_table = html.P("Portfolio is already at target — no trades needed.",
                             style={'fontSize': '13px', 'color': '#bbb', 'margin': '0 0 16px'})
    else:
        td_r = lambda v, **kw: html.Td(v, style={'textAlign': 'right', 'padding': '9px 12px', **kw})
        td_l = lambda v, **kw: html.Td(v, style={'textAlign': 'left',  'padding': '9px 12px', **kw})
        t_rows = []
        for t in sorted(action_rows, key=lambda x: abs(x['est_value']), reverse=True):
            is_buy   = t['action'] == 'BUY'
            clr      = '#16a34a' if is_buy else '#dc2626'
            act_pill = html.Span(t['action'], style={
                'fontSize': '11px', 'fontWeight': '600', 'color': clr,
                'background': '#f0fdf4' if is_buy else '#fef2f2',
                'padding': '2px 8px', 'borderRadius': '20px',
            })
            drift_clr = '#16a34a' if t['drift'] >= 0 else '#dc2626'
            t_rows.append(html.Tr([
                td_l(html.Span(t['ticker'], style={'fontWeight': '600'})),
                td_l(act_pill),
                td_r(f"{'+' if t['shares'] > 0 else ''}{t['shares']} shares"),
                td_r(f"${abs(t['est_value']):,.2f}  ·  €{abs(t['est_value'])/rate:,.2f}"),
                td_r(f"{t['drift']:+.2f}%", color=drift_clr),
            ], style={'borderTop': '0.5px solid #f5f5f5'}))
        trade_table = make_table(
            ['Ticker', 'Action', 'Shares', 'Est. Value', 'Drift'],
            t_rows)

    # ── Cash summary strip ────────────────────────────────────────────────────
    def cash_chip(label, val, color='#111'):
        return html.Div([
            html.P(label, style={'fontSize': '10px', 'color': '#bbb', 'margin': '0 0 4px',
                                 'textTransform': 'uppercase', 'letterSpacing': '0.05em'}),
            html.P(val,   style={'fontSize': '14px', 'fontWeight': '500',
                                 'margin': '0', 'color': color}),
        ], style={'minWidth': '110px'})

    cash_row = html.Div([
        cash_chip("Buys",          f"${buys_val:,.2f}",   '#dc2626' if buys_val else '#111'),
        cash_chip("Sells",         f"${sells_val:,.2f}",  '#16a34a' if sells_val else '#111'),
        cash_chip("Net Cash",      f"${net_cash:+,.2f}",  '#16a34a' if net_cash >= 0 else '#dc2626'),
        cash_chip("Cash After",    f"${cash_after:,.2f}", '#16a34a' if cash_ok else '#dc2626'),
    ], style={'display': 'flex', 'gap': '32px', 'marginTop': '16px',
              'paddingTop': '16px', 'borderTop': '0.5px solid #f0f0f0'})

    return html.Div([alloc_warn, trade_table, cash_row])


@app.callback(
    Output('save-feedback', 'children'),
    Input('save-targets', 'n_clicks'),
    State('rebalance-table', 'data'),
    prevent_initial_call=True,
)
def save_targets(n, table_data):
    if not table_data:
        return no_update
    targets = {row['ticker']: row.get('target_pct') or 0 for row in table_data}
    save_rebalance_targets(targets)
    return "✓ Saved"


# ── Dividends ─────────────────────────────────────────────────────────────────

@app.callback(
    Output('dividend-section', 'children'),
    Input('portfolio-data', 'data'),
    Input('refresh-interval', 'n_intervals'),
)
def update_dividends(data, _):
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

    # Pull logged events from DB for expected vs received
    db_events = get_dividend_events()
    today_str = datetime.now().strftime('%Y-%m-%d')

    received = [(t, e, a, q, tot) for t, e, a, q, tot, _ in db_events if e < today_str]
    expected = [(t, e, a, q, tot) for t, e, a, q, tot, _ in db_events if e >= today_str]

    received_total = sum(tot for *_, tot in received)
    expected_total = sum(tot for *_, tot in expected)
    annual_income  = sum(p['annual_income'] for p in div_positions)

    if not div_positions and not db_events:
        return html.Div(
            html.P("No dividend data — positions may not pay dividends or market data is unavailable.",
                   style={'fontSize': '13px', 'color': '#bbb', 'textAlign': 'center', 'padding': '24px 0'}),
            style=CARD)

    # ── Summary cards ────────────────────────────────────────────────────────
    def div_card(label, value, sub=None, color='#111'):
        return html.Div([
            html.P(label, style={'fontSize': '11px', 'color': '#bbb', 'margin': '0 0 8px',
                                 'textTransform': 'uppercase', 'letterSpacing': '0.05em'}),
            html.P(value, style={'fontSize': '22px', 'fontWeight': '600', 'margin': '0',
                                 'color': color, 'letterSpacing': '-0.5px'}),
            html.P(sub, style={'fontSize': '11px', 'color': '#ccc', 'margin': '4px 0 0'}) if sub else None,
        ], style={'background': '#fafafa', 'borderRadius': '10px', 'padding': '16px',
                  'borderLeft': '3px solid #ebebeb'})

    portfolio_yield = round(annual_income / data['summary']['total_value'] * 100, 2) \
        if annual_income and data.get('summary', {}).get('total_value') else None

    summary_row = html.Div([
        div_card("Projected Annual Income", f"${annual_income:,.2f}",
                 sub=f"€{annual_income / rate:,.2f}"),
        div_card("Portfolio Yield",
                 f"{portfolio_yield:.2f}%" if portfolio_yield else "—",
                 sub="Based on next 12M dividends"),
        div_card("Received (logged)", f"${received_total:,.2f}",
                 sub=f"{len(received)} payment{'s' if len(received) != 1 else ''}",
                 color='#16a34a' if received_total > 0 else '#111'),
        div_card("Upcoming (logged)", f"${expected_total:,.2f}",
                 sub=f"{len(expected)} payment{'s' if len(expected) != 1 else ''}"),
    ], style={'display': 'grid', 'gridTemplateColumns': 'repeat(4, 1fr)',
              'gap': '10px', 'marginBottom': '20px'})

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

    # ── Upcoming payments (from DB log) ──────────────────────────────────────
    if expected:
        up_rows = []
        td_r = lambda v, **kw: html.Td(v, style={'textAlign': 'right', 'padding': '10px 12px', **kw})
        td_l = lambda v, **kw: html.Td(v, style={'textAlign': 'left',  'padding': '10px 12px', **kw})
        from datetime import date
        for ticker, ex_date, amount_ps, qty, total in sorted(expected, key=lambda x: x[1]):
            days_until = (date.fromisoformat(ex_date) - date.today()).days
            days_str   = f"in {days_until}d" if days_until > 0 else "today"
            up_rows.append(html.Tr([
                td_l(html.Span(ticker, style={'fontWeight': '600', 'color': '#111'})),
                td_r(ex_date, color='#666'),
                td_r(days_str, color='#378ADD' if days_until <= 7 else '#999'),
                td_r(f"${amount_ps:,.4f}"),
                td_r(f"{qty:,.0f}"),
                td_r(html.Span(f"${total:,.2f}",
                               style={'fontWeight': '500',
                                      'color': '#16a34a' if days_until <= 30 else '#111'})),
            ], style={'borderTop': '0.5px solid #f5f5f5'}))
        upcoming = html.Div([
            section_label("Upcoming Payments"),
            make_table(['Ticker', 'Ex-Date', 'When', 'Per Share', 'Shares', 'Total Payout'], up_rows),
        ])
    else:
        upcoming = None

    return html.Div([
        section_label("Dividends"),
        summary_row,
        yield_table,
        upcoming,
    ], style=CARD)


# ── Performance analytics ──────────────────────────────────────────────────────

@app.callback(
    Output('performance-section', 'children'),
    Input('refresh-interval', 'n_intervals'),
    Input('portfolio-data', 'data'),
    Input('benchmark-selector', 'value'),
)
def update_performance(_, data, bench_ticker):
    if not data:
        return None

    rows = get_all_snapshots()
    m = calculate_analytics(rows)

    if not m:
        return html.Div(
            html.P("Not enough history yet — performance analytics appear after 2+ days of data.",
                   style={'fontSize': '13px', 'color': '#bbb', 'textAlign': 'center', 'padding': '24px 0'}),
            style=CARD)

    n = m['num_days']

    # ── Helpers ───────────────────────────────────────────────────────────────
    def pct_card(label, value, note=None):
        if value is None:
            val_el = html.P("—", style={'fontSize': '22px', 'fontWeight': '600',
                                        'margin': '0', 'color': '#ccc'})
        else:
            color = '#16a34a' if value >= 0 else '#dc2626'
            val_el = html.P(f"{value:+.2f}%",
                            style={'fontSize': '22px', 'fontWeight': '600',
                                   'margin': '0', 'color': color, 'letterSpacing': '-0.5px'})
        return html.Div([
            html.P(label, style={'fontSize': '11px', 'color': '#bbb', 'margin': '0 0 8px',
                                 'textTransform': 'uppercase', 'letterSpacing': '0.05em'}),
            val_el,
            html.P(note, style={'fontSize': '11px', 'color': '#ccc', 'margin': '4px 0 0'}) if note else None,
        ], style={'background': '#fafafa', 'borderRadius': '10px', 'padding': '16px',
                  'borderLeft': f'3px solid {"#ebebeb"}'})

    def risk_card(label, value, fmt, note=None, color_fn=None):
        if value is None:
            val_str, color = '—', '#ccc'
        else:
            val_str = fmt(value)
            color   = color_fn(value) if color_fn else '#111'
        return html.Div([
            html.P(label, style={'fontSize': '11px', 'color': '#bbb', 'margin': '0 0 8px',
                                 'textTransform': 'uppercase', 'letterSpacing': '0.05em'}),
            html.P(val_str, style={'fontSize': '22px', 'fontWeight': '600',
                                   'margin': '0', 'color': color, 'letterSpacing': '-0.5px'}),
            html.P(note, style={'fontSize': '11px', 'color': '#ccc', 'margin': '4px 0 0'}) if note else None,
        ], style={'background': '#fafafa', 'borderRadius': '10px', 'padding': '16px',
                  'borderLeft': '3px solid #ebebeb'})

    # ── Returns row ───────────────────────────────────────────────────────────
    returns_row = html.Div([
        pct_card("7-Day Return",  m['return_7d'],  f"{min(n,7)} days" if n < 7 else None),
        pct_card("30-Day Return", m['return_30d'], f"{n} days only"   if n < 30 else None),
        pct_card("90-Day Return", m['return_90d'], f"{n} days only"   if n < 90 else None),
        pct_card("YTD Return",    m['return_ytd']),
    ], style={'display': 'grid', 'gridTemplateColumns': 'repeat(4, 1fr)',
              'gap': '10px', 'marginBottom': '10px'})

    # ── Risk row ──────────────────────────────────────────────────────────────
    sharpe_note = f"Based on {n} days" if n < 252 else None
    dd_note     = f"{m['dd_peak_date']} → {m['dd_trough_date']}"

    risk_row = html.Div([
        risk_card("Sharpe Ratio", m['sharpe'],
                  lambda v: f"{v:.2f}",
                  note=sharpe_note,
                  color_fn=lambda v: '#16a34a' if v >= 1 else ('#b45309' if v >= 0 else '#dc2626')),
        risk_card("Max Drawdown", m['max_drawdown'],
                  lambda v: f"{v:.2f}%",
                  note=dd_note,
                  color_fn=lambda v: '#dc2626'),
        risk_card("Ann. Volatility", m['volatility'],
                  lambda v: f"{v:.2f}%",
                  color_fn=lambda v: '#111'),
        risk_card("Win Rate", m['win_rate'],
                  lambda v: f"{v:.1f}%",
                  note=f"Best {m['best_day']:+.2f}%  ·  Worst {m['worst_day']:+.2f}%",
                  color_fn=lambda v: '#16a34a' if v >= 50 else '#dc2626'),
    ], style={'display': 'grid', 'gridTemplateColumns': 'repeat(4, 1fr)',
              'gap': '10px', 'marginBottom': '20px'})

    # ── Equity curve + benchmark ───────────────────────────────────────────────
    curve      = m['curve']
    port_dates = [r['date'] for r in curve]
    port_vals  = [r['total_value'] for r in curve]
    port_peaks = [r['rolling_peak'] for r in curve]

    # Normalize portfolio to 100
    base_val    = port_vals[0] if port_vals[0] else 1
    port_norm   = [v / base_val * 100 for v in port_vals]
    port_norm_peaks = [p / base_val * 100 for p in port_peaks]

    fig = go.Figure()

    # Drawdown fill behind portfolio line
    fig.add_trace(go.Scatter(
        x=port_dates, y=port_norm_peaks, mode='lines',
        line=dict(width=0), showlegend=False, hoverinfo='skip',
    ))
    fig.add_trace(go.Scatter(
        x=port_dates, y=port_norm, mode='lines',
        line=dict(color='#378ADD', width=2),
        fill='tonexty', fillcolor='rgba(220,38,38,0.07)',
        name='Portfolio',
        hovertemplate='%{x|%b %d}<br>Portfolio <b>%{y:.1f}</b><extra></extra>',
    ))

    # Benchmark overlay
    bench_df    = None
    alpha_value = None
    if bench_ticker:
        bench_df = get_benchmark_series(bench_ticker, port_dates[0], port_dates[-1])

    if bench_df is not None and not bench_df.empty:
        fig.add_trace(go.Scatter(
            x=bench_df['date'], y=bench_df['value'], mode='lines',
            line=dict(color='#d1d5db', width=1.5, dash='dot'),
            name=bench_ticker,
            hovertemplate='%{x|%b %d}<br>' + bench_ticker + ' <b>%{y:.1f}</b><extra></extra>',
        ))
        # Alpha = portfolio end - benchmark end (both indexed to 100)
        try:
            bench_end   = float(bench_df['value'].iloc[-1])
            port_end    = port_norm[-1]
            alpha_value = round(port_end - bench_end, 2)
        except Exception:
            pass

    fig.update_layout(
        margin=dict(t=8, b=8, l=0, r=0),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        showlegend=True,
        legend=dict(orientation='h', x=0, y=1.1,
                    font=dict(size=11, color='#888'),
                    bgcolor='rgba(0,0,0,0)'),
        hovermode='x unified',
        xaxis=dict(showgrid=False, zeroline=False,
                   tickfont=dict(size=11, color='#bbb')),
        yaxis=dict(showgrid=True, gridcolor='#f5f5f5', zeroline=False,
                   tickfont=dict(size=11, color='#bbb'),
                   ticksuffix='', tickformat='.0f'),
    )

    # Alpha badge
    if alpha_value is not None:
        a_color = '#16a34a' if alpha_value >= 0 else '#dc2626'
        a_bg    = '#f0fdf4' if alpha_value >= 0 else '#fef2f2'
        a_border = '#bbf7d0' if alpha_value >= 0 else '#fecaca'
        alpha_badge = html.Span(
            f"{'▲' if alpha_value >= 0 else '▼'} {alpha_value:+.1f}pts vs {bench_ticker}",
            style={'fontSize': '12px', 'color': a_color, 'background': a_bg,
                   'border': f'0.5px solid {a_border}',
                   'padding': '3px 10px', 'borderRadius': '20px'})
    else:
        alpha_badge = None

    chart = html.Div([
        html.Div([
            section_label("Equity Curve"),
            alpha_badge,
        ], style={'display': 'flex', 'alignItems': 'center', 'gap': '10px'}),
        dcc.Graph(figure=fig, config={'displayModeBar': False}, style={'height': '220px'}),
    ])

    return html.Div([
        html.Div([
            section_label("Performance"),
            html.Span(f"{n} trading days of history",
                      style={'fontSize': '11px', 'color': '#ccc',
                             'marginTop': '-12px', 'display': 'block', 'marginBottom': '16px'}),
        ]),
        returns_row,
        risk_row,
        chart,
    ], style=CARD)

