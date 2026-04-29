"""Pure presentation helpers shared by every dashboard section.

Extracted verbatim from ``dashboard.py`` (step 5 of the roadmap). Keep this
module dependency-free aside from ``dash`` and ``styles`` so any future
submodule can import it without pulling in callback-registration code.
"""

from __future__ import annotations

from dash import html

from config import cfg
from styles import (
    COLOR_BORDER,
    LINK_PILL,
    TABLE_HEADER_CELL,
    TABLE_WRAPPER,
    TEXT_BODY_MUTED,
    TEXT_HEADING,
    TEXT_SECTION_LABEL,
)

# Single reference for the EUR/USD fallback rate (used when IBKR hasn't
# delivered a live rate yet). Reads from cfg so EURUSD_FALLBACK env var
# or config.yaml overrides flow through without touching any callback.
EURUSD_FALLBACK: float = cfg['display']['eurusd_fallback']

# Currency symbol map — covers the most common IBKR account currencies.
_CCY_SYMBOLS: dict[str, str] = {
    'USD': '$', 'EUR': '€', 'GBP': '£', 'CHF': 'Fr ',
    'CAD': 'C$', 'AUD': 'A$', 'JPY': '¥', 'HKD': 'HK$',
}


def ccy_symbol(ccy: str) -> str:
    """Return the display symbol for an ISO currency code (e.g. 'EUR' → '€')."""
    return _CCY_SYMBOLS.get((ccy or 'USD').upper(), (ccy or 'USD') + ' ')


def to_base(usd_val: float, rate: float, base_ccy: str = 'EUR') -> float:
    """
    Convert a USD-denominated value to the account's base currency.
    - EUR base: divide by EUR/USD rate
    - USD base: return as-is (already in base)
    - Other:    return as-is (no rate available — caller displays USD)
    """
    if not base_ccy or base_ccy == 'USD' or not rate:
        return usd_val
    if base_ccy == 'EUR':
        return usd_val / rate
    return usd_val


def to_eur(usd: float, rate: float) -> float:
    """Compatibility shim — converts USD → EUR. Prefer to_base() for new code."""
    return usd / rate if rate else usd


# CARD is re-exported so legacy inline references `style={**CARD, ...}` keep
# working without change. Source of truth lives in styles.py.
_LINK_STYLE = LINK_PILL


def section_label(text):
    return html.P(text, style=TEXT_SECTION_LABEL)


def make_table(cols, rows):
    header = html.Tr([
        html.Th(c, style={**TABLE_HEADER_CELL,
                          'textAlign': 'right' if i > 0 else 'left'})
        for i, c in enumerate(cols)
    ])
    return html.Table([html.Thead(header), html.Tbody(rows)],
                      style=TABLE_WRAPPER)


def badge(text, color, bg, border):
    return html.Span(text, style={
        'fontSize': '14px', 'color': color, 'background': bg,
        'padding': '4px 10px', 'borderRadius': '20px',
        'border': f'0.5px solid {border}',
    })


def status_banner(icon, title, body, color):
    return html.Div([
        html.Div(icon, style={'fontSize': '32px', 'marginBottom': '14px'}),
        html.P(title, style=TEXT_HEADING),
        html.P(body,  style=TEXT_BODY_MUTED),
    ], style={
        'textAlign':    'center',
        'padding':      '48px 32px',
        'background':   color,
        'borderRadius': '14px',
        'border':       f'0.5px solid {COLOR_BORDER}',
    })
