"""Position detail panel: click a holding row to expand a sliding card with
stats, a 52-week range bar, a price sparkline (with avg-cost line and
BUY/SELL trade markers), period toggle, and per-position CSV trade upload.

Uses pattern-matching IDs so the controls inside the panel can mount and
unmount without adding new top-level callbacks.
"""

from __future__ import annotations

import logging

import pandas as pd
import plotly.graph_objects as go
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

from decorators import safe_render
from market_intel import get_price_history
from schemas import PortfolioData
from styles import (
    CARD,
    COLOR_BAD,
    COLOR_BORDER_HEAVY,
    COLOR_BORDER_LIGHT,
    COLOR_BRAND,
    COLOR_GOOD,
    COLOR_SURFACE,
    COLOR_SURFACE_WHITE,
    COLOR_TEXT,
    COLOR_TEXT_MID,
    COLOR_TEXT_SLATE,
    COLOR_TEXT_STRONG,
    COLOR_WARN,
    LINK_PILL,
)
from trade_history import parse_activity_csv, save_uploaded_trades

log = logging.getLogger(__name__)

_LINK_STYLE = LINK_PILL
_PERIOD_CHOICES = ['1M', '3M', '1Y', '3Y', '5Y']
_PERIOD_TO_YF   = {'1M': '1mo', '3M': '3mo', '1Y': '1y', '3Y': '3y', '5Y': '5y'}


def _range_bar(low, high, current):
    """Visual 52-week range bar with current price marker."""
    pct = max(0.0, min(100.0, (current - low) / (high - low) * 100))
    return html.Div([
        html.Div([
            html.Div(style={
                'position': 'absolute', 'left': 0, 'top': 0,
                'width': '100%', 'height': '4px',
                'background': COLOR_BORDER_LIGHT, 'borderRadius': '2px',
            }),
            html.Div(style={
                'position': 'absolute', 'left': 0, 'top': 0,
                'width': f'{pct}%', 'height': '4px',
                'background': COLOR_BRAND, 'borderRadius': '2px',
            }),
            html.Div(style={
                'position': 'absolute', 'left': f'{pct}%', 'top': '-4px',
                'width': '14px', 'height': '14px', 'borderRadius': '50%',
                'background': COLOR_BRAND, 'border': '2px solid #fff',
                'boxShadow': '0 0 0 1.5px #378ADD',
                'transform': 'translateX(-50%)',
            }),
        ], style={'position': 'relative', 'height': '14px', 'margin': '8px 0'}),
        html.Div([
            html.Span(f"${low:,.2f}", style={'fontSize': '13px', 'color': COLOR_TEXT_MID, 'fontWeight': '500'}),
            html.Span(f"{pct:.0f}% of range",
                      style={'fontSize': '13px', 'color': COLOR_TEXT_MID, 'fontWeight': '500',
                             'position': 'absolute',
                             'left': '50%', 'transform': 'translateX(-50%)'}),
            html.Span(f"${high:,.2f}", style={'fontSize': '13px', 'color': COLOR_TEXT_MID, 'fontWeight': '500'}),
        ], style={'display': 'flex', 'justifyContent': 'space-between', 'position': 'relative'}),
    ])


def _stat(label, value, accent=None):
    return html.Div([
        html.P(label, style={
            'fontSize': '13px', 'color': COLOR_TEXT_MID, 'margin': '0 0 4px',
            'textTransform': 'uppercase', 'letterSpacing': '0.05em',
            'fontWeight': '600',
        }),
        html.P(value, style={
            'fontSize': '17px', 'fontWeight': '600', 'margin': '0',
            'color': accent or COLOR_TEXT_STRONG,
        }),
    ], style={'minWidth': '90px'})


