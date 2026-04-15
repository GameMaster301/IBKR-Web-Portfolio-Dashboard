"""
AI portfolio analysis and chat — two modes:

1. Rule-based (default, no API key needed):
   Pure Python logic that answers instantly using live portfolio data.

2. Claude API (if ANTHROPIC_API_KEY is set):
   Calls claude-sonnet-4-6 for richer, natural-language responses and
   true multi-turn conversation with full portfolio context, including
   sector/geo exposure, upcoming earnings, and macro valuation indicators.
"""

import os
from datetime import datetime


# ── Shared helpers ────────────────────────────────────────────────────────────

def _portfolio_context_lines(positions: list, summary: dict, account: dict) -> str:
    """Build a compact text block describing the portfolio — used in all Claude prompts."""
    rate        = account.get('eurusd_rate', 1.08) or 1.08
    total_val   = summary.get('total_value', 0) or 0
    unreal_pnl  = summary.get('total_unrealized_pnl', 0) or 0
    pnl_pct     = summary.get('total_pnl_pct', 0) or 0
    daily_pnl   = account.get('daily_pnl', 0) or 0
    cash_eur    = account.get('cash_eur', 0) or 0
    largest     = summary.get('largest_position', '—')
    largest_pct = summary.get('largest_position_pct', 0) or 0
    best        = summary.get('best_performer', '—')
    worst       = summary.get('worst_performer', '—')

    holdings_lines = []
    for p in sorted(positions, key=lambda x: x.get('market_value', 0) or 0, reverse=True):
        daily_str = ''
        if p.get('daily_change_pct') is not None:
            daily_str = f" | Day: {p['daily_change_pct']:+.2f}%"
        holdings_lines.append(
            f"  {p['ticker']:6s}  {int(p.get('quantity', 0)):>6} shares  "
            f"price ${p.get('current_price', 0):>9,.2f}  "
            f"value ${p.get('market_value', 0):>10,.2f}  "
            f"weight {p.get('allocation_pct', 0):5.1f}%  "
            f"P&L {p.get('pnl_pct', 0):+6.2f}% (${p.get('unrealized_pnl', 0):+,.0f})"
            f"{daily_str}"
        )

    return (
        f"Total value:      ${total_val:>12,.2f}  (€{total_val / rate:,.2f})\n"
        f"Unrealized P&L:   ${unreal_pnl:>+12,.2f}  ({pnl_pct:+.2f}%)\n"
        f"Today's P&L:      ${daily_pnl:>+12,.2f}\n"
        f"Cash (EUR):       €{cash_eur:>12,.2f}\n"
        f"EUR/USD rate:      {rate:.4f}\n"
        f"Largest position: {largest} ({largest_pct:.1f}%)\n"
        f"Best performer:   {best}\n"
        f"Worst performer:  {worst}\n\n"
        f"HOLDINGS ({len(positions)} positions)\n"
        + "\n".join(holdings_lines)
    )


