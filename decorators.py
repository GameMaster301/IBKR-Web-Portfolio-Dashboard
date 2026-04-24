"""Error and loading boundaries for Dash render callbacks.

Wrap any render callback whose job is to turn store data into children.
The decorator catches NotReadyError and generic exceptions, returning a
consistent placeholder instead of letting Dash show a 500.

Usage:
    @app.callback(Output('my-section', 'children'), Input('portfolio-data', 'data'))
    @safe_render('My Section')
    def my_callback(data: PortfolioData | None):
        if not data:
            raise NotReadyError('Portfolio not loaded yet.')
        ...
"""

from __future__ import annotations

import logging
from functools import wraps

from dash import html

from styles import CARD, COLOR_TEXT_GHOST, COLOR_WARN, COLOR_WARN_BG, COLOR_WARN_BORDER

log = logging.getLogger(__name__)


class NotReadyError(Exception):
    """Raised by a render callback when its input store isn't ready yet."""


def _loading(msg: str) -> html.Div:
    return html.Div(
        html.P(msg, style={
            'fontSize': '15px', 'color': COLOR_TEXT_GHOST,
            'textAlign': 'center', 'padding': '32px 0', 'margin': '0',
        }),
        style=CARD,
    )


def _error(label: str, err: Exception) -> html.Div:
    log.exception('safe_render [%s] failed', label)
    return html.Div([
        html.P(f'{label} unavailable',
               style={'color': COLOR_WARN, 'fontWeight': '600', 'margin': '0 0 6px'}),
        html.P(f'({type(err).__name__}: {err})',
               style={'color': '#888', 'fontSize': '13px', 'margin': '0'}),
    ], style={**CARD, 'background': COLOR_WARN_BG, 'borderLeft': f'3px solid {COLOR_WARN_BORDER}'})


def safe_render(label: str):
    """Decorator factory. `label` appears in the error card shown to the user."""
    def outer(fn):
        @wraps(fn)
        def inner(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except NotReadyError as e:
                return _loading(str(e) or 'Loading…')
            except Exception as e:
                return _error(label, e)
        return inner
    return outer
