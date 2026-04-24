"""Market valuation panel: Buffett Indicator, S&P 500 P/E, Shiller CAPE
(with 50-year history chart), and the derived Yield Gap verdict.

Two callbacks:
    - populate_valuation_data: parallel fan-out to the 4 macro getters,
      each independently 4-hour cached.
    - render_market_valuation: renders the three metric cards + yield-gap
      box + CAPE history chart from the valuation-data store.
"""

from __future__ import annotations

import plotly.graph_objects as go
from dash import Input, Output, dcc, html

from dashboard_core.helpers import section_label
from decorators import NotReadyError, safe_render
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
from styles import (
    CARD,
    COLOR_BAD,
    COLOR_BORDER_HEAVY,
    COLOR_BRAND,
    COLOR_GOOD,
    COLOR_GOOD_SOFT,
    COLOR_SURFACE,
    COLOR_SURFACE_WHITE,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    COLOR_TEXT_FAINT,
    COLOR_TEXT_GHOST,
    COLOR_TEXT_MID,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_SEMI,
    COLOR_TEXT_STRONG,
    COLOR_WARN_SOFT,
    COLOR_WARN_YELLOW,
)


def register(app):
    @app.callback(
        Output('valuation-data', 'data'),
        Input('refresh-interval', 'n_intervals'),
    )
    def populate_valuation_data(_):
        """
        Fetch all four valuation metrics in parallel and store them.
        Each getter has its own 4-hour cache, so real network calls are rare.
        On cold start the HTTP requests run concurrently — total latency is
        bounded by the slowest one, not all four. Failures are isolated per
        metric; one failing doesn't block the others.
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
    @safe_render('Market Valuation')
    def render_market_valuation(data):
        return _render_market_valuation_inner(data)


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
        'backgroundColor': COLOR_TEXT_STRONG, 'borderRadius': '2px', 'zIndex': '10',
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
                           'color': COLOR_BORDER_HEAVY, 'margin': '0 0 4px'}),
        html.Span("Data unavailable",
                  style={'fontSize': '13px', 'color': COLOR_TEXT_GHOST}),
    ])


def _render_market_valuation_inner(data):
    if data is None:
        raise NotReadyError('Loading valuation data…')

    buffett_d  = data.get('buffett')
    pe_d       = data.get('sp500_pe')
    cape_d     = data.get('cape')
    treasury_d = data.get('treasury')

    # ── Card builder ──────────────────────────────────────────────────────────
    def metric_card(title, subtitle, body, footer=None):
        return html.Div([
            html.P(title, style={
                'fontSize': '13px', 'color': COLOR_TEXT_MID, 'margin': '0 0 2px',
                'textTransform': 'uppercase', 'letterSpacing': '0.06em', 'fontWeight': '600',
            }),
            html.P(subtitle, style={
                'fontSize': '13px', 'color': COLOR_TEXT_MUTED, 'margin': '0 0 16px',
            }),
            body,
            html.P(footer, style={
                'fontSize': '13px', 'color': COLOR_TEXT_SEMI, 'margin': '14px 0 0',
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

    def big_value(text, color=COLOR_TEXT_STRONG):
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
                ('Well Below Norms',   75,  COLOR_GOOD),
                ('Fairly Valued',     110,  COLOR_GOOD_SOFT),
                ('Modern',            150,  '#84cc16'),
                ('Hot',               190,  COLOR_WARN_SOFT),
                ('Stretched',         280,  COLOR_BAD),
            ], display_max=280),
            html.Div([
                html.Span(f"Mkt Cap  ${buffett_d['market_cap_t']:.1f}T",
                          style={'fontSize': '13px', 'color': COLOR_TEXT_DIM}),
                html.Span('  ·  ', style={'color': COLOR_TEXT_GHOST, 'fontSize': '13px'}),
                html.Span(f"GDP  ${buffett_d['gdp_t']:.1f}T",
                          style={'fontSize': '13px', 'color': COLOR_TEXT_FAINT}),
            ], style={'marginTop': '8px'}),
            html.Div([
                html.Span(
                    f'The market is {bv/100:.1f}× the size of the US economy'
                    f' — {above_trend_pct:.0f}% above the modern trend line.',
                    style={'fontSize': '12px', 'color': COLOR_TEXT_MID, 'fontStyle': 'italic'},
                ),
                html.Br(),
                html.Span(
                    f'GDP as of {buffett_d["gdp_quarter"]} ({buffett_d["gdp_source"]})'
                    ' — 1–2 quarter lag is normal.',
                    style={'fontSize': '11px', 'color': COLOR_TEXT_GHOST},
                ),
            ], style={'marginTop': '6px'}),
        ])
        b_foot = (
            'Buffett: “The best single measure of where valuations stand at any given moment.” '
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
                    ('Cheap',              15, COLOR_GOOD),
                    ('Fairly Valued',      20, COLOR_GOOD_SOFT),
                    ('Expensive',          25, COLOR_WARN_YELLOW),
                    ('Very Expensive',     30, COLOR_WARN_SOFT),
                    ('Extremely Exp.',     45, COLOR_BAD),
                ], display_max=45),
                html.Div([
                    html.Span(f'{main_lbl}: {main_val:.1f}×',
                              style={'fontSize': '13px', 'color': COLOR_TEXT_DIM}),
                    (html.Span(f'  ·  Forward: {forward:.1f}×',
                               style={'fontSize': '13px', 'color': COLOR_TEXT_DIM})
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
                ('Undervalued',       15, COLOR_GOOD),
                ('Fairly Valued',     20, COLOR_GOOD_SOFT),
                ('Overvalued',        25, COLOR_WARN_YELLOW),
                ('Highly Overval.',   30, COLOR_WARN_SOFT),
                ('Extremely Overv.',  50, COLOR_BAD),
            ], display_max=50),
            html.Div([
                html.Span(f"Hist. mean {cape_d['hist_mean']:.1f}×",
                          style={'fontSize': '13px', 'color': COLOR_TEXT_DIM}),
                html.Span(' · ', style={'color': COLOR_TEXT_GHOST, 'fontSize': '13px'}),
                html.Span(f"Median {cape_d['hist_median']:.1f}×",
                          style={'fontSize': '13px', 'color': COLOR_TEXT_DIM}),
                html.Span(f"  ·  as of {cape_d['last_date']}",
                          style={'fontSize': '13px', 'color': COLOR_TEXT_MUTED}),
            ], style={'marginTop': '8px'}),
        ])
        cape_foot = (
            'Uses 10 years of inflation-adjusted earnings to smooth short-term noise. '
            f'100-year average: ~{cape_d["hist_mean"]:.0f}×. '
            'The modern (20-year) average is ~25×, reflecting higher structural '
            'valuations since the tech era. The chart marks major crashes to show that '
            'elevated readings did eventually matter — just not on a fixed timeline.'
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

        if   gap >  2:   gap_label, gap_color = 'Stocks strongly favoured',  COLOR_GOOD
        elif gap >  0:   gap_label, gap_color = 'Stocks slightly favoured',   COLOR_GOOD_SOFT
        elif gap > -1:   gap_label, gap_color = 'Roughly equal',              COLOR_WARN_YELLOW
        elif gap > -2:   gap_label, gap_color = 'Bonds competitive',          COLOR_WARN_SOFT
        else:            gap_label, gap_color = 'Bonds clearly favoured',     COLOR_BAD

        gap_sign = '+' if gap >= 0 else ''

        context_note = html.Div([
            html.Div([
                # Left: formula breakdown
                html.Div([
                    html.Span('Yield Gap', style={
                        'fontWeight': '700', 'fontSize': '13px', 'color': COLOR_TEXT,
                        'display': 'block', 'marginBottom': '4px',
                    }),
                    html.Span(
                        f'S&P earnings yield ({earnings_yield:.2f}%) '
                        f'− 10-yr bond yield ({tv:.2f}%)',
                        style={'fontSize': '14px', 'color': COLOR_TEXT_MID},
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
                'government bonds — a sign investors are still being rewarded for '
                'the extra risk. When it turns negative, bonds pay more than stocks earn, '
                'which makes expensive valuations harder to justify.',
                style={'fontSize': '14px', 'color': COLOR_TEXT_MID,
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
                    annotation_font=dict(size=11, color=COLOR_TEXT_SEMI),
                )

        # All-time historical mean line (dotted, grey)
        fig.add_hline(y=mean_val, line_dash='dot',
                      line_color=COLOR_TEXT_GHOST, line_width=1,
                      annotation_text=f'100-yr mean {mean_val:.0f}×',
                      annotation_position='top left',
                      annotation_font=dict(size=10, color=COLOR_TEXT_GHOST))

        # Modern mean line (dashed, blue-grey) — last 20 years
        fig.add_hline(y=modern_mean, line_dash='dash',
                      line_color=COLOR_BRAND, line_width=1,
                      annotation_text=f'20-yr mean {modern_mean:.0f}×',
                      annotation_position='bottom left',
                      annotation_font=dict(size=10, color=COLOR_BRAND))

        # CAPE line
        fig.add_trace(go.Scatter(
            x=dates_plot, y=vals_plot,
            mode='lines',
            line=dict(color=COLOR_BRAND, width=2),
            name='Shiller CAPE',
            hovertemplate='%{x}  ·  CAPE <b>%{y:.1f}×</b><extra></extra>',
        ))

        # Current value dot
        fig.add_trace(go.Scatter(
            x=[dates_plot[-1]], y=[vals_plot[-1]],
            mode='markers+text',
            marker=dict(color=ccolor if cape_d else COLOR_BRAND, size=10,
                        line=dict(color=COLOR_SURFACE_WHITE, width=2)),
            text=[f'  {vals_plot[-1]:.1f}×'],
            textposition='middle right',
            textfont=dict(size=11, color=ccolor if cape_d else COLOR_BRAND),
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
                       tickfont=dict(size=10, color=COLOR_TEXT_GHOST),
                       tickangle=-30),
            yaxis=dict(showgrid=True, gridcolor=COLOR_SURFACE, zeroline=False,
                       tickfont=dict(size=10, color=COLOR_TEXT_GHOST),
                       ticksuffix='×', title=None),
            height=260,
        )

        cape_chart = html.Div([
            html.P("Shiller CAPE — 50-year history", style={
                'fontSize': '14px', 'color': '#000', 'margin': '20px 0 4px',
                'textTransform': 'uppercase', 'letterSpacing': '0.05em',
            }),
            html.P([
                html.Span("▮ Undervalued (<15)",        style={'color': COLOR_GOOD}),
                html.Span("  ·  ", style={'color': COLOR_TEXT_GHOST}),
                html.Span("▮ Fairly Valued (15–20)",  style={'color': COLOR_GOOD_SOFT}),
                html.Span("  ·  ", style={'color': COLOR_TEXT_GHOST}),
                html.Span("▮ Overvalued (20–25)",      style={'color': COLOR_WARN_YELLOW}),
                html.Span("  ·  ", style={'color': COLOR_TEXT_GHOST}),
                html.Span("▮ Highly Overvalued (25–30)", style={'color': COLOR_WARN_SOFT}),
                html.Span("  ·  ", style={'color': COLOR_TEXT_GHOST}),
                html.Span("▮ Extremely Overvalued (30+)",  style={'color': COLOR_BAD}),
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
