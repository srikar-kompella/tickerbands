"""
analysis/earnings_moves.py — earnings expected-move + PEAD context (roadmap #3, #7).

Pure functions. The band is calibrated for NORMAL days; earnings days are the
documented exception. This module quantifies the exception instead of just
warning about it: what the stock ACTUALLY did on its last ~8 earnings days,
vs what the options market is implying for the next one (implied/historical
ratio — the standard rich/cheap gauge, per ORATS methodology).

PEAD: after a large surprise, prices historically drift in the surprise's
direction for ~60 trading days (Bernard & Thomas lineage). Shown as documented
context, never as a signal claim.
"""
from __future__ import annotations

import datetime as dt
from statistics import mean, median
from typing import Any

PEAD_WINDOW_TRADING_DAYS = 60
PEAD_MIN_SURPRISE_PCT = 5.0


def earnings_day_moves(bars: list[dict], events: list[dict]) -> list[dict[str, Any]]:
    """Actual close-to-close move for each past earnings event.

    Timing matters: a BMO (before-open) report moves THAT session; an AMC
    (after-close) report moves the NEXT session. Unknown timing → the larger
    of the two, flagged — honest about the ambiguity rather than guessing.
    """
    dates = [b["date"] for b in bars]
    idx = {d: i for i, d in enumerate(dates)}

    def move_at(i: int) -> float | None:
        if 0 < i < len(bars):
            return (bars[i]["close"] / bars[i - 1]["close"] - 1) * 100
        return None

    out = []
    for ev in events:
        d = ev.get("date")
        if not d:
            continue
        # find that or the next trading day present in bars
        i = idx.get(d)
        if i is None:
            later = [j for j, dd in enumerate(dates) if dd > d]
            i = later[0] if later else None
        if i is None:
            continue
        hour = (ev.get("hour") or "").lower()
        if hour == "bmo":
            mv, amb = move_at(i), False
        elif hour == "amc":
            mv, amb = move_at(i + 1), False
        else:
            candidates = [m for m in (move_at(i), move_at(i + 1)) if m is not None]
            mv = max(candidates, key=abs) if candidates else None
            amb = True
        if mv is not None:
            out.append({"date": d, "move_pct": round(mv, 2),
                        "timing_ambiguous": amb})
    return out


def expected_move_summary(implied_move_pct: float | None,
                          hist_moves: list[dict]) -> dict[str, Any] | None:
    """Implied (options) vs historical (last ~8 quarters) earnings move."""
    if not hist_moves and implied_move_pct is None:
        return None
    abs_moves = [abs(m["move_pct"]) for m in hist_moves]
    hist_med = round(median(abs_moves), 2) if abs_moves else None
    return {
        "implied_move_pct": implied_move_pct,
        "hist_median_abs_pct": hist_med,
        "hist_mean_abs_pct": round(mean(abs_moves), 2) if abs_moves else None,
        "hist_max_abs_pct": round(max(abs_moves), 2) if abs_moves else None,
        "quarters": len(abs_moves),
        # >1: options pricing MORE than this stock's own earnings history;
        # <1: pricing less. The standard rich/cheap gauge — context, not advice.
        "implied_vs_hist": (round(implied_move_pct / hist_med, 2)
                            if implied_move_pct is not None and hist_med
                            else None),
        "recent": hist_moves[-8:],
    }


def pead_flag(events: list[dict], surprises: list[dict],
              bars: list[dict], today: dt.date | None = None) -> dict[str, Any] | None:
    """Post-earnings-announcement-drift context: active when the LAST report
    was a big surprise (|%| ≥ 5) within ~60 trading days."""
    if not events or not surprises or not bars:
        return None
    today_s = (today or dt.date.today()).isoformat()
    past = sorted((e["date"] for e in events if e.get("date") and e["date"] <= today_s))
    if not past:
        return None
    last_date = past[-1]
    trading_days_since = sum(1 for b in bars if last_date < b["date"] <= today_s)
    if trading_days_since > PEAD_WINDOW_TRADING_DAYS:
        return None
    # latest surprise with a usable percentage
    sur = next((s for s in surprises if s.get("surprise_pct") is not None), None)
    if sur is None or abs(sur["surprise_pct"]) < PEAD_MIN_SURPRISE_PCT:
        return None
    return {
        "active": True,
        "report_date": last_date,
        "trading_days_since": trading_days_since,
        "window_days": PEAD_WINDOW_TRADING_DAYS,
        "surprise_pct": sur["surprise_pct"],
        "direction": "positive" if sur["surprise_pct"] > 0 else "negative",
    }