def _market_context_lines(market_data: dict | None, valuation_data: dict | None) -> str:
    """
    Format market intel and valuation indicators as a compact text block.
    Returns an empty string when no data is available.
    """
    if not market_data and not valuation_data:
        return ""

    lines = []

    if market_data:
        sg = market_data.get('sector_geo') or {}

        # Aggregate sector weights
        if sg:
            sector_weights: dict[str, float] = {}
            # sector_geo is {ticker: {sector, industry, country, longName}}
            # We don't have per-ticker weights here, so just list unique sectors
            sectors_seen = []
            for ticker_info in sg.values():
                s = ticker_info.get('sector', 'Unknown')
                if s and s not in sectors_seen:
                    sectors_seen.append(s)
            if sectors_seen:
                lines.append(f"SECTORS HELD: {', '.join(sectors_seen[:8])}")

            # Geographic breakdown
            geos_seen = []
            for ticker_info in sg.values():
                c = ticker_info.get('country', 'Unknown')
                if c and c not in geos_seen:
                    geos_seen.append(c)
            if geos_seen:
                lines.append(f"GEOGRAPHIES: {', '.join(geos_seen[:6])}")

        # Upcoming earnings
        earnings = market_data.get('earnings') or {}
        if earnings:
            upcoming = []
            today = datetime.now().date()
            for ticker, info in earnings.items():
                date_str = info.get('next_date')
                if date_str:
                    try:
                        ed = datetime.strptime(str(date_str)[:10], '%Y-%m-%d').date()
                        days_away = (ed - today).days
                        if 0 <= days_away <= 45:
                            avg_move = info.get('avg_1d_move')
                            move_str = f", avg ±{abs(avg_move):.1f}% post-earnings" if avg_move else ""
                            upcoming.append((ticker, str(date_str)[:10], days_away, move_str))
                    except Exception:
                        pass
            if upcoming:
                upcoming.sort(key=lambda x: x[2])
                lines.append(
                    "UPCOMING EARNINGS (next 45 days): "
                    + "; ".join(
                        f"{t} on {d} ({n}d away{mv})" for t, d, n, mv in upcoming
                    )
                )

    if valuation_data:
        val_parts = []

        b = valuation_data.get('buffett') or {}
        if b and b.get('value'):
            zone = _buffett_zone_label(b['value'])
            val_parts.append(f"Buffett Indicator {b['value']:.0f}% ({zone})")

        pe = valuation_data.get('sp500_pe') or {}
        if pe:
            t = pe.get('trailing_pe')
            f_ = pe.get('forward_pe')
            if t:
                val_parts.append(f"S&P 500 trailing P/E {t:.1f}×")
            if f_:
                val_parts.append(f"forward P/E {f_:.1f}×")

        cape = valuation_data.get('cape') or {}
        if cape and cape.get('current'):
            zone = _cape_zone_label(cape['current'])
            val_parts.append(f"Shiller CAPE {cape['current']:.1f} ({zone})")

        if val_parts:
            lines.append("MARKET VALUATION: " + " | ".join(val_parts))

    return "\n".join(lines) if lines else ""


def _buffett_zone_label(value: float) -> str:
    if value < 80:  return 'undervalued'
    if value < 100: return 'fair value'
    if value < 150: return 'moderately overvalued'
    return 'significantly overvalued'


def _cape_zone_label(value: float) -> str:
    if value < 15:  return 'undervalued'
    if value < 25:  return 'fair value'
    if value < 35:  return 'elevated'
    return 'historically extreme'


# ── Rule-based portfolio analysis ─────────────────────────────────────────────

