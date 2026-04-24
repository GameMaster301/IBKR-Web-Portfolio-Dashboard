"""
Portfolio Coach — rules-based scenario answers for beginner investors.

Each scenario is a pure function:
    fn(portfolio_data, market_intel, valuation_data) -> list of Dash children

No network calls, no LLMs, no external state. Everything is computed from
stores the dashboard already maintains. When any required piece of data is
missing the scenario returns a friendly "not ready yet" block instead of
erroring — callers can render the output directly.

The plain-English phrasing is deliberate. Thresholds are conservative and
explicit so a beginner can sanity-check the reasoning against the numbers
shown elsewhere on the dashboard.
"""

from __future__ import annotations

from dash import html

# ── Presentation helpers ──────────────────────────────────────────────────────

_TONE = {
    'good':  ('#16a34a', '#f0fdf4', '#bbf7d0'),   # fg, bg, border
    'warn':  ('#b45309', '#fffbeb', '#fde68a'),
    'info':  ('#374151', '#f9fafb', '#e5e7eb'),
}


def _answer(headline: str, body: list[str], tone: str = 'info'):
    fg, bg, bd = _TONE.get(tone, _TONE['info'])
    return html.Div([
        html.P(headline, style={
            'margin': '0 0 10px', 'fontWeight': '600', 'fontSize': '16px', 'color': fg,
        }),
        *[html.P(p, style={
            'margin': '0 0 8px', 'fontSize': '15px', 'color': '#374151', 'lineHeight': '1.55',
        }) for p in body],
    ], style={
        'background': bg, 'border': f'0.5px solid {bd}', 'borderRadius': '10px',
        'padding': '16px 18px',
    })


def _not_ready(reason: str):
    return _answer("Not enough data yet",
                   [reason + " Try again in a few seconds once the dashboard has finished loading."],
                   tone='info')


def _fmt_eur(v: float) -> str:
    sign = '-' if v < 0 else ''
    return f"{sign}€{abs(v):,.0f}"


def _fmt_pct(v: float) -> str:
    sign = '+' if v > 0 else ''
    return f"{sign}{v:.1f}%"


# ── Scenarios ─────────────────────────────────────────────────────────────────

def scenario_performance(port, _intel, _val):
    if not port or 'summary' not in port or not port.get('positions'):
        return _not_ready("Positions haven't loaded yet.")
    s = port['summary']
    pnl      = s.get('total_unrealized_pnl', 0)
    pnl_pct  = s.get('total_pnl_pct', 0)
    best     = s.get('best_performer', '—')
    worst    = s.get('worst_performer', '—')
    total    = s.get('total_value', 0)
    daily    = s.get('total_daily_pnl')

    # find per-ticker p&l% for the best/worst for richer text
    by_ticker = {p['ticker']: p for p in port['positions']}
    best_pct  = by_ticker.get(best,  {}).get('pnl_pct', 0)
    worst_pct = by_ticker.get(worst, {}).get('pnl_pct', 0)

    losers = [p for p in port['positions'] if p.get('pnl_pct', 0) < 0]

    tone = 'good' if pnl_pct >= 0 else 'warn'
    lines = [
        f"Portfolio value: {_fmt_eur(total)} · Unrealised P&L: "
        f"{_fmt_eur(pnl)} ({_fmt_pct(pnl_pct)} on cost).",
        f"Biggest winner: {best} ({_fmt_pct(best_pct)}). "
        + (f"Biggest drag: {worst} ({_fmt_pct(worst_pct)})."
           if losers else "No losers today — every position is in the green, which is a good reminder that this won't always be the case."),
    ]
    if daily is not None:
        lines.append(f"Today's move: {_fmt_eur(daily)}.")

    headline = ("Good shape — up overall." if pnl_pct >= 0
                else "Down overall — worth reviewing which positions are dragging.")
    return _answer(headline, lines, tone=tone)


