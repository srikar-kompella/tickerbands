"""
cache/store.py — SQLite-backed, per-key TTL cache with stale-while-error.

Contract (Q6/Q12):
  * get_or_fetch(key, ttl, fn):
      - fresh hit  → cached data, source="cache"
      - miss/stale → call fn(); on success store + return source="live"
      - fn() raises ProviderError AND stale data exists → serve stale,
        source="stale", carrying the error reason (UI shows age + reason
        instead of a dead section)
      - fn() raises and nothing cached → re-raise (composer marks section
        unavailable)
  * force=True skips freshness (the универсal refresh button) but still falls
    back to stale on failure — a refresh click must never *lose* data.

Payloads are JSON — providers return plain dicts/lists by design.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

from db import connect
from providers.base import ProviderError

# In-flight dedupe: two concurrent requests for the same key must NOT both hit
# the provider (double quota burn + write race). One fetches; the other waits
# and reads the fresh cache. Locks are per-key, created lazily.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _key_lock(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = threading.Lock()
        return lock


def _get_row(key: str) -> tuple[Any, float] | None:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT payload, fetched_ts FROM cache WHERE key = ?", (key,)
        ).fetchone()
        return (json.loads(row["payload"]), row["fetched_ts"]) if row else None
    finally:
        conn.close()


def _put_row(key: str, data: Any) -> float:
    now = time.time()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO cache (key, payload, fetched_ts) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET payload = excluded.payload, "
            "fetched_ts = excluded.fetched_ts",
            (key, json.dumps(data), now),
        )
        conn.commit()
        return now
    finally:
        conn.close()


def get_or_fetch(key: str, ttl_seconds: int, fetch_fn: Callable[[], Any],
                 force: bool = False) -> dict[str, Any]:
    """Returns {data, fetched_ts, age_seconds, source, error_reason?}."""
    cached = _get_row(key)
    now = time.time()

    if cached and not force and (now - cached[1]) < ttl_seconds:
        return {"data": cached[0], "fetched_ts": cached[1],
                "age_seconds": round(now - cached[1], 1), "source": "cache"}

    with _key_lock(key):
        # double-check: another thread may have fetched while we waited
        cached = _get_row(key)
        now = time.time()
        if cached and not force and (now - cached[1]) < ttl_seconds:
            return {"data": cached[0], "fetched_ts": cached[1],
                    "age_seconds": round(now - cached[1], 1), "source": "cache"}
        return _fetch_locked(key, ttl_seconds, fetch_fn, cached, now)


def _fetch_locked(key, ttl_seconds, fetch_fn, cached, now):
    try:
        data = fetch_fn()
        ts = _put_row(key, data)
        return {"data": data, "fetched_ts": ts, "age_seconds": 0.0, "source": "live"}
    except ProviderError as e:
        if cached:
            # Stale beats blank: keep the section alive, tell the truth about age.
            return {"data": cached[0], "fetched_ts": cached[1],
                    "age_seconds": round(now - cached[1], 1), "source": "stale",
                    "error_reason": e.reason, "error_detail": e.detail}
        raise


def put(key: str, data: Any) -> None:
    """Direct cache write — for batch fetches that warm MANY keys in one API
    call (e.g. one get_quotes call caching 36 quote:SYM entries so the
    screener and analysis pages reuse them within their TTL)."""
    _put_row(key, data)


def invalidate(prefix: str = "") -> int:
    """Delete cache rows by key prefix ('' = everything). Returns rows removed."""
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM cache WHERE key LIKE ?", (f"{prefix}%",))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