def _period_btn(label, active):
    return html.Button(label, id={'type': 'period-btn', 'index': label},
                       n_clicks=0, style={
        'background': COLOR_BRAND if active else 'transparent',
        'color':      COLOR_SURFACE_WHITE    if active else COLOR_TEXT_MID,
        'border':     '1px solid ' + (COLOR_BRAND if active else COLOR_BORDER_HEAVY),
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
                      style={'color': COLOR_TEXT_MID, 'fontSize': '14px', 'fontWeight': '500',
                             'textAlign': 'center', 'margin': '24px 0 8px'})

    first, last = prices[0], prices[-1]
    up = last >= first
    line_color = COLOR_GOOD if up else COLOR_BAD
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
            fig.add_hline(y=avg_cost, line=dict(color=COLOR_TEXT_MID, width=1, dash='dot'),
                          annotation_text=f'Avg ${avg_cost:,.2f}',
                          annotation_position='top left',
                          annotation=dict(font=dict(size=12, color=COLOR_TEXT)))

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
                marker=dict(symbol='triangle-up', color=COLOR_GOOD,
                            size=24, line=dict(color=COLOR_SURFACE_WHITE, width=1)),
                hovertext=buys_hover, hoverinfo='text',
            ))
        if sells_x:
            fig.add_trace(go.Scatter(
                x=sells_x, y=sells_y, mode='markers', name='SELL',
                marker=dict(symbol='triangle-down', color=COLOR_BAD,
                            size=24, line=dict(color=COLOR_SURFACE_WHITE, width=1)),
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
                   tickfont=dict(size=12, color=COLOR_TEXT_MID),
                   range=[y_lo - pad, y_hi + pad]),
        plot_bgcolor=COLOR_SURFACE_WHITE, paper_bgcolor='rgba(0,0,0,0)',
        showlegend=False,
        hoverlabel=dict(bgcolor=COLOR_SURFACE_WHITE, bordercolor=COLOR_BRAND,
                        font=dict(size=14, color=COLOR_TEXT_STRONG, family='Inter, system-ui, sans-serif')),
    )
    summary_line = html.Div([
        html.Span(f"{period} ", style={'color': COLOR_TEXT_MID, 'fontSize': '13px',
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


def register(app):
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
        # Escape key or ✕ close button: always close the detail panel and
        # clear the DataTable's active-cell highlight so the row doesn't stay
        # tinted.
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
    @safe_render('Position Detail')
    def show_position_detail(ticker: str | None, period: str, uploaded, data: PortfolioData | None):
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
            chg_color = COLOR_GOOD if daily_chg >= 0 else COLOR_BAD
            chg_str   = f"{'▲' if daily_chg >= 0 else '▼'} ${abs(daily_chg):,.2f}  ({daily_chg_pct:+.2f}%)"
        else:
            chg_color, chg_str = COLOR_TEXT_MID, '—'

        qty       = r.get('quantity') or 0
        mkt_val   = r.get('market_value')
        unreal    = r.get('unrealized_pnl')
        avg_cost  = r.get('avg_cost')

        if avg_cost and avg_cost == avg_cost and avg_cost > 0:
            cost_diff_pct = (price - avg_cost) / avg_cost * 100
            cost_color = COLOR_GOOD if cost_diff_pct >= 0 else COLOR_BAD
            cost_str   = f"${avg_cost:,.2f}  ({cost_diff_pct:+.2f}%)"
        else:
            cost_color, cost_str = COLOR_TEXT_MID, '—'

        qty_str    = f"{qty:,.0f}" if qty else '—'
        mkt_str    = f"${mkt_val:,.2f}" if mkt_val is not None else '—'
        if unreal is not None:
            unreal_color = COLOR_GOOD if unreal >= 0 else COLOR_BAD
            unreal_str   = f"${unreal:+,.2f}"
        else:
            unreal_color, unreal_str = COLOR_TEXT_MID, '—'

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
                    'fontSize': '13px', 'color': COLOR_TEXT_MID, 'margin': '0 0 0',
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
                'background': 'transparent', 'color': COLOR_BRAND,
                'border': '1px solid #cfe0f5', 'borderRadius': '6px',
                'padding': '4px 10px', 'fontSize': '12px',
                'cursor': 'pointer', 'fontWeight': '500',
            }),
        )
        upload_help = html.Details([
            html.Summary("How to export from IBKR ▸", style={
                'cursor': 'pointer', 'color': COLOR_BRAND, 'fontSize': '13px',
                'fontWeight': '600', 'marginTop': '6px',
            }),
            html.Ol([
                html.Li("Log in to the IBKR Client Portal."),
                html.Li("Go to Performance & Reports → Transaction History."),
                html.Li("Pick a date range and click the CSV / download icon."),
                html.Li("Drop the .csv file on the upload button above."),
            ], style={'fontSize': '13px', 'color': COLOR_TEXT, 'lineHeight': '1.7',
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
                    'fontSize': '14px', 'color': COLOR_TEXT_MID, 'margin': '0',
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
                html.Span(trade_count_note, style={'fontSize': '13px', 'color': COLOR_TEXT_MID, 'fontWeight': '500'}),
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
                    html.Span(ticker, style={'fontWeight': '700', 'fontSize': '17px', 'color': COLOR_TEXT_STRONG}),
                    html.Span(f"${price:,.2f}",
                              style={'fontSize': '17px', 'color': COLOR_TEXT_STRONG, 'marginLeft': '12px'}),
                    html.Span(chg_str,
                              style={'fontSize': '15px', 'color': chg_color, 'marginLeft': '12px'}),
                ], style={'display': 'flex', 'alignItems': 'center'}),
                html.Div([
                    html.Button(f"✨ Ask coach about {ticker}",
                                id={'type': 'position-ask-coach', 'index': 0},
                                n_clicks=0, style={
                        'fontSize': '13px', 'color': COLOR_TEXT_SLATE,
                        'background': COLOR_SURFACE, 'border': '1px solid #e5e7eb',
                        'borderRadius': '8px', 'padding': '5px 12px',
                        'cursor': 'pointer', 'fontWeight': '500',
                        'fontFamily': 'inherit', 'marginRight': '4px',
                    }),
                    html.A("Yahoo", href=f"https://finance.yahoo.com/quote/{ticker}",
                           target='_blank', style=_LINK_STYLE),
                    html.A("TradingView", href=f"https://www.tradingview.com/symbols/{ticker}/",
                           target='_blank', style=_LINK_STYLE),
                    html.Button("✕ Close", id={'type': 'position-close', 'index': 0}, n_clicks=0, style={
                        'fontSize': '13px', 'color': COLOR_SURFACE_WHITE,
                        'background': COLOR_BAD, 'border': '1px solid #dc2626',
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
            'background': COLOR_SURFACE_WHITE,
            'borderLeft': '3px solid #378ADD',
            'animation': 'slideInDown 0.25s ease-out',
        })

    # ── Per-position CSV trade upload ─────────────────────────────────────────
    # The upload button lives inside the opened position detail card. The user
    # exports a Transaction History CSV from the IBKR Client Portal and drops
    # it here. Parsed trades are persisted to data/uploaded_trades.json (the
    # same store used by the global trade history timeline) and plotted as
    # BUY/SELL arrows on the per-position price chart.
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
            return no_update, [_status("Need a .csv file", COLOR_BAD)
                               for _ in (contents_list or [])]
        try:
            _, b64 = contents.split(',', 1)
            decoded = base64.b64decode(b64)
        except Exception:
            return no_update, [_status("Could not decode file", COLOR_BAD)
                               for _ in (contents_list or [])]

        parsed = parse_activity_csv(decoded)
        if not parsed:
            return no_update, [_status("No trades found in CSV", COLOR_WARN)
                               for _ in (contents_list or [])]

        merged = save_uploaded_trades(parsed)
        msg = f"{len(parsed)} parsed · {len(merged)} total stored"
        return merged, [_status(msg, COLOR_GOOD) for _ in (contents_list or [])]