def scenario_biggest_risk(port, intel, val):
    if not port or 'summary' not in port:
        return _not_ready("Positions haven't loaded yet.")

    s       = port['summary']
    top     = s.get('largest_position', '—')
    top_pct = s.get('largest_position_pct', 0)

    # Sector concentration (reuses the same exposure market-intel builds)
    top_sector_name, top_sector_pct = None, 0
    if intel and intel.get('sector_geo'):
        sector_pct: dict[str, float] = {}
        for p in port['positions']:
            sg = intel['sector_geo'].get(p['ticker'], {})
            w  = p.get('allocation_pct', 0)
            sw = sg.get('sector_weights') or {}
            if sw:
                for sec, frac in sw.items():
                    sector_pct[sec] = sector_pct.get(sec, 0) + w * frac / 100.0
            elif sg.get('sector'):
                sector_pct[sg['sector']] = sector_pct.get(sg['sector'], 0) + w
        if sector_pct:
            top_sector_name, top_sector_pct = max(sector_pct.items(), key=lambda kv: kv[1])

    # Valuation context — CAPE and Buffett, if available
    cape_val = (val or {}).get('cape', {}).get('value') if val else None
    buf_val  = (val or {}).get('buffett', {}).get('value') if val else None

    # Rank risks: concentration > sector > valuation
    if top_pct >= 50:
        body = [
            f"Concentration. {top} alone is {top_pct:.0f}% of your portfolio — "
            f"if it drops 10%, that's about {top_pct/10:.1f}% of everything.",
            "For a beginner diversified portfolio, many advisors cap any single position at 20–30%. "
            "Above 50% is a large single-name bet — fine if intentional, worth knowing either way.",
        ]
        if top_sector_name and top_sector_pct >= 40:
            body.append(
                f"Sector-wise you're also heavy in {top_sector_name} (~{top_sector_pct:.0f}% of holdings), "
                "which doubles the concentration."
            )
        return _answer("Biggest risk: concentration in one holding.", body, tone='warn')

    if top_sector_name and top_sector_pct >= 45:
        body = [
            f"Sector tilt. You're ~{top_sector_pct:.0f}% in {top_sector_name} across all holdings combined.",
            "A rotation out of that sector would hit you harder than the broader market. "
            "Roughly 30–35% in any one sector is a common beginner ceiling.",
        ]
        return _answer("Biggest risk: sector concentration.", body, tone='warn')

    if cape_val and cape_val >= 32:
        body = [
            f"Market-wide valuation. The Shiller CAPE is at {cape_val:.0f} — historically elevated "
            "(long-run average is ~17).",
            "This doesn't predict a crash, but it does mean future returns have historically been "
            "lower when entering at these levels. Your own positions look balanced; the macro backdrop is the wild card.",
        ]
        if buf_val:
            body.append(f"Buffett Indicator is also running hot at {buf_val:.0f}%.")
        return _answer("Biggest risk: elevated market valuations.", body, tone='warn')

    # Everything looks reasonable
    return _answer(
        "No single dominant risk stands out.",
        [
            f"Top holding ({top}) is {top_pct:.0f}% of the portfolio — within reasonable limits.",
            "Keep an eye on concentration as positions grow, and watch upcoming earnings for your larger holdings.",
        ],
        tone='good',
    )


def scenario_what_if(port, _intel, _val):
    if not port or not port.get('positions'):
        return _not_ready("Positions haven't loaded yet.")

    positions = sorted(port['positions'],
                       key=lambda p: p.get('market_value', 0), reverse=True)
    top = positions[0]
    total_value = port['summary'].get('total_value', 0) or 1

    # Convert SPPE (EUR) vs USD positions using the saved EUR/USD rate.
    # portfolio-data positions store market_value in their native currency,
    # so we don't know for sure — but account.gross_position_value is in EUR.
    # We approximate "impact on portfolio" as share * 20%.
    share = top.get('allocation_pct', 0) / 100.0
    impact_pct = share * 20.0
    impact_eur = total_value * share * 0.20

    headline = f"If {top['ticker']} drops 20%, you'd lose about {_fmt_eur(impact_eur)}."
    body = [
        f"That's roughly {impact_pct:.1f}% of your total portfolio value.",
        "The rest of your holdings wouldn't automatically offset that — they move somewhat independently.",
    ]

    # If the top holding is a diversified ETF, soften the tone
    is_etf = False
    if _intel and _intel.get('sector_geo'):
        is_etf = _intel['sector_geo'].get(top['ticker'], {}).get('is_etf', False)
    if is_etf:
        body.append(
            f"{top['ticker']} is a broad ETF, so a 20% drop would typically only happen during a market-wide "
            "sell-off — not an idiosyncratic single-company event."
        )
    else:
        body.append(
            f"Because {top['ticker']} is a single stock, a 20% drop on news (earnings miss, guidance cut) "
            "is entirely possible — that's the concentration trade-off."
        )

    tone = 'warn' if impact_pct >= 10 else 'info'
    return _answer(headline, body, tone=tone)


