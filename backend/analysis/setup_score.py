"""
analysis/setup_score.py — the Setup Score (Design C, Q8 rulings applied).

Every component is a PURE function: typed inputs → float in [−1, +1]
(bearish → bullish) or None (data unavailable). compose() renormalizes the
weights over available components (Q8c — missing data must not silently drag
the score bearish), applies the vol-regime conviction multiplier (Q8a — vol is
conviction, not direction), and returns full per-component contributions so
the UI's expansion table shows exactly where every point came from.

No ML anywhere. Deterministic, inspectable, unit-tested. The score aggregates
observable positioning; it does NOT predict direction — that claim died with
~9,600 backtested predictions (config.SCORE_DISCLAIMER ships with every
response).
"""
from __future__ import annotations

import math
from typing import Any

from config import (
    DEFAULT_WEIGHTS,
    LEAN_BEARISH,
    LEAN_BULLISH,
    SCORE_DISCLAIMER,
    VOL_FACTOR_MAX,
    VOL_FACTOR_MIN,
)


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# ─── components (each documented in glossary.json under the same key) ──────────

def c_momentum(sma20_dist: float | None, sma50_dist: float | None) -> float | None:
    """Price vs its 20d and 50d averages, saturating.

    tanh keeps a runaway distance (e.g. +30% above SMA20 after a melt-up) from
    pinning the whole score; ±3% on SMA20 / ±5% on SMA50 ≈ the linear region.
    Both distances agreeing is what earns a strong reading (the "alignment"
    in the spec).
    """
    if sma20_dist is None or sma50_dist is None:
        return None
    return _clamp(0.6 * math.tanh(sma20_dist / 3.0) + 0.4 * math.tanh(sma50_dist / 5.0))


def c_news_sentiment(bullish_pct: float | None, bearish_pct: float | None,
                     articles_week: int | None) -> float | None:
    """Finnhub bullish% − bearish%, damped by article count.

    A 90/10 split on 3 articles is noise; on 40 articles it's a real skew.
    Full weight from ~20 articles/week.
    """
    if bullish_pct is None or bearish_pct is None:
        return None
    volume_weight = min(1.0, (articles_week or 0) / 20.0)
    return _clamp((bullish_pct - bearish_pct) * volume_weight)


def c_options_positioning(put_call_ratio: float | None) -> float | None:
    """Put/call volume ratio, inverted and centered at 0.9 (the empirical
    neutral zone per the experiment's own interpretation notes: <0.7 bullish
    crowd, >1.1 bearish crowd). 0.4 → +1, 1.4 → −1."""
    if put_call_ratio is None:
        return None
    return _clamp((0.9 - put_call_ratio) / 0.5)


def c_analyst_trend(months: list[dict] | None) -> float | None:
    """Month-over-month change in the analyst buy/hold/sell mix (Q4 + D12:
    CHANGES carry residual information; levels are priced in within minutes).

    net(month) = (strong_buy + buy − sell − strong_sell) / total ∈ [−1, 1].
    Component = 2·Δnet + 0.5·net_now — the *shift* dominates, the level is a
    small anchor so a uniformly-loved name isn't scored as pure neutral.
    """
    if not months or len(months) < 1:
        return None

    def net(m: dict) -> float | None:
        total = m.get("total") or 0
        if total <= 0:
            return None
        return (m["strong_buy"] + m["buy"] - m["sell"] - m["strong_sell"]) / total

    net_now = net(months[0])
    if net_now is None:
        return None
    net_prev = net(months[1]) if len(months) > 1 else None
    delta = (net_now - net_prev) if net_prev is not None else 0.0
    return _clamp(2.0 * delta + 0.5 * net_now)


def c_social_buzz(polarity: float | None, total_msgs: int | None) -> float | None:
    """StockTwits polarity × log-scaled volume: 30 tagged-ish messages ≈ full
    weight, 3 messages ≈ a third. A loud stream matters; a dead one shouldn't."""
    if polarity is None:
        return None
    volume_weight = min(1.0, math.log10(1 + (total_msgs or 0)) / math.log10(31))
    return _clamp(polarity * volume_weight)


