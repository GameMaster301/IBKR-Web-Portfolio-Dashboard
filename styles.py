"""
Centralised style constants for the Dash layout.

Goal: everything in dashboard.py that sets a colour, font size, border, or
card shape references a name from here instead of a literal. Named constants:
    - make global look-and-feel changes (palette, spacing, radius) a one-line
      edit instead of a find-and-replace across hundreds of occurrences
    - surface duplication — if two inline styles happen to match, renaming
      one will show you where the other is
    - let future UI variants (dark mode, compact layout) drop in without
      touching every callback

Naming convention
-----------------
COLOR_*   — raw palette entries, used only by other constants in this file
TEXT_*    — foreground text styles (dict-style, apply to html.P / html.Span)
CARD*     — container styles for bordered white/grey panels
BADGE_*   — pill-shaped status indicators
TABLE_*   — shared table header / cell styling
"""

from __future__ import annotations

# ── Palette ───────────────────────────────────────────────────────────────────
# Use these names instead of raw hex in dashboard.py. When you want to tweak
# a tone globally, change it here.

# Semantic tones — CSS custom properties so dark mode can provide brighter
# variants without touching every callback. Light-mode values live in :root
# in custom.css; [data-theme="dark"] overrides them there.
COLOR_GOOD           = 'var(--clr-good)'      # green — positive P&L, "fairly valued"
COLOR_GOOD_SOFT      = '#22c55e'               # lighter green for secondary good states
COLOR_GOOD_BG        = 'var(--clr-good-bg)'   # pale green panel background
COLOR_WARN           = 'var(--clr-warn)'       # amber — warnings, "running hot"
COLOR_WARN_SOFT      = '#f97316'               # orange, stronger warn
COLOR_WARN_YELLOW    = '#eab308'               # yellow — mid-warn zone
COLOR_WARN_BG        = '#fffbeb'               # pale amber — also used by export.py (keep as hex)
COLOR_WARN_BORDER    = '#fde68a'               # amber border — also used by export.py (keep as hex)
COLOR_WARN_DEEP      = '#92400e'               # deep amber — also used by export.py (keep as hex)
COLOR_BAD            = 'var(--clr-bad)'        # red — negative P&L, overvalued
COLOR_BAD_DEEP       = '#991b1b'               # deep red — used by export.py (keep as hex)
COLOR_BAD_BG         = 'var(--clr-bad-bg)'    # pale red panel background
COLOR_GOOD_DEEP      = '#166534'               # deep green — used by export.py (keep as hex)
COLOR_GOOD_MEDIUM    = 'var(--clr-good-medium)'  # pale green highlight / chip background
COLOR_BRAND          = '#378ADD'               # IBKR-style blue — links, primary actions
COLOR_BRAND_BORDER   = '#cfe0f5'

# Neutrals (text and surface) — CSS custom properties so dark mode works via
# [data-theme="dark"] overrides in custom.css without touching every callback.
COLOR_TEXT_STRONG    = 'var(--clr-text-strong)'
COLOR_TEXT           = 'var(--clr-text)'
COLOR_TEXT_SLATE     = 'var(--clr-text-slate)'
COLOR_TEXT_MID       = 'var(--clr-text-mid)'
COLOR_TEXT_DIM       = 'var(--clr-text-dim)'
COLOR_TEXT_MUTED     = 'var(--clr-text-muted)'
COLOR_TEXT_SEMI      = 'var(--clr-text-semi)'
COLOR_TEXT_FAINT     = 'var(--clr-text-faint)'
COLOR_TEXT_GHOST     = 'var(--clr-text-ghost)'
COLOR_SURFACE_WHITE  = 'var(--clr-surface-white)'
COLOR_SURFACE_SOFT   = 'var(--clr-surface-soft)'
COLOR_SURFACE        = 'var(--clr-surface)'
COLOR_BORDER_LIGHT   = 'var(--clr-border-light)'
COLOR_BORDER         = 'var(--clr-border)'
COLOR_BORDER_MID     = 'var(--clr-border-mid)'
COLOR_BORDER_STRONG  = 'var(--clr-border-strong)'
COLOR_BORDER_HEAVY   = 'var(--clr-border-heavy)'


# ── Card containers ───────────────────────────────────────────────────────────

CARD: dict = {
    'border':       f'0.5px solid {COLOR_BORDER}',
    'borderRadius': '14px',
    'padding':      '24px',
}

CARD_MUTED: dict = {**CARD, 'background': COLOR_SURFACE_SOFT}


# ── Text styles ───────────────────────────────────────────────────────────────

TEXT_SECTION_LABEL: dict = {
    'fontSize':       '14px',
    'color':          COLOR_TEXT_MID,
    'margin':         '0 0 16px',
    'textTransform': 'uppercase',
    'letterSpacing': '0.07em',
    'fontWeight':    '600',
}

TEXT_HEADING: dict = {
    'fontSize':   '17px',
    'fontWeight': '600',
    'color':      COLOR_TEXT_STRONG,
    'margin':     '0 0 6px',
}

TEXT_BODY_MUTED: dict = {
    'fontSize':   '15px',
    'color':      COLOR_TEXT_MUTED,
    'margin':     '0',
    'lineHeight': '1.6',
}


# ── Links / pill buttons ──────────────────────────────────────────────────────

LINK_PILL: dict = {
    'fontSize':       '13px',
    'color':          COLOR_BRAND,
    'textDecoration': 'none',
    'padding':        '5px 12px',
    'border':         f'1px solid {COLOR_BRAND_BORDER}',
    'borderRadius':   '8px',
    'fontWeight':     '500',
    'transition':     'background 0.15s ease',
}


# ── Tables ────────────────────────────────────────────────────────────────────

TABLE_HEADER_CELL: dict = {
    'fontSize':       '13px',
    'color':          COLOR_TEXT_DIM,
    'fontWeight':     '600',
    'padding':        '0 12px 12px',
    'textTransform': 'uppercase',
    'letterSpacing': '0.04em',
    'borderBottom':   f'0.5px solid {COLOR_BORDER_LIGHT}',
}

TABLE_WRAPPER: dict = {
    'width':          '100%',
    'borderCollapse': 'collapse',
    'fontSize':       '16px',
}


# ── Badges ────────────────────────────────────────────────────────────────────
# The badge() helper in dashboard.py takes explicit (color, bg, border). These
# constants let callers name a variant instead of passing three raw hex codes.

BADGE_GOOD: tuple   = (COLOR_GOOD, COLOR_GOOD_BG, COLOR_GOOD)
BADGE_WARN: tuple   = (COLOR_WARN, COLOR_WARN_BG, COLOR_WARN_BORDER)
BADGE_BAD: tuple    = (COLOR_BAD,  COLOR_BAD_BG, COLOR_BAD)
BADGE_NEUTRAL: tuple = (COLOR_TEXT_MID, COLOR_SURFACE_SOFT, COLOR_BORDER)
