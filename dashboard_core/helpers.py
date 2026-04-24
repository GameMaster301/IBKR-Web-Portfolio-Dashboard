"""Pure presentation helpers shared by every dashboard section.

Extracted verbatim from ``dashboard.py`` (step 5 of the roadmap). Keep this
module dependency-free aside from ``dash`` and ``styles`` so any future
submodule can import it without pulling in callback-registration code.
"""

from __future__ import annotations

from dash import html

from styles import (
    COLOR_BORDER,
    LINK_PILL,
    TABLE_HEADER_CELL,
    TABLE_WRAPPER,
    TEXT_BODY_MUTED,
    TEXT_HEADING,
    TEXT_SECTION_LABEL,
)


def to_eur(usd, rate):
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