def vol_conviction_factor(rv_20d: float | None, rv_60d: float | None) -> float:
    """Q8a: vol regime is a conviction MULTIPLIER, not a directional component.

    ratio = rv20/rv60. Calm vs baseline (ratio < 1) ⇒ modestly more conviction
    (up to ×1.15); turbulent (ratio > 1) ⇒ less (down to ×0.85). Missing
    baseline ⇒ exactly 1.0 — no regime invented.
    """
    if not rv_20d or not rv_60d or rv_60d <= 0:
        return 1.0
    ratio = rv_20d / rv_60d
    return round(max(VOL_FACTOR_MIN, min(VOL_FACTOR_MAX, 1.0 + 0.5 * (1.0 - ratio))), 3)


# ─── composition ───────────────────────────────────────────────────────────────

COMPONENT_ORDER = ["momentum", "news_sentiment", "options_positioning",
                   "analyst_trend", "social_buzz"]


def compose(components: dict[str, float | None],
            weights: dict[str, float] | None = None,
            vol_factor: float = 1.0) -> dict[str, Any]:
    """Fold component values into the 0–100 score + lean + full transparency.

    score = 50 + 50 · tilt · vol_factor, where tilt is the weighted mean of
    AVAILABLE components with weights renormalized over them (Q8c). Returns
    per-component rows (value, weight used, points contributed) for the
    expandable UI table — the score must always be reconstructible by eye.
    """
    weights = weights or DEFAULT_WEIGHTS
    available = {k: v for k, v in components.items()
                 if v is not None and weights.get(k, 0) > 0}
    total_w = sum(weights[k] for k in available)

    rows = []
    tilt = 0.0
    for key in COMPONENT_ORDER:
        w_nominal = weights.get(key, 0.0)
        value = components.get(key)
        if key in available and total_w > 0:
            w_used = weights[key] / total_w          # renormalized share ∈ [0,1]
            contribution = w_used * value
            tilt += contribution
            rows.append({
                "component": key, "value": round(value, 3),
                "weight_nominal": w_nominal,
                "weight_used_pct": round(w_used * 100, 1),
                # points this row moved the score off 50 (vol factor applied
                # uniformly so rows still sum to the displayed score):
                "points": round(50 * contribution * vol_factor, 1),
                "available": True,
            })
        else:
            rows.append({
                "component": key, "value": None, "weight_nominal": w_nominal,
                "weight_used_pct": 0.0, "points": 0.0, "available": False,
            })

    score = 50.0 + 50.0 * tilt * vol_factor
    score = max(0.0, min(100.0, round(score, 1)))
    lean = ("BULLISH" if score >= LEAN_BULLISH
            else "BEARISH" if score <= LEAN_BEARISH
            else "NEUTRAL")

    return {
        "score": score,
        "lean": lean,
        "vol_factor": vol_factor,
        "components": rows,
        "components_available": len(available),
        "components_total": len([k for k in COMPONENT_ORDER if weights.get(k, 0) > 0]),
        "disclaimer": SCORE_DISCLAIMER,
    }


def compute_from_sections(signals: dict | None, news: dict | None,
                          options: dict | None, recs: dict | None,
                          social: dict | None,
                          weights: dict[str, float] | None = None) -> dict[str, Any]:
    """Adapter from composer section payloads → component inputs → compose().

    Sections that are None/unavailable simply yield None components; compose()
    handles the renormalization. Keeping this adapter thin (and tested) means
    the pure functions above never learn about payload shapes.
    """
    def sec(d: dict | None) -> dict:
        return d if isinstance(d, dict) and d.get("available", True) else {}

    s, n, o, r, so = (sec(x) for x in (signals, news, options, recs, social))
    components = {
        "momentum": c_momentum(s.get("sma20_dist"), s.get("sma50_dist")),
        "news_sentiment": c_news_sentiment(
            n.get("bullish_pct"), n.get("bearish_pct"), n.get("articles_week")),
        "options_positioning": c_options_positioning(o.get("put_call_ratio")),
        "analyst_trend": c_analyst_trend(r.get("months")),
        "social_buzz": c_social_buzz(so.get("polarity"), so.get("total_msgs")),
    }
    factor = vol_conviction_factor(s.get("rv_20d"), s.get("rv_60d"))
    return compose(components, weights, factor)
