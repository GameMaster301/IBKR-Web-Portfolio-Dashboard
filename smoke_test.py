"""Fast sanity check: every pure function works on the demo payload.

No network, no Dash server, no IB connection. Target runtime: < 2 sec.
Run with: python smoke_test.py
"""

from __future__ import annotations

import sys

from ai_provider import _strip_followups, detect_provider, provider_label
from cache_util import cached_fetch
from coach import SCENARIOS, render_scenario
from data_processor import get_summary, process_positions
from demo_data import build_demo_payload
from market_valuation import buffett_zone, cape_zone, pe_zone, treasury_zone

_failed = 0


def check(name: str, cond: bool, detail: str = '') -> None:
    global _failed
    status = 'OK  ' if cond else 'FAIL'
    suffix = f'  {detail}' if detail else ''
    print(f'{status}  {name}{suffix}')
    if not cond:
        _failed += 1


# 1. Demo payload + pure transforms
demo = build_demo_payload()
check('demo payload has positions', len(demo['positions']) == 8)
check('demo payload has account',   'net_liquidation' in demo['account'])

df = process_positions(demo['positions'], demo['market_data'])
check('process_positions returns rows', len(df) == 8)
check('process_positions adds pnl_pct',  'pnl_pct' in df.columns)
check('process_positions adds allocation_pct', 'allocation_pct' in df.columns)

summary = get_summary(df)
check('get_summary has total_value', summary.get('total_value', 0) > 0)
check('get_summary has best/worst',
      bool(summary.get('best_performer')) and bool(summary.get('worst_performer')))

# 2. Coach scenarios render against a realistic port shape
port = {
    'positions': df.to_dict('records'),
    'summary':   summary,
    'account':   demo['account'],
    'div_data':  demo['div_data'],
}
for s in SCENARIOS:
    out = render_scenario(s['id'], port, {}, {})
    check(f'scenario {s["id"]} renders', out is not None)

# 3. Cache round-trip (single-flight + diskcache fallback)
val = cached_fetch(('smoke', 1), 60, lambda: 42)
check('cached_fetch round-trip', val == 42)
val2 = cached_fetch(('smoke', 1), 60, lambda: 99)  # second call should hit cache
check('cached_fetch returns cached value', val2 == 42)

# 4. Zone classifiers cover the full numeric range without crashing
for fn in (buffett_zone, pe_zone, cape_zone, treasury_zone):
    for v in (0, 1, 15, 25, 50, 100, 200):
        label, colour = fn(v)
        check(f'{fn.__name__}({v})', bool(label) and colour.startswith('#'))

# 5. LLM helpers work without a key
check('detect_provider unknown',   detect_provider('garbage') is None)
check('detect_provider empty',     detect_provider('') is None)
check('detect_provider anthropic', detect_provider('sk-ant-xxx') == 'anthropic')
check('detect_provider xai',       detect_provider('xai-xxx') == 'xai')
check('detect_provider openai',    detect_provider('sk-xxx') == 'openai')
check('provider_label maps',       provider_label('anthropic') == 'Anthropic Claude')

body, fups = _strip_followups('Hello.\nFOLLOWUPS: ["a","b","c"]')
check('strip_followups parses', body == 'Hello.' and len(fups) == 3)

body2, fups2 = _strip_followups('No tail here.')
check('strip_followups no-tail', body2 == 'No tail here.' and fups2 == [])


if _failed:
    print(f'\n{_failed} smoke check(s) failed.')
    sys.exit(1)
print('\nAll smoke checks passed.')
