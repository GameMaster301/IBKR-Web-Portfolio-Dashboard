"""
Pluggable LLM provider for the Portfolio Coach — optional path, only used
when the user pastes an API key. Provider is auto-detected from the key
prefix so the user doesn't have to pick one:

    sk-ant-…   → Anthropic (Claude)
    xai-…      → xAI (Grok)
    sk-…       → OpenAI (GPT)

Everything is plain HTTP via `requests` (already transitively available).
No provider SDK is imported.

The `ask()` function is synchronous and returns a single string. It is meant
to be called from a Dash callback; callers should wrap it in a try/except
and display a friendly error if the provider responds with anything other
than a 200 with a parseable body.
"""

from __future__ import annotations

import json
import re

import requests

SYSTEM_PROMPT = (
    "You are a friendly coach for a first-time investor. "
    "Write plain English. If you use any jargon (P/E, CAPE, beta, yield, hedge, "
    "drawdown, allocation), define it in one short sentence the first time it "
    "appears. Keep answers under 250 words unless the user explicitly asks for "
    "a long answer. Base every claim on the portfolio data the user provides — "
    "never invent positions, prices, or numbers. If something isn't in the data, "
    "say you don't know rather than guess.\n\n"
    "After your answer, on a NEW final line write EXACTLY one JSON array with "
    "three short follow-up questions the user might ask next (max 8 words each, "
    "specific to this portfolio), prefixed with the literal token FOLLOWUPS: — "
    "for example:\n"
    'FOLLOWUPS: ["Why is NVDA so volatile?", "Should I trim my biggest holding?", "What happens if rates rise?"]\n'
    "Do not write anything after that line."
)


_FOLLOWUPS_RE = re.compile(r'\n?\s*FOLLOWUPS:\s*(\[[^\n]*\])\s*$', re.MULTILINE)


def _strip_followups(text: str) -> tuple[str, list[str]]:
    """Pull the trailing FOLLOWUPS JSON array out of an LLM reply."""
    if not text:
        return text, []
    m = _FOLLOWUPS_RE.search(text)
    if not m:
        return text.strip(), []
    try:
        fups = json.loads(m.group(1))
        if not isinstance(fups, list):
            fups = []
        fups = [str(f).strip() for f in fups if str(f).strip()][:3]
    except Exception:
        fups = []
    return text[:m.start()].rstrip(), fups


# ── Provider detection ────────────────────────────────────────────────────────

def detect_provider(api_key: str) -> str | None:
    if not api_key:
        return None
    k = api_key.strip()
    if k.startswith('sk-ant-'):
        return 'anthropic'
    if k.startswith('xai-'):
        return 'xai'
    if k.startswith('sk-'):
        return 'openai'
    return None


def provider_label(name: str) -> str:
    return {'anthropic': 'Anthropic Claude',
            'xai':       'xAI Grok',
            'openai':    'OpenAI GPT'}.get(name, 'Unknown')


# ── Key validation ────────────────────────────────────────────────────────────

_MODELS_URLS = {
    'anthropic': ('GET', 'https://api.anthropic.com/v1/models'),
    'openai':    ('GET', 'https://api.openai.com/v1/models'),
    'xai':       ('GET', 'https://api.x.ai/v1/models'),
}


def validate_key(api_key: str) -> tuple[bool, str]:
    """
    Ping the provider's models endpoint to confirm the key is accepted.
    Returns (True, '') on success or (False, human-readable error) on failure.
    """
    provider = detect_provider(api_key)
    if not provider:
        return False, (
            "Unrecognised key format. "
            "Expected sk-ant-… (Anthropic), xai-… (xAI), or sk-… (OpenAI)."
        )
    _, url = _MODELS_URLS[provider]
    headers = (
        {'x-api-key': api_key, 'anthropic-version': '2023-06-01'}
        if provider == 'anthropic'
        else {'Authorization': f'Bearer {api_key}'}
    )
    try:
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            return True, ''
        if r.status_code == 401:
            return False, "Key rejected by the provider — check for typos or that it hasn't expired."
        if r.status_code == 403:
            return False, "Access denied (403) — the key may lack required permissions."
        return False, f"Provider returned HTTP {r.status_code} — the key may be invalid."
    except requests.Timeout:
        return False, "Validation timed out — check your internet connection and try again."
    except Exception as e:
        return False, f"Could not reach provider: {e}"


