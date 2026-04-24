"""Full app.layout tree. Call build_layout(refresh_ms) and assign to app.layout."""

from __future__ import annotations

from dash import dcc, html

from dashboard_core.helpers import section_label
from styles import (
    CARD,
    COLOR_BAD,
    COLOR_SURFACE,
    COLOR_SURFACE_WHITE,
    COLOR_TEXT_MID,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_STRONG,
    COLOR_WARN_BG,
    COLOR_WARN_DEEP,
)
from trade_history import load_uploaded_trades


def build_layout(refresh_ms: int) -> html.Div:
    return html.Div([

        # Sticky header
        html.Div([
            html.Div([
                html.Div([
                    html.H1("Portfolio", style={'margin': '0', 'fontSize': '22px', 'fontWeight': '600', 'color': COLOR_TEXT_STRONG}),
                    html.P(id='last-updated', style={'margin': '4px 0 0', 'color': COLOR_TEXT_MUTED, 'fontSize': '14px'}),
                ]),
                html.Div([
                    html.Div(id='connection-badge'),
                    html.Div(
                        html.Button("Exit demo", id='exit-demo-btn', n_clicks=0, style={
                            'fontSize': '13px', 'color': COLOR_WARN_DEEP, 'background': COLOR_WARN_BG,
                            'border': '0.5px solid #fcd34d', 'borderRadius': '8px',
                            'padding': '6px 12px', 'cursor': 'pointer',
                        }),
                        id='exit-demo-wrap',
                        style={'display': 'none'},
                    ),
                    html.Button("↓ PDF", id='export-pdf-btn', n_clicks=0, style={
                        'fontSize': '13px', 'color': COLOR_TEXT_MID, 'background': COLOR_SURFACE,
                        'border': '0.5px solid #ddd', 'borderRadius': '8px',
                        'padding': '6px 14px', 'cursor': 'pointer',
                    }),
                ], id='header-actions', style={'display': 'flex', 'alignItems': 'center', 'gap': '12px'}),
            ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'flex-start'}),
        ], id='sticky-header', style={
            'position': 'sticky', 'top': '0', 'zIndex': '100',
            'backgroundColor': COLOR_SURFACE_WHITE,
            'marginLeft': '-64px', 'marginRight': '-64px',
            'paddingLeft': '64px', 'paddingRight': '64px',
            'paddingTop': '24px', 'paddingBottom': '17px',
            'marginBottom': '28px',
            'borderBottom': '0.5px solid #ebebeb',
        }),

        # Status / loading banner
        html.Div(id='status-banner', style={'marginBottom': '24px'}),

        # Retry-connection + Try-demo buttons
        html.Div([
            html.Button("↻ Retry connection", id='retry-connection-btn', n_clicks=0, style={
                'fontSize': '14px', 'fontWeight': '500', 'color': COLOR_SURFACE_WHITE,
                'background': COLOR_BAD, 'border': 'none', 'borderRadius': '8px',
                'padding': '10px 22px', 'cursor': 'pointer',
            }),
            html.Button("▶ Try demo mode", id='try-demo-btn', n_clicks=0, style={
                'fontSize': '14px', 'fontWeight': '500', 'color': COLOR_TEXT_STRONG,
                'background': COLOR_SURFACE_WHITE, 'border': '0.5px solid #d4d4d4', 'borderRadius': '8px',
                'padding': '10px 22px', 'cursor': 'pointer', 'marginLeft': '10px',
            }),
            html.P("No TWS? Explore the dashboard with a sample portfolio.",
                   style={'fontSize': '13px', 'color': COLOR_TEXT_MUTED, 'margin': '10px 0 0'}),
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
                            'fontSize': '13px', 'color': COLOR_TEXT_MID, 'background': COLOR_SURFACE,
                            'border': '0.5px solid #ddd', 'borderRadius': '8px',
                            'padding': '6px 14px', 'cursor': 'pointer',
                        }),
                    ], style={'display': 'flex', 'justifyContent': 'space-between',
                              'alignItems': 'center', 'marginBottom': '0px'}),
                    html.Span(id='positions-count', style={
                        'fontSize': '14px', 'color': COLOR_TEXT_MUTED,
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

        html.Div(id='position-detail'),
        html.Div(id='coach-panel'),

        # Market Intelligence header
        html.Div([
            html.P("Market Intelligence", style={
                'fontSize': '15px', 'color': COLOR_TEXT_MUTED, 'margin': '0 0 4px',
                'textTransform': 'uppercase', 'letterSpacing': '0.07em', 'fontWeight': '600',
            }),
            html.P("Sector & geography ",
                   style={'fontSize': '15px', 'color': COLOR_TEXT_MUTED, 'margin': '0'}),
        ], style={
            'marginTop': '40px', 'paddingTop': '32px',
            'borderTop': '0.5px solid #f0f0f0', 'marginBottom': '24px',
        }),

        html.Div(id='sector-geo-section'),

        html.Div(id='earnings-section', style={
            'marginTop': '32px', 'paddingTop': '28px',
            'borderTop': '0.5px solid #f0f0f0',
        }),

        html.Div(id='dividend-section', style={
            'marginTop': '32px', 'paddingTop': '28px',
            'borderTop': '0.5px solid #f0f0f0',
        }),

        html.Div(id='market-valuation-section', style={
            'marginTop': '32px', 'paddingTop': '28px',
            'borderTop': '0.5px solid #f0f0f0',
        }),

        # Toast
        html.Div(id='toast', style={
            'position': 'fixed', 'bottom': '28px', 'right': '28px',
            'zIndex': '9999', 'pointerEvents': 'none',
        }),

        # Hidden keyboard-shortcut trigger buttons
        html.Button(id='kb-refresh-btn', n_clicks=0,
                    style={'display': 'none', 'position': 'absolute'}),
        html.Button(id='kb-escape-btn',  n_clicks=0,
                    style={'display': 'none', 'position': 'absolute'}),

        dcc.Download(id='download-pdf'),
        dcc.Interval(id='startup-interval', interval=2000, n_intervals=0, max_intervals=1),
        dcc.Interval(id='refresh-interval', interval=refresh_ms, n_intervals=0),
        dcc.Store(id='portfolio-data'),
        dcc.Store(id='market-intel-data'),
        dcc.Store(id='valuation-data'),
        dcc.Store(id='connection-status', data='loading'),
        dcc.Store(id='selected-ticker', data=None),
        dcc.Store(id='selected-period', data='1M'),
        dcc.Store(id='uploaded-trades', data=load_uploaded_trades()),
        dcc.Store(id='coach-open', data=False),
        dcc.Store(id='coach-mode', data='preset'),
        dcc.Store(id='coach-active-id', data=None),
        dcc.Store(id='coach-api-key', storage_type='local', data=''),
        dcc.Store(id='coach-threads', storage_type='local', data=[]),
        dcc.Store(id='coach-active-thread-id', storage_type='local', data=None),
        dcc.Store(id='coach-chat-history', data=[]),
        dcc.Store(id='coach-prefill', data=''),
        dcc.Store(id='coach-pending-q', data=None),
        dcc.Store(id='coach-copy-signal', data=0),
        dcc.Store(id='coach-scroll-signal', data=0),
        dcc.Store(id='position-detail-scroll-signal', data=0),
        dcc.Store(id='coach-panel-scroll-signal', data=0),

    ], id='app-root', style={
        'fontFamily': '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        'fontSize': '16px',
        'padding': '48px 64px',
        'maxWidth': '1400px',
        'margin': '0 auto',
        'backgroundColor': COLOR_SURFACE_WHITE,
        'color': COLOR_TEXT_STRONG,
    })