def _rule_based_analysis(positions: list, summary: dict, account: dict) -> str:
    """Produce data-driven portfolio insights with zero external dependencies."""
    lines = []

    total_val   = summary.get('total_value', 0) or 0
    unreal_pnl  = summary.get('total_unrealized_pnl', 0) or 0
    pnl_pct     = summary.get('total_pnl_pct', 0) or 0
    daily_pnl   = account.get('daily_pnl', 0) or 0
    cash_eur    = account.get('cash_eur', 0) or 0
    rate        = account.get('eurusd_rate', 1.08) or 1.08

    if not positions:
        return "No positions found in the portfolio."

    sorted_pos = sorted(positions, key=lambda x: x.get('market_value', 0) or 0, reverse=True)
    top        = sorted_pos[0]
    top_pct    = top.get('allocation_pct', 0) or 0
    top_ticker = top.get('ticker', '?')
    winners    = [p for p in positions if (p.get('pnl_pct') or 0) > 0]
    best       = max(positions, key=lambda x: x.get('pnl_pct', 0) or 0)
    worst      = min(positions, key=lambda x: x.get('pnl_pct', 0) or 0)
    win_rate   = len(winners) / len(positions) * 100

    # 1. Concentration
    if top_pct >= 30:
        lines.append(
            f"1. Concentration risk is HIGH: {top_ticker} makes up {top_pct:.1f}% of the portfolio. "
            f"A single adverse move in this position would have an outsized impact on total value."
        )
    elif top_pct >= 20:
        lines.append(
            f"1. Moderate concentration: {top_ticker} is your largest position at {top_pct:.1f}%. "
            f"Consider whether this reflects a deliberate conviction bet or unintended drift."
        )
    else:
        top3_pct = sum(p.get('allocation_pct', 0) or 0 for p in sorted_pos[:3])
        lines.append(
            f"1. Concentration is well-spread: the largest position ({top_ticker}) is {top_pct:.1f}%. "
            f"Your top 3 holdings account for {top3_pct:.1f}% of the portfolio."
        )

    # 2. P&L health
    pnl_sign   = "+" if unreal_pnl >= 0 else ""
    daily_sign = "+" if daily_pnl >= 0 else ""
    lines.append(
        f"2. P&L health: overall unrealized P&L is {pnl_sign}${unreal_pnl:,.0f} ({pnl_sign}{pnl_pct:.1f}%), "
        f"today {daily_sign}${daily_pnl:,.0f}. "
        f"{len(winners)}/{len(positions)} positions are in the green ({win_rate:.0f}% win rate). "
        f"Best performer: {best.get('ticker')} ({best.get('pnl_pct', 0):+.1f}%); "
        f"biggest drag: {worst.get('ticker')} ({worst.get('pnl_pct', 0):+.1f}%)."
    )

    # 3. Cash
    if total_val > 0:
        cash_usd = cash_eur * rate
        cash_pct = cash_usd / (total_val + cash_usd) * 100
        if cash_pct > 15:
            lines.append(
                f"3. Cash drag: you're holding €{cash_eur:,.0f} (~{cash_pct:.1f}% of assets). "
                f"High cash allocation limits upside — consider deploying into existing positions."
            )
        elif cash_pct > 5:
            lines.append(
                f"3. Cash position (€{cash_eur:,.0f}, ~{cash_pct:.1f}%) provides a reasonable buffer "
                f"for opportunistic buys without creating significant drag."
            )
        else:
            lines.append(
                f"3. Portfolio is nearly fully invested (cash €{cash_eur:,.0f}, ~{cash_pct:.1f}%). "
                f"Limited dry powder — any rebalancing will require selling before buying."
            )

    # 4. Actionable suggestion
    worst_pct = worst.get('pnl_pct', 0) or 0
    worst_val = worst.get('unrealized_pnl', 0) or 0
    if worst_pct < -15:
        suggestion = (
            f"Review {worst.get('ticker')}: down {worst_pct:.1f}% (${worst_val:+,.0f}). "
            f"Decide whether the original thesis still holds or if a stop-loss is warranted."
        )
    elif top_pct >= 25 and (top.get('pnl_pct', 0) or 0) > 20:
        suggestion = (
            f"Consider trimming {top_ticker}: it has grown to {top_pct:.1f}% of the portfolio "
            f"with a {top.get('pnl_pct', 0):+.1f}% gain. A partial sale would lock in profits "
            f"and reduce concentration without exiting the position."
        )
    elif win_rate < 40:
        suggestion = (
            f"With only {win_rate:.0f}% of positions profitable, review whether the losers "
            f"share a common theme (sector, geography) that could be addressed together."
        )
    else:
        suggestion = (
            f"Portfolio looks balanced. Next review trigger: {top_ticker} past 30% allocation "
            f"or overall drawdown exceeding 10% from current levels."
        )
    lines.append(f"4. Suggestion: {suggestion}")

    return "\n\n".join(lines)


# ── Rule-based chat ───────────────────────────────────────────────────────────

