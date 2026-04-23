"""
Shared persistent cache used by market_intel, market_valuation, and analytics.

Replaces the previous per-module `_CACHE` / `_div_cache` dicts. Values are
persisted to disk via `diskcache`, so cold starts (restart, container
re-deploy) no longer need to re-hit yfinance/FRED/multpl for everything.

If `diskcache` is unavailable for any reason we silently fall back to an
in-memory TTL dict, preserving the old behaviour.

Cache location: `<IBKRDASH_DATA_DIR or ./data>/cache/`.

Concurrency: `cached_fetch` implements single-flight — concurrent misses on
the same key serialize on a per-key lock so `fn()` is invoked once, not N
times. Misses on different keys run in parallel. Hits take the fast path
and never touch the lock.
"""

from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger(__name__)

_MISS = object()

try:
    import diskcache  # type: ignore

    _dir = os.environ.get('IBKRDASH_DATA_DIR') \
        or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
    _DISK = diskcache.Cache(os.path.join(_dir, 'cache'))
    log.debug('cache_util: diskcache at %s', _DISK.directory)
except Exception as e:
    _DISK = None
    log.warning('cache_util: diskcache unavailable (%s); using in-memory cache', e)


_MEM: dict = {}

# Per-key locks for the single-flight pattern. A registry lock guards the
# creation of per-key locks so two threads never create different Lock()
# objects for the same key.
_REGISTRY_LOCK = threading.Lock()
_KEY_LOCKS: dict = {}


def _lock_for(key) -> threading.Lock:
    """Return the per-key lock for `key`, creating it on first use."""
    with _REGISTRY_LOCK:
        lk = _KEY_LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _KEY_LOCKS[key] = lk
        return lk


def _cache_read(key):
    """Return the cached value for `key` or `_MISS` if absent/expired.

    Disk path: diskcache handles expiry internally; we just treat `_MISS` as
    "not present". In-memory fallback stores `(expiry_ts, val)` so the
    staleness check is a single timestamp compare.
    """
    if _DISK is not None:
        try:
            hit = _DISK.get(key, default=_MISS)
            return hit
        except Exception as e:
            log.debug('cache_util: disk read failed for %r: %s', key, e)
            return _MISS

    hit = _MEM.get(key)
    if hit is None:
        return _MISS
    expiry_ts, val = hit
    if time.time() >= expiry_ts:
        return _MISS
    return val


def _cache_write(key, val, ttl: int) -> None:
    if _DISK is not None:
        try:
            _DISK.set(key, val, expire=ttl)
        except Exception as e:
            log.debug('cache_util: disk write failed for %r: %s', key, e)
        return
    _MEM[key] = (time.time() + ttl, val)


def cached_fetch(key, ttl: int, fn):
    """
    Return cached value for `key` if fresh, else call `fn()`, cache it, return it.

    Single-flight: if multiple threads call this concurrently with the same
    `key` during a miss, only one of them will execute `fn()`; the others
    will wait, then read the populated value. Different keys don't block
    each other. Cache hits never acquire the lock.

    `key` must be hashable and picklable (tuples of strings/ints are fine).
    `ttl` is seconds. When diskcache is available, values persist across
    process restarts; otherwise falls back to an in-memory dict with the
    same semantics.
    """
    # Fast path: no lock on hit.
    val = _cache_read(key)
    if val is not _MISS:
        return val

    # Miss: serialize concurrent fetchers on the same key.
    lock = _lock_for(key)
    with lock:
        # Re-check under the lock: another thread may have populated the
        # cache while we were waiting.
        val = _cache_read(key)
        if val is not _MISS:
            return val
        val = fn()
        _cache_write(key, val, ttl)
        return val