# ── Error explanation ─────────────────────────────────────────────────────────

def explain_http_error(status: int | None, body: str = '') -> str:
    """Turn a provider HTTP error into a clear, user-facing message.

    The key may have validated fine against /v1/models yet still fail a real
    chat call — most often because it's out of credits, expired, or rate-
    limited. We surface that distinction explicitly so the user knows it's an
    account/billing issue, not a bug in the dashboard.
    """
    # Pull the provider's own message out of the JSON body if present.
    detail = ''
    try:
        parsed = json.loads(body) if body else {}
        err = parsed.get('error', parsed) if isinstance(parsed, dict) else {}
        if isinstance(err, dict):
            detail = (err.get('message') or '').strip()
    except Exception:
        pass

    blob = f"{detail} {body}".lower()
    out_of_credit = any(s in blob for s in (
        'credit balance', 'insufficient', 'quota', 'billing', 'payment',
        'exceeded your current', 'spending limit',
    ))

    if out_of_credit:
        msg = ("Your account is out of credits / over quota. The key is valid, "
               "but the provider won't process requests until you add billing or "
               "credit to your account.")
    elif status in (401, 403) or 'expired' in blob or 'revoked' in blob:
        msg = ("Your API key was rejected (it may be expired, revoked, or lack "
               "permission). Open the AI tab, clear the key, and paste a fresh one.")
    elif status == 429:
        msg = ("Rate limit / quota exceeded. The key is valid — wait a moment "
               "and try again, or check your account's usage limits.")
    elif status and 500 <= status < 600:
        msg = "The provider is having a temporary outage (server error). Try again shortly."
    else:
        msg = (f"The provider returned an error (HTTP {status}). This is an "
               "account/key issue, not a dashboard bug — check the key is valid "
               "and has credit.")

    if detail:
        msg += f"\n\nProvider said: {detail}"
    return msg


# ── Per-provider call ─────────────────────────────────────────────────────────

_TIMEOUT = 45  # seconds; the dashboard is synchronous, keep it modest