def _rule_based_chat(question: str, positions: list, summary: dict, account: dict,
                     market_data: dict | None = None) -> str:
    """Answer common portfolio questions without an API key."""
    q = question.lower().strip()

    if not positions:
        return "No portfolio data available yet — connect to TWS and wait for the first refresh."

    sorted_pos = sorted(positions, key=lambda x: x.get('market_value', 0) or 0, reverse=True)
    total_val  = summary.get('total_value', 0) or 0
    unreal_pnl = summary.get('total_unrealized_pnl', 0) or 0
    pnl_pct    = summary.get('total_pnl_pct', 0) or 0
    daily_pnl  = account.get('daily_pnl', 0) or 0
    cash_eur   = account.get('cash_eur', 0) or 0
    rate       = account.get('eurusd_rate', 1.08) or 1.08
    best       = max(positions, key=lambda x: x.get('pnl_pct', 0) or 0)
    worst      = min(positions, key=lambda x: x.get('pnl_pct', 0) or 0)
    winners    = [p for p in positions if (p.get('pnl_pct') or 0) > 0]

    # Largest position
    if any(w in q for w in ['largest', 'biggest', 'top position', 'top holding', 'heaviest']):
        top = sorted_pos[0]
        return (
            f"Your largest position is **{top['ticker']}** — "
            f"${top.get('market_value', 0):,.0f} ({top.get('allocation_pct', 0):.1f}% of portfolio), "
            f"P&L {top.get('pnl_pct', 0):+.1f}%."
        )

    # Best performer
    if any(w in q for w in ['best', 'top performer', 'biggest gain', 'most profit', 'winning']):
        return (
            f"Your best performer is **{best['ticker']}** with a "
            f"{best.get('pnl_pct', 0):+.1f}% gain (${best.get('unrealized_pnl', 0):+,.0f} unrealized)."
        )

    # Worst performer
    if any(w in q for w in ['worst', 'biggest loss', 'drag', 'underperform', 'losing']):
        return (
            f"Your worst performer is **{worst['ticker']}** with a "
            f"{worst.get('pnl_pct', 0):+.1f}% return (${worst.get('unrealized_pnl', 0):+,.0f} unrealized)."
        )

    # Cash
    if any(w in q for w in ['cash', 'liquidity', 'dry powder', 'uninvested']):
        cash_usd = cash_eur * rate
        cash_pct = cash_usd / (total_val + cash_usd) * 100 if total_val > 0 else 0
        return (
            f"You have **€{cash_eur:,.0f}** in cash (~{cash_pct:.1f}% of total assets, "
            f"≈${cash_usd:,.0f} at current EUR/USD {rate:.4f})."
        )

    # Total value / portfolio worth
    if any(w in q for w in ['total', 'worth', 'value', 'how much is', 'portfolio size']):
        return (
            f"Your portfolio is worth **${total_val:,.0f}** (€{total_val / rate:,.0f} at {rate:.4f})."
        )

    # P&L / returns
    if any(w in q for w in ["p&l", "pnl", "profit", "loss", "return", "gain", "performance"]):
        sign = "+" if unreal_pnl >= 0 else ""
        dsign = "+" if daily_pnl >= 0 else ""
        return (
            f"Overall unrealized P&L: **{sign}${unreal_pnl:,.0f}** ({sign}{pnl_pct:.1f}%). "
            f"Today: **{dsign}${daily_pnl:,.0f}**."
        )

    # Today / daily
    if any(w in q for w in ["today", "daily", "day", "this morning"]):
        dsign = "+" if daily_pnl >= 0 else ""
        return f"Today's P&L is **{dsign}${daily_pnl:,.0f}**."

    # Number of positions
    if any(w in q for w in ['how many', 'number of', 'count', 'positions', 'holdings']):
        return (
            f"You have **{len(positions)} positions**. "
            f"{len(winners)} are profitable ({len(winners)/len(positions)*100:.0f}% win rate)."
        )

    # Concentration / risk
    if any(w in q for w in ['concentrat', 'risk', 'exposure', 'diversif']):
        top = sorted_pos[0]
        top_pct = top.get('allocation_pct', 0) or 0
        top3_pct = sum(p.get('allocation_pct', 0) or 0 for p in sorted_pos[:3])
        level = "HIGH" if top_pct >= 30 else "moderate" if top_pct >= 20 else "low"
        return (
            f"Concentration is **{level}**. Largest position: {top['ticker']} at {top_pct:.1f}%. "
            f"Top 3 holdings account for {top3_pct:.1f}% of the portfolio."
        )

    # Rebalance
    if any(w in q for w in ['rebalanc', 'realloc', 'adjust', 'trim', 'reduce']):
        top = sorted_pos[0]
        top_pct = top.get('allocation_pct', 0) or 0
        if top_pct > 25:
            return (
                f"**{top['ticker']}** at {top_pct:.1f}% is the most obvious candidate for trimming. "
                f"Reducing it to ~20% would free up capital to deploy elsewhere. "
                f"Set ANTHROPIC_API_KEY for a full rebalancing plan."
            )
        return (
            "Portfolio weights look reasonable — no single position is heavily overweight. "
            "Set ANTHROPIC_API_KEY for a detailed rebalancing analysis."
        )

    # Sector / industry exposure
    if any(w in q for w in ['sector', 'industry', 'sector break']):
        if market_data and market_data.get('sector_geo'):
            sg = market_data['sector_geo']
            sector_tickers: dict[str, list] = {}
            for ticker_info_key, ticker_info in sg.items():
                s = ticker_info.get('sector', 'Unknown')
                sector_tickers.setdefault(s, []).append(ticker_info_key)
            lines = [f"**{s}**: {', '.join(tks)}" for s, tks in sorted(sector_tickers.items())]
            return "Sector breakdown:\n" + "\n".join(lines)
        return (
            "Sector data is loading — check the Sector & Geography chart below, "
            "or set ANTHROPIC_API_KEY for a detailed sector analysis."
        )

    # Geography
    if any(w in q for w in ['geo', 'country', 'region', 'international', 'global', 'us market', 'europe']):
        if market_data and market_data.get('sector_geo'):
            sg = market_data['sector_geo']
            country_tickers: dict[str, list] = {}
            for ticker_info_key, ticker_info in sg.items():
                c = ticker_info.get('country', 'Unknown')
                country_tickers.setdefault(c, []).append(ticker_info_key)
            lines = [f"**{c}**: {', '.join(tks)}" for c, tks in sorted(country_tickers.items())]
            return "Geographic breakdown:\n" + "\n".join(lines)
        return "Geographic data is loading — check the Sector & Geography chart below."

    # Earnings
    if any(w in q for w in ['earning', 'report', 'earnings date', 'earnings soon', 'upcoming']):
        if market_data and market_data.get('earnings'):
            earnings = market_data['earnings']
            today = datetime.now().date()
            upcoming = []
            for ticker, info in earnings.items():
                date_str = info.get('next_date')
                if date_str:
                    try:
                        ed = datetime.strptime(str(date_str)[:10], '%Y-%m-%d').date()
                        days_away = (ed - today).days
                        if 0 <= days_away <= 90:
                            avg_move = info.get('avg_1d_move')
                            upcoming.append((ticker, str(date_str)[:10], days_away, avg_move))
                    except Exception:
                        pass
            if upcoming:
                upcoming.sort(key=lambda x: x[2])
                lines = []
                for ticker, d, n, avg in upcoming:
                    move_str = f" (avg ±{abs(avg):.1f}% post-earnings)" if avg else ""
                    lines.append(f"**{ticker}** reports {d} — {n} days away{move_str}")
                return "Upcoming earnings in your portfolio:\n" + "\n".join(lines)
            return "No earnings reports found within the next 90 days for your holdings."
        return "Earnings data is loading — check the Earnings Calendar section below."

    # Default
    return (
        "I can answer questions like: *What's my largest position?*, *What's my best performer?*, "
        "*How much cash do I have?*, *What's today's P&L?*, *Should I rebalance?*, "
        "*What sectors do I hold?*, *Any earnings soon?*. "
        "Set **ANTHROPIC_API_KEY** to unlock free-form AI conversation."
    )