def scenario_sector_geo(port, intel, _val):
    if not port or not port.get('positions'):
        return _not_ready("Positions haven't loaded yet.")
    if not intel or not intel.get('sector_geo'):
        return _not_ready("Market-intelligence data (sector / geography) is still loading.")

    # Aggregate by sector (using ETF weights when available) and by country
    sector_pct: dict[str, float] = {}
    country_pct: dict[str, float] = {}
    for p in port['positions']:
        sg = intel['sector_geo'].get(p['ticker'], {})
        w  = p.get('allocation_pct', 0)
        sw = sg.get('sector_weights') or {}
        if sw:
            for sec, frac in sw.items():
                sector_pct[sec] = sector_pct.get(sec, 0) + w * frac / 100.0
        elif sg.get('sector'):
            sector_pct[sg['sector']] = sector_pct.get(sg['sector'], 0) + w
        country = sg.get('country') or 'Unknown'
        country_pct[country] = country_pct.get(country, 0) + w

    top_sectors = sorted(sector_pct.items(), key=lambda kv: kv[1], reverse=True)[:3]
    top_countries = sorted(country_pct.items(), key=lambda kv: kv[1], reverse=True)[:3]

    sec_str = ", ".join(f"{name} {pct:.0f}%" for name, pct in top_sectors) or "—"
    geo_str = ", ".join(f"{name} {pct:.0f}%" for name, pct in top_countries) or "—"

    top_sec_pct = top_sectors[0][1] if top_sectors else 0
    top_geo_pct = top_countries[0][1] if top_countries else 0

    body = [
        f"Sector mix (top 3): {sec_str}.",
        f"Geography (top 3): {geo_str}.",
    ]
    if top_sec_pct >= 45:
        body.append(f"Heads-up: {top_sectors[0][0]} is ~{top_sec_pct:.0f}% of your portfolio — concentrated. "
                    "A common beginner ceiling is 30–35% per sector.")
        tone = 'warn'
    elif top_geo_pct >= 85:
        body.append(f"You're ~{top_geo_pct:.0f}% in one region. Adding a position outside it (developed Europe, "
                    "emerging markets, or a global ETF) is the simplest diversification move.")
        tone = 'warn'
    else:
        body.append("Balance looks reasonable for a beginner portfolio. No urgent rebalancing signal from sector or geography alone.")
        tone = 'good'

    return _answer("Sector & geography breakdown", body, tone=tone)


def scenario_vs_market(port, _intel, val):
    if not port or 'summary' not in port:
        return _not_ready("Positions haven't loaded yet.")

    your_pct = port['summary'].get('total_pnl_pct', 0)

    # Best effort: use S&P 500 ytd from CAPE/Buffett? We don't have ytd here.
    # Show your return and compare qualitatively to long-run equity average (~8%/yr).
    body = [
        f"Your portfolio is {_fmt_pct(your_pct)} on cost (unrealised).",
        "The S&P 500's long-run average is about 8–10% per year. "
        "If you bought most holdings within the past 12 months, comparing to that yardstick is a rough but useful sanity check.",
    ]
    # Flag if a single position is doing most of the work
    positions = port['positions']
    if positions:
        total_pnl = sum(p.get('unrealized_pnl', 0) for p in positions) or 1
        biggest   = max(positions, key=lambda p: abs(p.get('unrealized_pnl', 0)))
        share     = biggest.get('unrealized_pnl', 0) / total_pnl * 100
        if abs(share) >= 60:
            body.append(
                f"Most of your gains come from one name: {biggest['ticker']} "
                f"accounts for ~{share:.0f}% of total unrealised P&L. "
                "Without it, your portfolio would be tracking the market closely — worth knowing."
            )

    tone = 'good' if your_pct >= 8 else 'info'
    return _answer(
        ("Beating the long-run average." if your_pct >= 10
         else "Tracking roughly in line with long-run averages." if your_pct >= 5
         else "Below long-run averages — but this depends heavily on how long you've held."),
        body, tone=tone,
    )


# ── Registry ──────────────────────────────────────────────────────────────────
# Order matters: this is the order they appear in the dropdown.

SCENARIOS = [
    {'id': 'perf',          'label': 'Quick performance snapshot',       'fn': scenario_performance},
    {'id': 'biggest_risk',  'label': 'What is my biggest risk?',         'fn': scenario_biggest_risk},
    {'id': 'what_if',       'label': 'What if my biggest holding drops 20%?', 'fn': scenario_what_if},
    {'id': 'sector_geo',    'label': 'Am I balanced by sector & geography?',  'fn': scenario_sector_geo},
    {'id': 'vs_market',     'label': 'How am I doing vs the market?',    'fn': scenario_vs_market},
]

SCENARIOS_BY_ID = {s['id']: s for s in SCENARIOS}


def render_scenario(scenario_id: str, port, intel, val):
    """Dispatch: pick the scenario by id, call it, return Dash children."""
    sc = SCENARIOS_BY_ID.get(scenario_id)
    if not sc:
        return html.P("Unknown question.", style={'color': '#b45309'})
    try:
        return sc['fn'](port, intel, val)
    except Exception as e:
        return html.Div([
            html.P("Couldn't produce an answer for that question.",
                   style={'color': '#b45309', 'fontWeight': '600', 'margin': '0 0 6px'}),
            html.P(f"({type(e).__name__}: {e})",
                   style={'color': '#888', 'fontSize': '13px', 'margin': '0'}),
        ], style={'padding': '16px', 'background': '#fffbeb',
                  'border': '0.5px solid #fde68a', 'borderRadius': '10px'})
