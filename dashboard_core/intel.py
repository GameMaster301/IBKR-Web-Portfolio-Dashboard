"""Toast notifications + Market Intelligence callbacks.

The toast is grouped here because it lives in the same band of the layout
and shares the `ctx`/`datetime` plumbing.

Market Intelligence:
    - populate_market_intel: fan-out yfinance fetch (sector_geo + earnings)
      into the market-intel-data store, with a ticker-set guard that returns
      no_update when the holdings haven't changed.
    - render_sector_geo:     sector donut + country chart + per-sector ticker
      legend table.
    - render_earnings:       upcoming earnings calendar table.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime

import plotly.graph_objects as go
from dash import Input, Output, State, ctx, dcc, html, no_update

from dashboard_core.helpers import EURUSD_FALLBACK, make_table, section_label, to_eur
from decorators import NotReadyError, safe_render
from market_intel import get_earnings_data, get_sector_geo
from net_util import run_parallel
from schemas import MarketIntelData, PortfolioData
from styles import CARD

log = logging.getLogger(__name__)

# Module-level cache key; only updated AFTER a successful fetch so that a
# failed fetch is retried on the next 60-second refresh.
_last_intel_tickers: tuple | None = None



def register(app):
    # ── Toast notifications ───────────────────────────────────────────────────
    # One callback covers all toast triggers.  Using a single Output avoids the
    # "multiple callbacks with the same output" constraint in Dash.
    #
    # The child div receives a unique `key` (current timestamp) on every call
    # so React unmounts and remounts it — which restarts the CSS animation
    # even when the message text is identical to the previous one.
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

    # ── Populate market-intel store ───────────────────────────────────────────
    # Fires whenever portfolio-data updates. Calls yfinance-backed functions in
    # parallel (sector/geo + earnings concurrently) so cold-start latency is
    # bounded by the slower of the two rather than their sum.
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

        _last_intel_tickers = ticker_key
        return result

    # ── 1. Sector & Geography ────────────────────────────────────────────────
    @app.callback(
        Output('sector-geo-section', 'children'),
        Input('market-intel-data', 'data'),
        State('portfolio-data', 'data'),
    )
    @safe_render('Sector & Geography')
    def render_sector_geo(intel: MarketIntelData | None, port_data: PortfolioData | None):
        return _render_sector_geo_inner(intel, port_data)

    # ── 3. Earnings calendar ─────────────────────────────────────────────────
    @app.callback(
        Output('earnings-section', 'children'),
        Input('market-intel-data', 'data'),
        State('portfolio-data', 'data'),
    )
    @safe_render('Earnings')
    def render_earnings(intel: MarketIntelData | None, port_data: PortfolioData | None):
        return _render_earnings_inner(intel, port_data)


def _render_sector_geo_inner(intel: MarketIntelData | None, port_data: PortfolioData | None):
    if not intel:
        raise NotReadyError('Loading sector & geography data…')
    if not port_data or 'positions' not in port_data:
        return None

    sg = intel.get('sector_geo', {})
    positions = port_data['positions']

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

    # De-duplicate ticker lists
    for sec, tks in sector_tickers.items():
        seen: list = []
        for t in tks:
            if t not in seen:
                seen.append(t)
        sector_tickers[sec] = seen

    total = sum(sector_val.values()) or 1

    # ── Sector donut ────────────────────────────────────────────────────────
    MIN_SECTOR_PCT = 5.0

    sec_full_sorted = sorted(sector_val.items(), key=lambda x: x[1], reverse=True)
    sec_labels = [s[0] for s in sec_full_sorted]
    sec_values = [s[1] for s in sec_full_sorted]

    colors = ['#378ADD', '#f97316', '#a855f7', '#22c55e',
              '#eab308', '#ec4899', '#14b8a6', '#6366f1',
              '#84cc16', '#ef4444', '#06b6d4']
    sec_colors = [colors[i % len(colors)] for i in range(len(sec_labels))]
    color_by_sec = dict(zip(sec_labels, sec_colors, strict=True))

    rate = (port_data.get('account') or {}).get('eurusd_rate') or EURUSD_FALLBACK
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
            html.Div([
                html.P("Sector", style={'fontSize': '14px', 'color': '#555',
                                        'textTransform': 'uppercase',
                                        'letterSpacing': '0.04em', 'margin': '0 0 8px'}),
                dcc.Graph(figure=donut, config={'displayModeBar': False}),
            ], style={'flex': '2', 'minWidth': '200px'}),

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


def _render_earnings_inner(intel: MarketIntelData | None, port_data: PortfolioData | None):
    if not intel:
        raise NotReadyError('Loading earnings data…')

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