# ── Claude API analyser ───────────────────────────────────────────────────────

def _claude_analysis(positions: list, summary: dict, account: dict,
                     market_data: dict | None = None,
                     valuation_data: dict | None = None) -> str:
    """Call the Anthropic Claude API for a full portfolio analysis."""
    try:
        import anthropic
    except ImportError:
        return _rule_based_analysis(positions, summary, account)

    portfolio_ctx = _portfolio_context_lines(positions, summary, account)
    market_ctx    = _market_context_lines(market_data, valuation_data)

    context_block = portfolio_ctx
    if market_ctx:
        context_block += f"\n\nMARKET CONTEXT\n{market_ctx}"

    prompt = (
        f"PORTFOLIO SNAPSHOT\n{context_block}\n\n"
        "Give 3-4 concise, specific observations covering:\n"
        "1. Concentration risk (any single position or sector dominating)\n"
        "2. P&L health (what's working, what's dragging)\n"
        "3. Market context — if valuation data is provided, note whether the broader "
        "market environment supports holding/adding vs. being defensive\n"
        "4. One actionable suggestion based on the data above\n\n"
        "Be direct and data-driven. No generic disclaimers. Use exact numbers."
    )

    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
    try:
        response = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=700,
            system="You are a concise portfolio analyst reviewing a real IBKR brokerage account.",
            messages=[{'role': 'user', 'content': prompt}],
        )
        return response.content[0].text
    except anthropic.AuthenticationError:
        return _rule_based_analysis(positions, summary, account)
    except anthropic.RateLimitError:
        return "Rate limit reached — please wait a moment and try again."
    except anthropic.BadRequestError as e:
        return f"Bad request: {e.message}"
    except anthropic.APIStatusError as e:
        return f"API error {e.status_code}: {e.message}"
    except anthropic.APIConnectionError:
        return _rule_based_analysis(positions, summary, account)
    except Exception as e:
        return f"Unexpected error: {e}"


