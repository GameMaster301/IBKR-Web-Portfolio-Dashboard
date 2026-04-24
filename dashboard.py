"""
Dash layout and callbacks for the IBKR portfolio dashboard.

This module is intentionally large — it owns the full UI surface (layout +
every @app.callback). A split into `dashboard_core/` submodules is planned
(see notes.txt, step 5). Until then keep this file self-contained: helpers
at the top, layout in one place, callbacks grouped by section banner.
"""

from __future__ import annotations

import logging

import dash
from dash import dcc, html

from config import cfg
from dashboard_core import coach_ui as _coach_ui_mod
from dashboard_core import data_callbacks as _data_callbacks_mod
from dashboard_core import detail as _detail_mod
from dashboard_core import export as _export_mod
from dashboard_core import intel as _intel_mod
from dashboard_core import summary as _summary_mod
from dashboard_core import valuation as _valuation_mod
from dashboard_core.helpers import (
    section_label,
)
from styles import CARD, LINK_PILL
from trade_history import (
    load_uploaded_trades,
)

log = logging.getLogger(__name__)

app = dash.Dash(__name__, suppress_callback_exceptions=True)

# Re-exported so legacy inline references `style=_LINK_STYLE` keep working.
# Source of truth lives in styles.py.
_LINK_STYLE = LINK_PILL

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
                html.Div(
                    html.Button("Exit demo", id='exit-demo-btn', n_clicks=0, style={
                        'fontSize': '13px', 'color': '#92400e', 'background': '#fffbeb',
                        'border': '0.5px solid #fcd34d', 'borderRadius': '8px',
                        'padding': '6px 12px', 'cursor': 'pointer',
                    }),
                    id='exit-demo-wrap',
                    style={'display': 'none'},
                ),
                html.Button("↓ PDF", id='export-pdf-btn', n_clicks=0, style={
                    'fontSize': '13px', 'color': '#555', 'background': '#f5f5f5',
                    'border': '0.5px solid #ddd', 'borderRadius': '8px',
                    'padding': '6px 14px', 'cursor': 'pointer',
                }),
            ], id='header-actions', style={'display': 'flex', 'alignItems': 'center', 'gap': '12px'}),
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

    # Retry-connection + Try-demo buttons — shown only when status='disconnected'.
    # Live in the static layout so their callbacks always register.
    html.Div([
        html.Button("↻ Retry connection", id='retry-connection-btn', n_clicks=0, style={
            'fontSize': '14px', 'fontWeight': '500', 'color': '#fff',
            'background': '#dc2626', 'border': 'none', 'borderRadius': '8px',
            'padding': '10px 22px', 'cursor': 'pointer',
        }),
        html.Button("▶ Try demo mode", id='try-demo-btn', n_clicks=0, style={
            'fontSize': '14px', 'fontWeight': '500', 'color': '#111',
            'background': '#fff', 'border': '0.5px solid #d4d4d4', 'borderRadius': '8px',
            'padding': '10px 22px', 'cursor': 'pointer', 'marginLeft': '10px',
        }),
        html.P("No TWS? Explore the dashboard with a sample portfolio.",
               style={'fontSize': '13px', 'color': '#888', 'margin': '10px 0 0'}),
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
                        'fontSize': '13px', 'color': '#555', 'background': '#f5f5f5',
                        'border': '0.5px solid #ddd', 'borderRadius': '8px',
                        'padding': '6px 14px', 'cursor': 'pointer',
                    }),
                ], style={'display': 'flex', 'justifyContent': 'space-between',
                          'alignItems': 'center', 'marginBottom': '0px'}),
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

    # Position detail panel (shown on row click). Rendered BEFORE the coach
    # panel so that when both are open the ticker chart sits above the coach
    # conversation — the natural reading order for "I clicked a ticker and
    # then asked the coach about it".
    html.Div(id='position-detail'),

    # AI Coach panel (shown when the ✨ Ask coach button is clicked)
    html.Div(id='coach-panel'),

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
    # Coach panel state
    dcc.Store(id='coach-open', data=False),
    dcc.Store(id='coach-mode', data='preset'),            # 'preset' or 'ai'
    dcc.Store(id='coach-active-id', data=None),           # currently picked scenario id
    # Browser-persistent API key (localStorage). Never touches the server's disk.
    dcc.Store(id='coach-api-key', storage_type='local', data=''),
    # Thread list (persisted): [{id, title, created, history:[{q,a,error?,followups?}]}]
    dcc.Store(id='coach-threads', storage_type='local', data=[]),
    # Active thread id (persisted)
    dcc.Store(id='coach-active-thread-id', storage_type='local', data=None),
    # Derived: history of the currently active thread — kept as a store so all
    # the chat-rendering + copy callbacks stay unchanged.
    dcc.Store(id='coach-chat-history', data=[]),
    # Pre-fill for the chat input — set when the user clicks "Ask coach" from
    # a position-detail panel, or when they hit Edit on a past question.
    dcc.Store(id='coach-prefill', data=''),
    # Pending user question — set by submit_question, consumed by run_llm.
    # run_llm's Input is this Store, so writing a question triggers the
    # LLM call directly (no Interval indirection needed).
    dcc.Store(id='coach-pending-q', data=None),
    # Dummy sinks for clientside callbacks (copy / auto-scroll)
    dcc.Store(id='coach-copy-signal', data=0),
    dcc.Store(id='coach-scroll-signal', data=0),
    # Sinks for smooth-scroll-into-view clientside callbacks
    dcc.Store(id='position-detail-scroll-signal', data=0),
    dcc.Store(id='coach-panel-scroll-signal', data=0),

], id='app-root', style={
    'fontFamily': '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    'fontSize': '16px',
    'padding': '48px 64px',
    'maxWidth': '1400px',
    'margin': '0 auto',
    'backgroundColor': '#fff',
    'color': '#111',
})


# ── Data fetch + status banner + retry + demo toggles + keyboard shortcuts ───
# Moved to dashboard_core/data_callbacks.py.
_data_callbacks_mod.register(app)


# ── Summary cards + Holdings + Donut + Dividends ──────────────────────────────
# Moved to dashboard_core/summary.py.
_summary_mod.register(app)


# ── Export ─────────────────────────────────────────────────────────────────────
# PDF export moved to dashboard_core/export.py.
_export_mod.register(app)


# ── Position detail (click to expand) + per-position CSV upload ───────────────
# Moved to dashboard_core/detail.py.
_detail_mod.register(app)



# ── Toast notifications + Market Intelligence (sector/geo + earnings) ─────────
# Moved to dashboard_core/intel.py.
_intel_mod.register(app)


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET VALUATION CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════════
# Moved to dashboard_core/valuation.py.
_valuation_mod.register(app)

# ═══════════════════════════════════════════════════════════════════════════════
# AI COACH
# ═══════════════════════════════════════════════════════════════════════════════
# Moved to dashboard_core/coach_ui.py.
_coach_ui_mod.register(app)