def _ask_openai_compatible(api_key: str, base_url: str, model: str,
                            messages: list) -> str:
    """Works for OpenAI and xAI (xAI exposes an OpenAI-compatible endpoint)."""
    r = requests.post(
        f"{base_url}/chat/completions",
        headers={'Authorization': f'Bearer {api_key}',
                 'Content-Type':  'application/json'},
        json={
            'model':    model,
            'messages': [{'role': 'system', 'content': SYSTEM_PROMPT}, *messages],
            'max_tokens':  900,
            'temperature': 0.4,
        },
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()['choices'][0]['message']['content'].strip()


def _ask_anthropic(api_key: str, model: str, messages: list) -> str:
    r = requests.post(
        'https://api.anthropic.com/v1/messages',
        headers={'x-api-key':          api_key,
                 'anthropic-version':  '2023-06-01',
                 'Content-Type':       'application/json'},
        json={
            'model':      model,
            'max_tokens': 900,
            'system':     SYSTEM_PROMPT,
            'messages':   messages,
        },
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    body = r.json()
    parts = [b.get('text', '') for b in body.get('content', []) if b.get('type') == 'text']
    return "\n".join(parts).strip()


# ── Public entry point ────────────────────────────────────────────────────────

_MODELS = {
    'anthropic': 'claude-haiku-4-5-20251001',
    'xai':       'grok-4-latest',
    'openai':    'gpt-4o-mini',
}


def ask(api_key: str, portfolio_json: str, user_question: str,
        history: list | None = None) -> tuple[str, list[str]]:
    """
    Send the portfolio context + prior turns + new question to the provider.
    Returns (answer_text, followup_suggestions).

    `history` is a list of prior turns in the shape [{'q': ..., 'a': ...}, ...].
    The portfolio JSON is attached to the FIRST user message only; subsequent
    turns rely on the model's memory of that context.

    Raises ValueError if the key prefix isn't recognised. Lets requests /
    HTTPError surface to the caller for HTTP failures.
    """
    provider = detect_provider(api_key)
    if not provider:
        raise ValueError("API key format not recognised. "
                         "Expected sk-ant-…, xai-…, or sk-… prefix.")

    history = history or []
    messages: list[dict] = []
    # Past turns
    for i, turn in enumerate(history):
        q = turn.get('q', '')
        a = turn.get('a', '')
        if i == 0:
            q = (
                "Here is the user's current portfolio as compact JSON:\n\n"
                f"```json\n{portfolio_json}\n```\n\n"
                f"Question: {q}"
            )
        messages.append({'role': 'user', 'content': q})
        if a:
            messages.append({'role': 'assistant', 'content': a})

    # New question — attach portfolio if this is the first turn
    if not history:
        new_content = (
            "Here is the user's current portfolio as compact JSON:\n\n"
            f"```json\n{portfolio_json}\n```\n\n"
            f"Question: {user_question}"
        )
    else:
        new_content = user_question
    messages.append({'role': 'user', 'content': new_content})

    model = _MODELS[provider]
    if provider == 'anthropic':
        raw = _ask_anthropic(api_key, model, messages)
    elif provider == 'xai':
        raw = _ask_openai_compatible(api_key, 'https://api.x.ai/v1', model, messages)
    elif provider == 'openai':
        raw = _ask_openai_compatible(api_key, 'https://api.openai.com/v1', model, messages)
    else:
        raise ValueError(f"Unhandled provider: {provider}")

    return _strip_followups(raw)


# ── Prompts unlocked by pasting a key ─────────────────────────────────────────

LLM_PROMPTS = [
    "Give me a full portfolio health check in 5 bullet points.",
    "What would a conservative investor change about this portfolio?",
    "If I had €500 more to invest today, where would you put it and why?",
    "Explain my biggest holding like I'm new to investing.",
    "What are three realistic risks in the next 12 months for this portfolio?",
    "Draft a one-sentence thesis for each of my holdings.",
]


# ── Compact context builder ───────────────────────────────────────────────────

def build_portfolio_context(port, intel, val, trades=None) -> str:
    """
    Build a compact JSON blob with everything the LLM needs. Kept small so
    even short-context providers have no problem.
    """
    compact = {}
    if port:
        summary = port.get('summary', {}) or {}
        compact['summary'] = {
            'total_value_eur':       summary.get('total_value'),
            'unrealised_pnl_eur':    summary.get('total_unrealized_pnl'),
            'unrealised_pnl_pct':    summary.get('total_pnl_pct'),
            'num_positions':         summary.get('num_positions'),
            'largest_position':      summary.get('largest_position'),
            'largest_position_pct':  summary.get('largest_position_pct'),
        }
        compact['positions'] = [{
            'ticker':        p.get('ticker'),
            'qty':           p.get('quantity'),
            'avg_cost':      p.get('avg_cost'),
            'price':         p.get('current_price'),
            'market_value':  p.get('market_value'),
            'pnl':           p.get('unrealized_pnl'),
            'pnl_pct':       p.get('pnl_pct'),
            'weight_pct':    p.get('allocation_pct'),
        } for p in port.get('positions', [])]
        acct = port.get('account') or {}
        compact['account'] = {
            'cash_eur':     acct.get('cash_eur'),
            'net_liq_eur':  acct.get('net_liquidation'),
            'eurusd_rate':  acct.get('eurusd_rate'),
        }
    if intel and intel.get('sector_geo'):
        compact['sector_geo'] = {
            t: {'sector': sg.get('sector'), 'country': sg.get('country'),
                'is_etf': sg.get('is_etf')}
            for t, sg in intel['sector_geo'].items()
        }
    if intel and intel.get('earnings'):
        compact['earnings'] = {
            t: e.get('next_date') for t, e in intel['earnings'].items()
            if e.get('next_date')
        }
    if val:
        compact['macro'] = {
            'buffett_indicator': (val.get('buffett') or {}).get('value'),
            'sp500_trailing_pe': (val.get('sp500_pe') or {}).get('trailing_pe'),
            'shiller_cape':      (val.get('cape') or {}).get('value'),
            'us_10y_yield':      (val.get('treasury') or {}).get('value'),
        }
    if trades:
        # Include the 50 most recent trades, compact shape, sorted newest-first.
        recent = sorted(trades, key=lambda t: t.get('time') or '', reverse=True)[:50]
        compact['trade_history'] = [{
            'ticker': t.get('ticker'),
            'side':   t.get('side'),
            'shares': t.get('shares'),
            'price':  t.get('price'),
            'time':   t.get('time'),
            'value':  t.get('value'),
        } for t in recent]
    return json.dumps(compact, separators=(',', ':'))
