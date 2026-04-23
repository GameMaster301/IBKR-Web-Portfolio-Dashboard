"""
Shared helpers for network-bound work: retries and parallel fan-out.

Anything that calls yfinance / FRED / multpl should go through here so
timeout, retry, and concurrency policies stay consistent. Before this
module existed the same patterns were duplicated across `analytics`,
`market_intel`, `market_valuation`, and two places in `dashboard.py`, each
with slightly different retry delays and worker caps.

Three helpers:
    fetch_with_retry   — call a zero-arg fn, retrying on exceptions
    fetch_parallel     — map ONE function across MANY items (fan-out)
    run_parallel       — run a set of named, independent tasks (fan-in)

All three catch per-item exceptions and log them rather than tearing down
the whole batch. fetch_with_retry is the exception — it re-raises the last
exception after exhausting retries, so callers can choose how to degrade.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Hashable, Iterable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar('T')
R = TypeVar('R')

# yfinance rate-limits above ~6 concurrent requests. Anywhere that needs a
# different cap should pass max_workers explicitly.
_DEFAULT_MAX_WORKERS = 6


# ── Retry ─────────────────────────────────────────────────────────────────────

def fetch_with_retry(fn: Callable[[], R],
                     retries: int = 3,
                     base_delay: float = 2.0) -> R:
    """Call `fn()` up to `retries` times, with increasing back-off between
    attempts (base_delay, 2·base_delay, 3·base_delay …).

    Returns `fn`'s result on success. Re-raises the last exception if every
    attempt fails — callers wrap this in their own try/except if they want
    a "return None on failure" semantics.
    """
    last_exc: BaseException | None = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                time.sleep(base_delay * (attempt + 1))
    assert last_exc is not None
    raise last_exc


# ── Parallel fan-out: one function, many items ────────────────────────────────

def fetch_parallel(items: Iterable[T],
                   fn: Callable[[T], R],
                   max_workers: int = _DEFAULT_MAX_WORKERS) -> dict[T, R | None]:
    """Map `fn` across `items` in a thread pool.

    Returns `{item: result}`. An exception for a single item is logged and
    becomes `None` in the result rather than aborting the whole batch. An
    empty iterable returns `{}` without spinning up any workers.
    """
    items_list = list(items)
    if not items_list:
        return {}
    workers = min(max_workers, len(items_list))

    def _safe(x: T) -> tuple[T, R | None]:
        try:
            return x, fn(x)
        except Exception as e:
            log.warning('fetch_parallel: %r failed: %s', x, e)
            return x, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(_safe, items_list))


# ── Parallel fan-in: named independent tasks ──────────────────────────────────

def run_parallel(tasks: dict[Hashable, Callable[[], R]],
                 max_workers: int | None = None) -> dict[Hashable, R | None]:
    """Run a dict of `{name: zero-arg callable}` concurrently.

    Returns `{name: result}`, with exceptions replaced by `None` (and
    logged). Useful when you need to run several *different* functions in
    parallel — e.g. "fetch Buffett + S&P P/E + CAPE + Treasury yield all at
    once" — as opposed to fetch_parallel which maps one function over many
    items.
    """
    if not tasks:
        return {}
    workers = max_workers or min(len(tasks), _DEFAULT_MAX_WORKERS)

    def _safe(item: tuple[Hashable, Callable[[], R]]) -> tuple[Hashable, R | None]:
        name, fn = item
        try:
            return name, fn()
        except Exception as e:
            log.warning('run_parallel: %r failed: %s', name, e)
            return name, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(_safe, tasks.items()))
