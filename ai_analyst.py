"""
AI portfolio analysis using the Anthropic Claude API.
Reads ANTHROPIC_API_KEY from the environment — never hardcode it.
"""

import os
import anthropic


def analyse_portfolio(positions: list, summary: dict, account: dict) -> str:
    """
    Build a structured prompt from live portfolio data and call the Claude API.

    Args:
        positions: list of position dicts from the portfolio-data Store
        summary:   summary dict (total_value, pnl, best/worst performer, etc.)
        account:   account dict (cash, EUR/USD rate, daily P&L, etc.)

    Returns:
        Analysis text string, or a user-friendly error message on failure.
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return (
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Add it to your shell profile or launch environment and restart the dashboard."
        )

    rate         = account.get('eurusd_rate', 1.08)
    total_val    = summary.get('total_value', 0) or 0
    unreal_pnl   = summary.get('total_unrealized_pnl', 0) or 0
    pnl_pct      = summary.get('total_pnl_pct', 0) or 0
    daily_pnl    = account.get('daily_pnl', 0) or 0
    cash_eur     = account.get('cash_eur', 0) or 0
    best         = summary.get('best_performer', '—')
    worst        = summary.get('worst_performer', '—')
    largest      = summary.get('largest_position', '—')
    largest_pct  = summary.get('largest_position_pct', 0) or 0

    # Build per-position lines, sorted by market value descending
    holdings_lines = []
    for p in sorted(positions, key=lambda x: x.get('market_value', 0), reverse=True):
        daily_str = ''
        if p.get('daily_change_pct') is not None:
            daily_str = f" | Day: {p['daily_change_pct']:+.2f}%"
        holdings_lines.append(
            f"  {p['ticker']:6s}  {int(p['quantity']):>6} shares  "
            f"price ${p['current_price']:>9,.2f}  "
            f"value ${p['market_value']:>10,.2f}  "
            f"weight {p.get('allocation_pct', 0):5.1f}%  "
            f"P&L {p.get('pnl_pct', 0):+6.2f}% (${p.get('unrealized_pnl', 0):+,.0f})"
            f"{daily_str}"
        )

    prompt = f"""You are a concise portfolio analyst reviewing a real IBKR brokerage account.

PORTFOLIO SNAPSHOT
  Total value:      ${total_val:>12,.2f}  (€{total_val / rate:,.2f})
  Unrealized P&L:   ${unreal_pnl:>+12,.2f}  ({pnl_pct:+.2f}%)
  Today's P&L:      ${daily_pnl:>+12,.2f}
  Cash (EUR):       €{cash_eur:>12,.2f}
  EUR/USD rate:      {rate:.4f}
  Largest position: {largest} ({largest_pct:.1f}%)
  Best performer:   {best}
  Worst performer:  {worst}

HOLDINGS ({len(positions)} positions)
{chr(10).join(holdings_lines)}

Give 3-4 concise, specific observations covering:
1. Concentration risk (any single position or sector dominating)
2. P&L health (what's working, what's dragging)
3. One actionable suggestion based on the data above

Be direct and data-driven. No generic disclaimers."""

    client = anthropic.Anthropic(api_key=api_key)

    try:
        response = client.messages.create(
            model='claude-sonnet-4-20250514',
            max_tokens=600,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return response.content[0].text

    except anthropic.AuthenticationError:
        return (
            "Authentication failed — your ANTHROPIC_API_KEY appears to be invalid. "
            "Check the key at console.anthropic.com and update your environment."
        )
    except anthropic.RateLimitError:
        return "Rate limit reached — please wait a moment and try again."
    except anthropic.BadRequestError as e:
        return f"Bad request: {e.message}"
    except anthropic.APIStatusError as e:
        return f"API error {e.status_code}: {e.message}"
    except anthropic.APIConnectionError:
        return "Network error — could not reach the Anthropic API. Check your internet connection."
    except Exception as e:
        return f"Unexpected error: {e}"