# ── Claude API chat ───────────────────────────────────────────────────────────

def _claude_chat(question: str, history: list, positions: list, summary: dict, account: dict,
                 market_data: dict | None = None,
                 valuation_data: dict | None = None) -> str:
    """
    Multi-turn portfolio Q&A using Claude.
    history is a list of {'role': 'user'|'assistant', 'content': str} dicts.
    """
    try:
        import anthropic
    except ImportError:
        return _rule_based_chat(question, positions, summary, account, market_data)

    portfolio_ctx = _portfolio_context_lines(positions, summary, account)
    market_ctx    = _market_context_lines(market_data, valuation_data)

    context_block = portfolio_ctx
    if market_ctx:
        context_block += f"\n\nMARKET CONTEXT\n{market_ctx}"

    system = (
        "You are a concise, data-driven portfolio analyst. "
        "Answer questions about the following IBKR portfolio. "
        "Be specific, use the exact numbers provided, and keep answers under 4 sentences.\n\n"
        f"PORTFOLIO\n{context_block}"
    )

    messages = list(history) + [{'role': 'user', 'content': question}]

    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
    try:
        response = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=450,
            system=system,
            messages=messages,
        )
        return response.content[0].text
    except anthropic.AuthenticationError:
        return _rule_based_chat(question, positions, summary, account, market_data)
    except anthropic.RateLimitError:
        return "Rate limit reached — please wait a moment and try again."
    except Exception as e:
        return f"Error: {e}"


# ── Per-position analysis ─────────────────────────────────────────────────────

