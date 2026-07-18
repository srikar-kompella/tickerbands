"""
analysis/score_audit.py — does YOUR Setup Score correlate with anything? (#10)

The anti-delusion instrument: joins accumulated setup_score_history rows to
what prices did in the following 5 and 20 trading days, then buckets by score
band. Either the deciles line up (tune weights with evidence) or they don't
(stop staring at the number). Same discipline that killed the parent
experiment, pointed inward.

Forward returns come from the cached daily history (no extra API calls); a
score too recent to have a forward window yet is simply pending. One score
per symbol per DAY (the latest) — analyzing a ticker five times in an hour
must not create five pseudo-samples.
"""
from __future__ import annotations

import json
from typing import Any

from db import connect

BANDS = [(70.0, "70+ (green)"), (55.0, "55–70"), (40.0, "40–55"), (0.0, "<40 (red)")]


def _history_closes(symbol: str) -> list[dict] | None:
    conn = connect()
    try:
        row = conn.execute("SELECT payload FROM cache WHERE key = ?",
                           (f"history:{symbol}",)).fetchone()
        return json.loads(row["payload"]) if row else None
    finally:
        conn.close()


def _fwd_return(bars: list[dict], date: str, horizon: int) -> float | None:
    dates = [b["date"] for b in bars]
    # first bar ON or AFTER the score date = entry close
    entry = next((i for i, d in enumerate(dates) if d >= date), None)
    if entry is None or entry + horizon >= len(bars):
        return None
    return round((bars[entry + horizon]["close"] / bars[entry]["close"] - 1) * 100, 2)


def score_audit() -> dict[str, Any]:
    conn = connect()
    try:
        rows = conn.execute(
            # one row per symbol per day — the day's LAST computed score
            "SELECT symbol, MAX(ts) AS ts, score FROM setup_score_history "
            "GROUP BY symbol, substr(ts, 1, 10) ORDER BY ts").fetchall()
    finally:
        conn.close()

    samples, pending = [], 0
    bars_cache: dict[str, list[dict] | None] = {}
    for r in rows:
        sym = r["symbol"]
        if sym not in bars_cache:
            bars_cache[sym] = _history_closes(sym)
        bars = bars_cache[sym]
        if not bars:
            pending += 1
            continue
        day = r["ts"][:10]
        f5, f20 = _fwd_return(bars, day, 5), _fwd_return(bars, day, 20)
        if f5 is None and f20 is None:
            pending += 1
            continue
        samples.append({"symbol": sym, "date": day, "score": r["score"],
                        "fwd5_pct": f5, "fwd20_pct": f20})

    buckets = []
    for lo, label in BANDS:
        greater = [l2 for l2, _ in BANDS if l2 > lo]
        hi = min(greater) if greater else 101.0   # nearest upper boundary
        grp = [s for s in samples if lo <= s["score"] < hi]
        for horizon, key in ((5, "fwd5_pct"), (20, "fwd20_pct")):
            vals = [g[key] for g in grp if g[key] is not None]
            if not vals:
                continue
            buckets.append({
                "band": label, "horizon_days": horizon, "n": len(vals),
                "mean_fwd_pct": round(sum(vals) / len(vals), 2),
                "hit_rate_up": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
            })

    return {
        "samples": samples[-500:],
        "buckets": buckets,
        "n_scored_days": len(samples),
        "n_pending": pending,
        "sufficient": len(samples) >= 30,
        "note": ("Needs ≥30 scored days before the buckets mean anything. "
                 "If the bands DON'T order themselves, that's a finding, not a bug — "
                 "it's the same result the parent experiment got at scale."),
    }
