"""
AI portfolio analysis and chat — two modes:

1. Rule-based (default, no API key needed):
   Pure Python logic that answers instantly using live portfolio data.

2. Claude API (if ANTHROPIC_API_KEY is set):
   Calls claude-sonnet-4-6 for richer, natural-language responses and
   true multi-turn conversation with full portfolio context.
"""

import os


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

def _rule_based_chat(question: str, positions: list, summary: dict, account: dict) -> str:
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

    # Default
    return (
        "I can answer questions like: *What's my largest position?*, *What's my best performer?*, "
        "*How much cash do I have?*, *What's today's P&L?*, *Should I rebalance?*. "
        "Set **ANTHROPIC_API_KEY** to unlock free-form AI conversation."
    )


# ── Claude API analyser ───────────────────────────────────────────────────────

def _claude_analysis(positions: list, summary: dict, account: dict) -> str:
    """Call the Anthropic Claude API for a full portfolio analysis."""
    try:
        import anthropic
    except ImportError:
        return _rule_based_analysis(positions, summary, account)

    context = _portfolio_context_lines(positions, summary, account)
    prompt = (
        f"PORTFOLIO SNAPSHOT\n{context}\n\n"
        "Give 3-4 concise, specific observations covering:\n"
        "1. Concentration risk (any single position or sector dominating)\n"
        "2. P&L health (what's working, what's dragging)\n"
        "3. One actionable suggestion based on the data above\n\n"
        "Be direct and data-driven. No generic disclaimers."
    )

    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
    try:
        response = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=600,
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

def _claude_chat(question: str, history: list, positions: list, summary: dict, account: dict) -> str:
    """
    Multi-turn portfolio Q&A using Claude.
    history is a list of {'role': 'user'|'assistant', 'content': str} dicts.
    """
    try:
        import anthropic
    except ImportError:
        return _rule_based_chat(question, positions, summary, account)

    context = _portfolio_context_lines(positions, summary, account)
    system = (
        "You are a concise, data-driven portfolio analyst. "
        "Answer questions about the following IBKR portfolio. "
        "Be specific, use the numbers, and keep answers under 3 sentences.\n\n"
        f"PORTFOLIO\n{context}"
    )

    messages = list(history) + [{'role': 'user', 'content': question}]

    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
    try:
        response = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=400,
            system=system,
            messages=messages,
        )
        return response.content[0].text
    except anthropic.AuthenticationError:
        return _rule_based_chat(question, positions, summary, account)
    except anthropic.RateLimitError:
        return "Rate limit reached — please wait a moment and try again."
    except Exception as e:
        return f"Error: {e}"


# ── Public entry points ───────────────────────────────────────────────────────

def analyse_portfolio(positions: list, summary: dict, account: dict) -> str:
    """
    One-shot portfolio analysis.
    Uses Claude when ANTHROPIC_API_KEY is set; rule-based otherwise.
    """
    if os.environ.get('ANTHROPIC_API_KEY'):
        return _claude_analysis(positions, summary, account)
    return _rule_based_analysis(positions, summary, account)


def chat_portfolio(question: str, history: list, positions: list, summary: dict, account: dict) -> str:
    """
    Answer a portfolio question, optionally using prior conversation history.
    Uses Claude when ANTHROPIC_API_KEY is set; keyword-based fallback otherwise.
    """
    if os.environ.get('ANTHROPIC_API_KEY'):
        return _claude_chat(question, history, positions, summary, account)
    return _rule_based_chat(question, positions, summary, account)
