"""Top-of-page rendering: summary cards, holdings table, allocation donut,
and the dividends panel.

All four callbacks read from `portfolio-data` and render plain children. No
network, no IBKR — pure functions of the store payload.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Input, Output, State, dash_table, html

from dashboard_core.helpers import EURUSD_FALLBACK, make_table, section_label, to_eur
from decorators import NotReadyError, safe_render
from schemas import PortfolioData
from styles import (
    CARD,
    COLOR_BAD,
    COLOR_BORDER_STRONG,
    COLOR_BRAND,
    COLOR_GOOD,
    COLOR_GOOD_SOFT,
    COLOR_SURFACE_SOFT,
    COLOR_SURFACE_WHITE,
    COLOR_TEXT_DIM,
    COLOR_TEXT_FAINT,
    COLOR_TEXT_GHOST,
    COLOR_TEXT_MID,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_STRONG,
    COLOR_WARN,
    COLOR_WARN_BG,
    COLOR_WARN_SOFT,
    COLOR_WARN_YELLOW,
)


def register(app):
    # ── Summary cards ─────────────────────────────────────────────────────────
    @app.callback(
        Output('summary-cards', 'children'),
        Input('portfolio-data', 'data'),
        State('connection-status', 'data'),
    )
    @safe_render('Summary')
    def update_summary(data: PortfolioData | None, status):
        if not data or 'summary' not in data:
            if status == 'disconnected':
                return []
            def _skel_card():
                return html.Div([
                    html.Div(className='skeleton-block', style={'height': '11px', 'width': '55%', 'marginBottom': '14px'}),
                    html.Div(className='skeleton-block', style={'height': '28px', 'width': '78%', 'marginBottom': '10px'}),
                    html.Div(className='skeleton-block', style={'height': '11px', 'width': '40%'}),
                ], style={'background': COLOR_SURFACE_SOFT, 'borderRadius': '12px',
                          'padding': '18px', 'borderLeft': f'3px solid {COLOR_BORDER_STRONG}'})
            return [_skel_card() for _ in range(4)]

        s = data['summary']
        a = data.get('account', {})
        rate = a.get('eurusd_rate', EURUSD_FALLBACK)

        total_val  = s['total_value']
        unreal_pnl = s['total_unrealized_pnl']
        daily_pnl  = a.get('daily_pnl') or s.get('total_daily_pnl')
        cash_eur   = a.get('cash_eur', 0)
        pnl_pct    = s.get('total_pnl_pct')

        def card(label, eur_val, pnl_pct=None, is_pnl=False, note=None):
            positive = eur_val >= 0
            accent = (COLOR_GOOD if positive else COLOR_BAD) if is_pnl else COLOR_TEXT_STRONG
            val_str = f"€{eur_val:+,.2f}" if is_pnl else f"€{eur_val:,.2f}"
            usd_str = f"${eur_val * rate:,.2f}"
            return html.Div([
                html.P(label, style={
                    'fontSize': '14px', 'color': COLOR_TEXT_FAINT, 'margin': '0 0 10px',
                    'textTransform': 'uppercase', 'letterSpacing': '0.05em', 'fontWeight': '500',
                }),
                html.P(val_str, style={
                    'fontSize': '26px', 'fontWeight': '600', 'margin': '0',
                    'color': accent if is_pnl else COLOR_TEXT_STRONG, 'letterSpacing': '-0.5px',
                }),
                html.Div([
                    html.Span(usd_str, style={'fontSize': '14px', 'color': COLOR_TEXT_FAINT}),
                    html.Span(f" · {pnl_pct:+.2f}%",
                              style={'fontSize': '14px', 'color': accent}) if pnl_pct is not None else None,
                    html.Span(f" · {note}",
                              style={'fontSize': '14px', 'color': COLOR_TEXT_MUTED}) if note is not None else None,
                ], style={'marginTop': '4px'}),
            ], style={
                'background': COLOR_SURFACE_SOFT, 'borderRadius': '12px', 'padding': '18px',
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

    # ── Holdings ──────────────────────────────────────────────────────────────
    @app.callback(
        Output('holdings-table', 'children'),
        Output('positions-count', 'children'),
        Output('stale-price-badge', 'children'),
        Input('portfolio-data', 'data'),
        State('connection-status', 'data'),
    )
    def update_holdings(data, status):
        if not data or 'positions' not in data:
            if status == 'disconnected':
                return None, '', None
            skel_row = html.Div([
                html.Div(className='skeleton-block', style={'height': '13px', 'width': w, 'borderRadius': '4px'})
                for w in ('12%', '18%', '15%', '14%', '14%', '13%')
            ], style={'display': 'flex', 'gap': '12px', 'alignItems': 'center',
                      'padding': '10px 0', 'borderBottom': f'0.5px solid {COLOR_BORDER_STRONG}'})
            return html.Div([skel_row for _ in range(5)],
                            style={'padding': '4px 0'}), '', None
        df = pd.DataFrame(data['positions'])
        rate = data.get('account', {}).get('eurusd_rate', EURUSD_FALLBACK)

        count = f"{len(df)} positions"
        any_stale = df.get('price_stale', pd.Series(False)).any()
        stale_badge = html.Span("● Market closed · last-close prices",
                                style={
                                    'fontSize': '13px', 'color': COLOR_WARN,
                                    'background': COLOR_WARN_BG, 'border': '0.5px solid #fde68a',
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
                'fontSize': '14px', 'color': COLOR_TEXT_FAINT, 'fontWeight': '500',
                'textTransform': 'uppercase', 'letterSpacing': '0.04em',
                'backgroundColor': COLOR_SURFACE_WHITE, 'border': 'none',
                'borderBottom': '0.5px solid #f5f5f5',
                'paddingBottom': '14px',
            },
            style_cell={
                'fontFamily': '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
                'fontSize': '16px', 'padding': '12px 12px',
                'backgroundColor': COLOR_SURFACE_WHITE, 'color': COLOR_TEXT_STRONG,
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
                 'color': COLOR_GOOD},
                {'if': {'filter_query': '{pnl_pct} < 0', 'column_id': 'pnl_pct_display'},
                 'color': COLOR_BAD},
                {'if': {'filter_query': '{unrealized_pnl} >= 0', 'column_id': 'unrealized_pnl'},
                 'color': COLOR_GOOD},
                {'if': {'filter_query': '{unrealized_pnl} < 0', 'column_id': 'unrealized_pnl'},
                 'color': COLOR_BAD},
    {'if': {'filter_query': '{price_display} contains "~"', 'column_id': 'price_display'},
                 'color': COLOR_WARN},
                {'if': {'state': 'active'}, 'backgroundColor': '#f0f7ff', 'border': 'none'},
            ],
        )

        return table, count, stale_badge

    # ── Donut ─────────────────────────────────────────────────────────────────
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
        colors = [COLOR_BRAND, COLOR_WARN_SOFT, '#a855f7', COLOR_GOOD_SOFT, COLOR_WARN_YELLOW, '#ec4899', '#14b8a6']
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
                        font=dict(size=12, color=COLOR_TEXT_MID), itemclick=False, itemdoubleclick=False),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
        )
        return fig

    # ── Dividends ─────────────────────────────────────────────────────────────
    @app.callback(
        Output('dividend-section', 'children'),
        Input('portfolio-data', 'data'),
        Input('refresh-interval', 'n_intervals'),
    )
    @safe_render('Dividends')
    def update_dividends(data: PortfolioData | None, *_):
        if not data or 'positions' not in data:
            raise NotReadyError('Loading dividend data…')

        positions = data['positions']
        div_data  = data.get('div_data', {})
        rate      = data.get('account', {}).get('eurusd_rate', EURUSD_FALLBACK)

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
                       style={'fontSize': '15px', 'color': COLOR_TEXT_GHOST, 'textAlign': 'center', 'padding': '24px 0'}),
                style=CARD)

        # ── Summary cards ────────────────────────────────────────────────────
        def div_card(label, value, sub=None, color=COLOR_TEXT_STRONG):
            return html.Div([
                html.P(label, style={'fontSize': '12px', 'color': COLOR_TEXT_FAINT, 'margin': '0 0 6px',
                                     'textTransform': 'uppercase', 'letterSpacing': '0.05em',
                                     'fontWeight': '500'}),
                html.P(value, style={'fontSize': '20px', 'fontWeight': '600', 'margin': '0',
                                     'color': color, 'letterSpacing': '-0.5px'}),
                html.P(sub, style={'fontSize': '13px', 'color': COLOR_TEXT_MUTED, 'margin': '3px 0 0'}) if sub else None,
            ], style={'background': COLOR_SURFACE_SOFT, 'borderRadius': '12px', 'padding': '10px 14px',
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

        # ── Per-position yield table ─────────────────────────────────────────
        if div_positions:
            td_r = lambda v, **kw: html.Td(v, style={'textAlign': 'right', 'padding': '10px 12px', **kw})
            td_l = lambda v, **kw: html.Td(v, style={'textAlign': 'left',  'padding': '10px 12px', **kw})

            rows = []
            for p in sorted(div_positions, key=lambda x: x['annual_income'], reverse=True):
                nxt_date = p['next_date'] or '—'
                nxt_amt  = f"${p['next_amount']:,.4f}" if p['next_amount'] else '—'
                nxt_pay  = f"${p['next_amount'] * p['quantity']:,.2f}" \
                           if p['next_amount'] else '—'
                yield_color = COLOR_GOOD if (p['yield_pct'] or 0) >= 2 else COLOR_TEXT_STRONG
                rows.append(html.Tr([
                    td_l(html.Span(p['ticker'], style={'fontWeight': '600', 'color': COLOR_TEXT_STRONG})),
                    td_r(f"{p['yield_pct']:.2f}%" if p['yield_pct'] else '—', color=yield_color),
                    td_r(f"${p['annual_dps']:,.4f}"),
                    td_r(f"${p['annual_income']:,.2f}"),
                    td_r(nxt_date, color=COLOR_TEXT_DIM),
                    td_r(nxt_amt,  color=COLOR_TEXT_DIM),
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
