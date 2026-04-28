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
from market_intel import get_price_history, get_stock_fundamentals, score_fundamentals
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


def _build_brief_section(f: dict, scored: dict, pos: dict) -> html.Div:
    """Investment brief view: signals, narrative, risk/reward, condensed metrics."""
    sig = scored.get('signals', {})

    _CHIP_BASE = {
        'display': 'inline-block', 'padding': '3px 10px',
        'borderRadius': '12px', 'fontSize': '11px', 'fontWeight': '600',
        'marginRight': '6px', 'marginBottom': '6px', 'letterSpacing': '0.04em',
    }
    _CHIP_LABELS = [
        ('valuation',     'Valuation'),
        ('growth',        'Growth'),
        ('profitability', 'Profitability'),
        ('fin_health',    'Financial Health'),
        ('sentiment',     'Sentiment'),
        ('momentum',      'Momentum'),
    ]
    chips = []
    for key, label in _CHIP_LABELS:
        s = sig.get(key, {})
        c = s.get('color', '#9ca3af')
        chips.append(html.Span(
            f"{label}: {s.get('label', '—')}",
            style={**_CHIP_BASE, 'background': c + '18', 'color': c,
                   'border': f'1px solid {c}40'},
        ))

    overall_block = html.Div([
        html.Div(scored.get('overall', ''), style={
            'fontSize': '14px', 'fontWeight': '600', 'color': COLOR_TEXT_STRONG,
            'marginBottom': '3px',
        }),
        html.Div('→ ' + scored.get('overall_sub', ''), style={
            'fontSize': '12px', 'color': COLOR_TEXT_MID, 'fontStyle': 'italic',
        }),
    ], style={'marginBottom': '10px'})

    chips_block = html.Div(chips, style={'marginBottom': '10px'})

    sentences = scored.get('explanation', [])
    explanation_block = html.Div([
        html.P(s, style={'fontSize': '13px', 'color': COLOR_TEXT,
                         'margin': '0 0 4px', 'lineHeight': '1.5'})
        for s in sentences
    ], style={'marginBottom': '10px'}) if sentences else None

    rr_block = html.Div([
        html.Div([
            html.Div("↑ Upside", style={
                'fontSize': '11px', 'fontWeight': '700', 'color': COLOR_GOOD,
                'textTransform': 'uppercase', 'letterSpacing': '0.06em', 'marginBottom': '4px',
            }),
            html.Div(scored.get('upside', ''), style={
                'fontSize': '12px', 'color': COLOR_TEXT, 'lineHeight': '1.4',
            }),
        ], style={'flex': '1', 'paddingRight': '10px'}),
        html.Div(style={'width': '1px', 'background': COLOR_BORDER_LIGHT, 'alignSelf': 'stretch'}),
        html.Div([
            html.Div("↓ Downside", style={
                'fontSize': '11px', 'fontWeight': '700', 'color': COLOR_BAD,
                'textTransform': 'uppercase', 'letterSpacing': '0.06em', 'marginBottom': '4px',
            }),
            html.Div(scored.get('downside', ''), style={
                'fontSize': '12px', 'color': COLOR_TEXT, 'lineHeight': '1.4',
            }),
        ], style={'flex': '1', 'paddingLeft': '10px'}),
    ], style={
        'display': 'flex', 'marginBottom': '10px',
        'padding': '8px 0',
        'borderTop': f'1px solid {COLOR_BORDER_LIGHT}',
        'borderBottom': f'1px solid {COLOR_BORDER_LIGHT}',
    })

    watch = scored.get('watch', [])
    watch_block = html.Div([
        html.Div("What to watch", style={
            'fontSize': '11px', 'fontWeight': '700', 'color': COLOR_TEXT_MID,
            'textTransform': 'uppercase', 'letterSpacing': '0.06em', 'marginBottom': '4px',
        }),
        html.Ul([
            html.Li(w, style={'fontSize': '12px', 'color': COLOR_TEXT, 'marginBottom': '2px'})
            for w in watch
        ], style={'margin': '0', 'paddingLeft': '16px'}),
    ], style={'marginBottom': '10px'}) if watch else None

    # Condensed metric rows with inline interpretation
    def _cm(label, val_str, interp, color):
        parts = [
            html.Span(label + ": ", style={'fontSize': '11px', 'color': COLOR_TEXT_MID, 'fontWeight': '600'}),
            html.Span(val_str or '—', style={'fontSize': '12px', 'fontWeight': '700',
                                              'color': color or COLOR_TEXT_STRONG}),
        ]
        if interp:
            parts.append(html.Span(f' · {interp}', style={'fontSize': '11px', 'color': COLOR_TEXT_MID}))
        return html.Div(parts, style={'marginBottom': '3px'})

    def _grade_pe(v):
        if v is None: return '—', None, '#9ca3af'
        if v > 40:  return f"{v:.1f}×", "very expensive", '#dc2626'
        if v > 25:  return f"{v:.1f}×", "expensive",      '#ea580c'
        if v > 15:  return f"{v:.1f}×", "fair",            '#d97706'
        return          f"{v:.1f}×", "cheap",           '#16a34a'

    def _grade_margin(v):
        if v is None: return '—', None, '#9ca3af'
        if v > 20:  return f"{v:.1f}%", "high margin",    '#16a34a'
        if v > 10:  return f"{v:.1f}%", "decent margin",  '#d97706'
        if v >= 0:  return f"{v:.1f}%", "thin margin",    '#ea580c'
        return          f"{v:.1f}%", "losing money",   '#dc2626'

    def _grade_fcf(v):
        if v is None: return '—', None, '#9ca3af'
        if v > 5:   return f"{v:.1f}%", "cash machine",   '#16a34a'
        if v > 0:   return f"{v:.1f}%", "positive",       '#d97706'
        return          f"{v:.1f}%", "burning cash",   '#dc2626'

    def _grade_debt(v):
        if v is None: return '—', None, '#9ca3af'
        if v < 1:   return f"{v:.1f}×", "very low",       '#16a34a'
        if v < 3:   return f"{v:.1f}×", "manageable",     '#d97706'
        if v < 5:   return f"{v:.1f}×", "elevated",       '#ea580c'
        return          f"{v:.1f}×", "high risk",      '#dc2626'

    pe_s, pe_i, pe_c   = _grade_pe(f.get('fwd_pe'))
    mg_s, mg_i, mg_c   = _grade_margin(f.get('profit_margin'))
    fcf_s, fcf_i, fcf_c = _grade_fcf(f.get('fcf_yield'))
    dbt_s, dbt_i, dbt_c = _grade_debt(f.get('debt_ebitda'))

    peg = f.get('peg')
    rev = f.get('rev_growth_pct')
    roe = f.get('roe')
    ncp = f.get('net_cash_pct')
    grw_c = sig.get('growth', {}).get('color', '#9ca3af')

    metrics_block = html.Div([
        html.Div("Core Metrics", style={
            'fontSize': '11px', 'fontWeight': '700', 'color': COLOR_TEXT_MID,
            'textTransform': 'uppercase', 'letterSpacing': '0.06em', 'marginBottom': '6px',
        }),
        html.Div([
            html.Div([
                _cm("P/E",         pe_s,  pe_i,  pe_c),
                _cm("PEG",         f"{peg:.2f}" if peg else None, "growth-adjusted", '#9ca3af'),
                _cm("Rev Growth",  f"{rev:+.1f}%/yr" if rev is not None else None,
                    ("very fast" if rev and rev > 20 else
                     "solid"    if rev and rev > 10 else
                     "slow"     if rev is not None and rev >= 0 else
                     "declining") if rev is not None else None,
                    grw_c),
            ], style={'flex': '1', 'minWidth': '130px'}),
            html.Div([
                _cm("Net Margin",  mg_s,  mg_i,  mg_c),
                _cm("ROE",
                    f"{roe:.1f}%" if roe is not None else None,
                    ("strong"   if roe is not None and roe > 20 else
                     "moderate" if roe is not None and roe > 10 else
                     "weak")    if roe is not None else None,
                    '#16a34a' if roe is not None and roe > 20 else
                    '#d97706' if roe is not None and roe > 10 else
                    '#ea580c' if roe is not None else '#9ca3af'),
                _cm("FCF Yield",   fcf_s, fcf_i, fcf_c),
            ], style={'flex': '1', 'minWidth': '130px'}),
            html.Div([
                _cm("Debt/EBITDA", dbt_s, dbt_i, dbt_c),
                _cm("Net Cash",
                    f"+{ncp:.1f}% of mkt cap" if ncp is not None and ncp > 0 else
                    f"{ncp:.1f}% (net debt)"  if ncp is not None else None,
                    None,
                    '#16a34a' if ncp is not None and ncp > 0 else
                    '#dc2626' if ncp is not None and ncp < -10 else
                    '#d97706' if ncp is not None else '#9ca3af'),
            ], style={'flex': '1', 'minWidth': '130px'}),
        ], style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap'}),
    ], style={'borderTop': f'1px solid {COLOR_BORDER_LIGHT}',
              'paddingTop': '8px', 'marginBottom': '10px'})

    gain_pct = pos.get('cost_diff_pct')
    alloc    = pos.get('allocation_pct')
    pos_parts = []
    if gain_pct is not None:
        gain_color = COLOR_GOOD if gain_pct >= 0 else COLOR_BAD
        pos_parts += [
            html.Span("Your gain: ", style={'color': COLOR_TEXT_MID}),
            html.Span(f"{gain_pct:+.1f}%", style={'color': gain_color, 'fontWeight': '700'}),
        ]
    if alloc:
        pos_parts += [
            html.Span("  ·  Portfolio weight: ", style={'color': COLOR_TEXT_MID}),
            html.Span(f"{alloc:.1f}%", style={'fontWeight': '600', 'color': COLOR_TEXT_STRONG}),
        ]
        if alloc > 10:
            pos_parts.append(html.Span(
                " — large position, increases portfolio risk",
                style={'color': COLOR_WARN, 'fontSize': '11px'},
            ))
        elif alloc > 5:
            pos_parts.append(html.Span(
                " — meaningful position",
                style={'color': COLOR_TEXT_MID, 'fontSize': '11px'},
            ))
    pos_block = html.Div(pos_parts, style={
        'fontSize': '12px', 'padding': '6px 0',
        'borderTop': f'1px solid {COLOR_BORDER_LIGHT}', 'marginBottom': '8px',
    }) if pos_parts else None

    takeaway = scored.get('takeaway', '')
    takeaway_block = html.Div([
        html.Span("💡 ", style={'fontSize': '14px'}),
        html.Span(takeaway, style={'fontSize': '12px', 'fontStyle': 'italic',
                                   'color': COLOR_TEXT, 'lineHeight': '1.5'}),
    ], style={
        'borderLeft': f'3px solid {COLOR_BRAND}', 'paddingLeft': '8px', 'marginTop': '4px',
    }) if takeaway else None

    return html.Div([c for c in [
        overall_block, chips_block, explanation_block,
        rr_block, watch_block, metrics_block, pos_block, takeaway_block,
    ] if c is not None])


def _build_fundamentals_section(f: dict, pos: dict | None = None,
                                view_mode: str = 'grid') -> html.Div | None:
    """
    Render the Valuation / Growth / Sentiment 3-column panel + Technicals row,
    with a toggle button to switch to the Investment Brief view.
    view_mode='grid' or 'brief' controls which view is shown on render.
    Returns None when f is empty (e.g. ETF with no data).
    """
    if not f:
        return None

    # ── helpers ───────────────────────────────────────────────────────────────
    _COL_HDR = {
        'fontSize': '11px', 'fontWeight': '700', 'color': COLOR_TEXT_MID,
        'textTransform': 'uppercase', 'letterSpacing': '0.08em',
        'borderBottom': f'1px solid {COLOR_BORDER_LIGHT}',
        'paddingBottom': '5px', 'marginBottom': '10px',
    }

    def _metric(label, value, subtitle, color=None):
        return html.Div([
            html.Div(label, style={
                'fontSize': '11px', 'color': COLOR_TEXT_MID,
                'textTransform': 'uppercase', 'letterSpacing': '0.06em',
                'fontWeight': '600', 'marginBottom': '1px',
            }),
            html.Div(value or '—', style={
                'fontSize': '16px', 'fontWeight': '700',
                'color': color or COLOR_TEXT_STRONG,
                'lineHeight': '1.2',
            }),
            html.Div(subtitle, style={
                'fontSize': '11px', 'color': COLOR_TEXT_MID, 'marginTop': '1px',
            }),
        ], style={'marginBottom': '10px'})

    _COL_STYLE = {'flex': '1', 'minWidth': '120px'}
    _DIVIDER   = {'width': '1px', 'background': COLOR_BORDER_LIGHT,
                  'margin': '0 14px', 'alignSelf': 'stretch'}

    # ── Valuation column ──────────────────────────────────────────────────────
    pe   = f.get('fwd_pe')
    peg  = f.get('peg')
    ev   = f.get('ev_ebitda')

    pe_color  = (COLOR_GOOD if pe  and pe  < 20 else
                 COLOR_WARN if pe  and pe  < 35 else
                 COLOR_BAD  if pe  else None)
    peg_color = (COLOR_GOOD if peg and peg < 1  else
                 COLOR_WARN if peg and peg < 2  else
                 COLOR_BAD  if peg else None)
    ev_color  = (COLOR_GOOD if ev  and ev  < 15 else
                 COLOR_WARN if ev  and ev  < 25 else
                 COLOR_BAD  if ev  else None)

    val_col = html.Div([
        html.Div("Valuation", style=_COL_HDR),
        _metric("Fwd P/E",    f"{pe:.1f}×"   if pe   else None, "price vs expected earnings", pe_color),
        _metric("PEG ratio",  f"{peg:.2f}"   if peg  else None, "value adjusted for growth",  peg_color),
        _metric("EV/EBITDA",  f"{ev:.1f}×"   if ev   else None, "company vs operating profit", ev_color),
    ], style=_COL_STYLE)

    # ── Growth column ─────────────────────────────────────────────────────────
    rev    = f.get('rev_growth_pct')
    margin = f.get('profit_margin')
    beat   = f.get('eps_beat')

    rev_color    = (COLOR_GOOD if rev    is not None and rev    > 10 else
                    COLOR_WARN if rev    is not None and rev    >= 0 else
                    COLOR_BAD  if rev    is not None else None)
    margin_color = (COLOR_GOOD if margin is not None and margin > 20 else
                    COLOR_WARN if margin is not None and margin >= 5  else
                    COLOR_BAD  if margin is not None else None)

    growth_col = html.Div([
        html.Div("Growth", style=_COL_HDR),
        _metric("Revenue",    f"{rev:+.1f}%/yr" if rev    is not None else None, "annual revenue change", rev_color),
        _metric("Net Margin", f"{margin:.1f}%"   if margin is not None else None, "profit per $1 of revenue", margin_color),
        _metric("EPS Beat",   beat,                                                "earnings surprises (last 4 qtrs)"),
    ], style=_COL_STYLE)

    # ── Sentiment column ──────────────────────────────────────────────────────
    buy, hold_n, sell = f.get('analyst_buy'), f.get('analyst_hold'), f.get('analyst_sell')
    target  = f.get('price_target')
    upside  = f.get('target_upside')
    short   = f.get('short_pct')
    insider = f.get('insider_net')

    # Analyst counts as coloured chips
    if all(v is not None for v in [buy, hold_n, sell]):
        analyst_val = html.Span([
            html.Span(f"{buy}", style={'color': COLOR_GOOD, 'fontWeight': '700'}),
            html.Span(" Buy  ", style={'color': COLOR_TEXT_MID}),
            html.Span(f"{hold_n}", style={'color': COLOR_TEXT_STRONG, 'fontWeight': '700'}),
            html.Span(" Hold  ", style={'color': COLOR_TEXT_MID}),
            html.Span(f"{sell}", style={'color': COLOR_BAD, 'fontWeight': '700'}),
            html.Span(" Sell", style={'color': COLOR_TEXT_MID}),
        ], style={'fontSize': '14px', 'fontWeight': '600'})
    else:
        analyst_val = None

    # Price target with upside
    if target:
        upside_color = COLOR_GOOD if upside and upside > 0 else COLOR_BAD if upside and upside < 0 else COLOR_TEXT_MID
        target_val = html.Span([
            html.Span(f"${target:,.2f}", style={'color': COLOR_TEXT_STRONG, 'fontWeight': '700', 'fontSize': '16px'}),
            html.Span(f"  ({upside:+.1f}%)" if upside is not None else '',
                      style={'color': upside_color, 'fontSize': '13px', 'fontWeight': '600'}),
        ])
    else:
        target_val = None

    short_color   = (COLOR_GOOD if short is not None and short < 2  else
                     COLOR_WARN if short is not None and short < 5  else
                     COLOR_BAD  if short is not None else None)
    insider_color = (COLOR_GOOD if insider == 'Net+'
                     else COLOR_BAD  if insider == 'Net−'
                     else COLOR_TEXT_MID)

    # Short + Insider on one row
    if short is not None or insider:
        si_val = html.Span([
            *([html.Span(f"{short:.1f}%", style={'color': short_color, 'fontWeight': '700'}),
               html.Span(" short  ", style={'color': COLOR_TEXT_MID})] if short is not None else []),
            *([html.Span(insider, style={'color': insider_color, 'fontWeight': '700'}),
               html.Span(" insider", style={'color': COLOR_TEXT_MID})] if insider else []),
        ], style={'fontSize': '14px'})
    else:
        si_val = None

    sentiment_col = html.Div([
        html.Div("Sentiment", style=_COL_HDR),
        _metric("Analysts",     analyst_val, "Wall Street recommendations"),
        _metric("Price Target", target_val,  "analyst consensus forecast"),
        _metric("Short / Insider", si_val,   "bearish bets · exec activity"),
    ], style=_COL_STYLE)

    three_cols = html.Div([
        val_col,
        html.Div(style=_DIVIDER),
        growth_col,
        html.Div(style=_DIVIDER),
        sentiment_col,
    ], style={'display': 'flex', 'padding': '14px 0 8px'})

    # ── Technicals row ────────────────────────────────────────────────────────
    rsi       = f.get('rsi_14')
    above_50d = f.get('above_50d_ma')
    vol       = f.get('volume_vs_avg')

    tech_chips = []
    if rsi is not None:
        rsi_color = (COLOR_BAD  if rsi > 70 else
                     COLOR_GOOD if rsi < 30 else COLOR_TEXT_STRONG)
        rsi_tag   = 'overbought' if rsi > 70 else 'oversold' if rsi < 30 else 'neutral'
        tech_chips.append(html.Span([
            html.Span("RSI ", style={'color': COLOR_TEXT_MID, 'fontSize': '12px'}),
            html.Span(f"{rsi:.0f}", style={'color': rsi_color, 'fontWeight': '700'}),
            html.Span(f" ({rsi_tag})", style={'color': COLOR_TEXT_MID, 'fontSize': '12px'}),
        ], style={'marginRight': '14px'}))

    if above_50d is not None:
        ma_color = COLOR_GOOD if above_50d else COLOR_BAD
        ma_text  = "Above 50d MA" if above_50d else "Below 50d MA"
        tech_chips.append(html.Span(ma_text, style={
            'color': ma_color, 'fontWeight': '600',
            'fontSize': '13px', 'marginRight': '14px',
        }))

    if vol:
        vol_color = COLOR_WARN if vol == 'High' else COLOR_TEXT_MID
        tech_chips.append(html.Span([
            html.Span("Volume ", style={'color': COLOR_TEXT_MID, 'fontSize': '12px'}),
            html.Span(vol, style={'color': vol_color, 'fontWeight': '600', 'fontSize': '13px'}),
        ]))

    tech_row = html.Div([
        html.Div("Technicals", style={**_COL_HDR, 'borderBottom': 'none',
                                      'paddingBottom': 0, 'marginBottom': '6px',
                                      'display': 'inline-block', 'marginRight': '10px'}),
        *tech_chips,
    ], style={
        'borderTop': f'1px solid {COLOR_BORDER_LIGHT}',
        'paddingTop': '10px', 'marginTop': '4px',
        'display': 'flex', 'alignItems': 'center', 'flexWrap': 'wrap', 'gap': '4px',
    }) if tech_chips else None

    # Don't render the section at all if every value is None (e.g. bond ETFs)
    has_data = any([pe, peg, ev, rev, margin, beat, buy, target, short, insider,
                    rsi, above_50d, vol])
    if not has_data:
        return None

    scored = score_fundamentals(f)
    pos    = pos or {}

    showing_brief  = view_mode == 'brief'
    toggle_label   = '← Metric Grid' if showing_brief else '📊 Investment Brief'
    grid_display   = {'display': 'none'} if showing_brief else {'display': 'block'}
    brief_display  = {'display': 'block', 'marginTop': '10px'} if showing_brief else {'display': 'none', 'marginTop': '10px'}

    toggle_btn = html.Button(
        toggle_label,
        id='fund-toggle-btn',
        n_clicks=0,
        style={
            'background': 'transparent', 'color': COLOR_BRAND,
            'border': f'1px solid {COLOR_BRAND}', 'borderRadius': '6px',
            'padding': '3px 10px', 'fontSize': '12px', 'cursor': 'pointer',
            'fontWeight': '600', 'fontFamily': 'inherit',
        },
    )

    header = html.Div([
        html.Div("Fundamentals & Signals", style={
            'fontSize': '13px', 'color': COLOR_TEXT_MID, 'fontWeight': '600',
            'textTransform': 'uppercase', 'letterSpacing': '0.05em',
        }),
        toggle_btn,
    ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center'})

    grid_view = html.Div(
        [three_cols, tech_row],
        id='fund-grid-view',
        style=grid_display,
    )
    brief_view = html.Div(
        _build_brief_section(f, scored, pos),
        id='fund-brief-view',
        style=brief_display,
    )

    return html.Div([
        header,
        grid_view,
        brief_view,
    ], style={
        'marginTop': '18px',
        'padding': '14px 16px 10px',
        'background': COLOR_SURFACE,
        'borderRadius': '10px',
        'border': f'1px solid {COLOR_BORDER_LIGHT}',
    })


def register(app):
    app.clientside_callback(
        """
        function(n, briefStyle) {
            if (!n || n === 0) return window.dash_clientside.no_update;
            var showingBrief = (briefStyle || {}).display !== 'none';
            if (showingBrief) {
                return [{'display': 'block'}, {'display': 'none', 'marginTop': '10px'}, '📊 Investment Brief', 'grid'];
            } else {
                return [{'display': 'none'}, {'display': 'block', 'marginTop': '10px'}, '← Metric Grid', 'brief'];
            }
        }
        """,
        Output('fund-grid-view',  'style'),
        Output('fund-brief-view', 'style'),
        Output('fund-toggle-btn', 'children'),
        Output('fund-view-mode',  'data'),
        Input('fund-toggle-btn',  'n_clicks'),
        State('fund-brief-view',  'style'),
        prevent_initial_call=True,
    )

    @app.callback(
        Output('selected-ticker', 'data'),
        Output('holdings-datatable', 'active_cell'),
        Output('holdings-datatable', 'selected_cells'),
        Output('fund-view-mode', 'data', allow_duplicate=True),
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
            return None, None, [], 'grid'
        if isinstance(trig, dict) and trig.get('type') == 'position-close':
            if not any(close_clicks or []):
                return no_update, no_update, no_update, no_update
            return None, None, [], 'grid'
        if not active_cell or not table_data:
            return no_update, no_update, no_update, no_update
        ticker = table_data[active_cell['row']]['ticker']
        if current == ticker:
            return None, None, [], 'grid'
        return ticker, no_update, no_update, 'grid'

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
        State('fund-view-mode', 'data'),
    )
    @safe_render('Position Detail')
    def show_position_detail(ticker: str | None, period: str, uploaded, data: PortfolioData | None,
                             fund_view: str | None):
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

        # Fundamentals panel — fetched per-ticker, 4h cached
        pos_ctx = {
            'cost_diff_pct':  cost_diff_pct if avg_cost and avg_cost > 0 else None,
            'allocation_pct': r.get('allocation_pct'),
        }
        fundamentals_section = _build_fundamentals_section(
            get_stock_fundamentals(ticker), pos_ctx, fund_view or 'grid'
        )

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
                'border': '1px solid #88A0BA', 'borderRadius': '6px',
                'padding': '5px 12px', 'fontSize': '13px',
                'cursor': 'pointer', 'fontWeight': '500',
                'display': 'inline-block',
            }),
        )
        delete_btn = html.Button(
            "Delete trade history",
            id={'type': 'position-delete-trades', 'index': 0},
            n_clicks=0,
            style={
                'background': 'transparent', 'color': COLOR_BAD,
                'border': f'1px solid {COLOR_BAD}', 'borderRadius': '6px',
                'padding': '5px 12px', 'fontSize': '13px',
                'cursor': 'pointer', 'fontWeight': '500',
                'fontFamily': 'inherit',
            },
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
                html.Li("Drop the downloaded .csv file using the button below."),
            ], style={'fontSize': '13px', 'color': COLOR_TEXT, 'lineHeight': '1.7',
                      'fontWeight': '500',
                      'paddingLeft': '20px', 'margin': '6px 0 8px'}),
            html.Div([upload_btn, delete_btn],
                     style={'display': 'flex', 'gap': '8px', 'alignItems': 'center',
                            'marginBottom': '4px'}),
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
                html.Div([_period_btn(p, p == sel_period) for p in _PERIOD_CHOICES],
                         style={'display': 'flex', 'gap': '4px'}),
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
            stats,
            fundamentals_section,
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

    @app.callback(
        Output('uploaded-trades', 'data', allow_duplicate=True),
        Output({'type': 'position-upload-status', 'index': ALL}, 'children'),
        Input({'type': 'position-delete-trades', 'index': ALL}, 'n_clicks'),
        prevent_initial_call=True,
    )
    def handle_delete_trades(n_clicks_list):
        from trade_history import clear_uploaded_trades

        if not any(n_clicks_list or []):
            return no_update, [no_update for _ in (n_clicks_list or [])]
        cleared = clear_uploaded_trades()
        msg = html.Span("Trade history deleted", style={'color': COLOR_WARN, 'fontWeight': '500'})
        return cleared, [msg for _ in (n_clicks_list or [])]