def _rule_based_position_analysis(ticker: str, position: dict, positions: list) -> str:
    """Quick rule-based analysis for a single position."""
    price     = position.get('current_price', 0) or 0
    qty       = position.get('quantity', 0) or 0
    avg_cost  = position.get('average_cost', 0) or 0
    pnl_pct   = position.get('pnl_pct', 0) or 0
    alloc_pct = position.get('allocation_pct', 0) or 0
    low_52w   = position.get('low_52w')
    high_52w  = position.get('high_52w')

    lines = []

    # P&L status
    unrealized = (price - avg_cost) * qty if avg_cost else 0
    if pnl_pct > 20:
        lines.append(
            f"{ticker} is up {pnl_pct:+.1f}% from your average cost of ${avg_cost:,.2f}, "
            f"representing ${unrealized:+,.0f} in unrealized gains."
        )
    elif pnl_pct < -10:
        lines.append(
            f"{ticker} is down {pnl_pct:+.1f}% from your average cost of ${avg_cost:,.2f}, "
            f"a current loss of ${unrealized:+,.0f}."
        )
    else:
        lines.append(
            f"{ticker} is {pnl_pct:+.1f}% vs your avg cost of ${avg_cost:,.2f} "
            f"(${unrealized:+,.0f} unrealized)."
        )

    # Weight / concentration
    if alloc_pct > 25:
        lines.append(
            f"At {alloc_pct:.1f}% of the portfolio this is a high-concentration position — "
            f"adverse moves here will have an outsized impact on total value."
        )
    elif alloc_pct < 3:
        lines.append(
            f"At {alloc_pct:.1f}% this is a small position; its impact on overall "
            f"performance is limited in either direction."
        )
    else:
        lines.append(f"Weight: {alloc_pct:.1f}% of the portfolio.")

    # 52-week range
    if low_52w and high_52w and high_52w > low_52w:
        range_pct = (price - low_52w) / (high_52w - low_52w) * 100
        if range_pct > 85:
            lines.append(
                f"Trading near its 52-week high — top {100 - range_pct:.0f}% of the "
                f"${low_52w:,.2f}–${high_52w:,.2f} range."
            )
        elif range_pct < 15:
            lines.append(
                f"Trading near its 52-week low — bottom {range_pct:.0f}% of the "
                f"${low_52w:,.2f}–${high_52w:,.2f} range."
            )
        else:
            lines.append(
                f"52-week range: ${low_52w:,.2f}–${high_52w:,.2f} "
                f"(currently at {range_pct:.0f}% of range)."
            )

    lines.append("Set ANTHROPIC_API_KEY to get a deeper AI-driven thesis review for this position.")
    return "\n\n".join(lines)


def _claude_position_analysis(ticker: str, position: dict, positions: list,
                               summary: dict, account: dict,
                               market_data: dict | None = None) -> str:
    """Claude-powered deep dive for a single position."""
    try:
        import anthropic
    except ImportError:
        return _rule_based_position_analysis(ticker, position, positions)

    portfolio_ctx = _portfolio_context_lines(positions, summary, account)

    pos_detail = (
        f"Ticker:        {ticker}\n"
        f"Quantity:      {int(position.get('quantity', 0))} shares\n"
        f"Avg cost:      ${position.get('average_cost', 0):,.2f}\n"
        f"Current price: ${position.get('current_price', 0):,.2f}\n"
        f"Market value:  ${position.get('market_value', 0):,.2f}\n"
        f"P&L:           {position.get('pnl_pct', 0):+.2f}% "
        f"(${position.get('unrealized_pnl', 0):+,.0f})\n"
        f"Weight:        {position.get('allocation_pct', 0):.1f}% of portfolio\n"
        f"Daily change:  {position.get('daily_change_pct') or 0:+.2f}%\n"
    )
    if position.get('low_52w') and position.get('high_52w'):
        low, high = position['low_52w'], position['high_52w']
        price = position.get('current_price', 0) or 0
        rng_pct = (price - low) / (high - low) * 100 if high > low else 0
        pos_detail += (
            f"52w range:     ${low:,.2f} – ${high:,.2f} "
            f"(at {rng_pct:.0f}% of range)\n"
        )

    # Add earnings context if available
    earnings_note = ""
    if market_data and market_data.get('earnings') and ticker in market_data['earnings']:
        ei = market_data['earnings'][ticker]
        next_date = ei.get('next_date')
        avg_move  = ei.get('avg_1d_move')
        if next_date:
            today = datetime.now().date()
            try:
                ed = datetime.strptime(str(next_date)[:10], '%Y-%m-%d').date()
                days_away = (ed - today).days
                earnings_note = f"\nNext earnings: {next_date} ({days_away} days away)"
                if avg_move:
                    earnings_note += f", historical avg ±{abs(avg_move):.1f}% 1-day move"
            except Exception:
                pass

    prompt = (
        f"POSITION DETAIL\n{pos_detail}{earnings_note}\n\n"
        f"FULL PORTFOLIO CONTEXT\n{portfolio_ctx}\n\n"
        f"Give 3 specific observations about this {ticker} position:\n"
        f"1. How it fits in the portfolio (weight, relative performance vs other positions)\n"
        f"2. P&L health — is it near the cost basis, a 52-week extreme, or a meaningful level?\n"
        f"3. One concrete action to consider (hold, trim, add, set a stop-loss) with a specific "
        f"reason tied to the numbers above\n\n"
        f"Be direct. No generic disclaimers. Use exact numbers from the data."
    )

    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
    try:
        response = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=450,
            system="You are a concise portfolio analyst reviewing a specific IBKR position.",
            messages=[{'role': 'user', 'content': prompt}],
        )
        return response.content[0].text
    except anthropic.AuthenticationError:
        return _rule_based_position_analysis(ticker, position, positions)
    except anthropic.RateLimitError:
        return "Rate limit reached — please wait a moment and try again."
    except Exception as e:
        return f"Error: {e}"


# ── Public entry points ───────────────────────────────────────────────────────

def analyse_portfolio(positions: list, summary: dict, account: dict,
                      market_data: dict | None = None,
                      valuation_data: dict | None = None) -> str:
    """
    One-shot portfolio analysis.
    Uses Claude when ANTHROPIC_API_KEY is set; rule-based otherwise.
    Optionally accepts market intel (sector/earnings) and valuation data
    to enrich the Claude prompt with broader market context.
    """
    if os.environ.get('ANTHROPIC_API_KEY'):
        return _claude_analysis(positions, summary, account, market_data, valuation_data)
    return _rule_based_analysis(positions, summary, account)


def chat_portfolio(question: str, history: list, positions: list, summary: dict, account: dict,
                   market_data: dict | None = None,
                   valuation_data: dict | None = None) -> str:
    """
    Answer a portfolio question, optionally using prior conversation history.
    Uses Claude when ANTHROPIC_API_KEY is set; keyword-based fallback otherwise.
    market_data and valuation_data are passed through to enrich the Claude system prompt.
    """
    if os.environ.get('ANTHROPIC_API_KEY'):
        return _claude_chat(question, history, positions, summary, account,
                            market_data, valuation_data)
    return _rule_based_chat(question, positions, summary, account, market_data)


def analyse_position(ticker: str, position: dict, positions: list,
                     summary: dict, account: dict,
                     market_data: dict | None = None) -> str:
    """
    Per-position AI analysis — deep dive on a single holding.
    Uses Claude when ANTHROPIC_API_KEY is set; rule-based otherwise.
    """
    if os.environ.get('ANTHROPIC_API_KEY'):
        return _claude_position_analysis(ticker, position, positions, summary, account,
                                         market_data)
    return _rule_based_position_analysis(ticker, position, positions)
