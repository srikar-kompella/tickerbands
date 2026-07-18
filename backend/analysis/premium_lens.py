"""
analysis/premium_lens.py — the premium-seller's lens (covered calls + CSPs).

For every liquid short-strike candidate inside the validated DTE window:
  the market's implied breach probability (|delta|)  vs
  OUR empirical breach probability (breach.py, validation receipt attached)
edge = implied − empirical. Positive edge = the market is paying more for the
risk than this stock's own two-year history says the risk costs. That's the
variance risk premium made visible per strike — with the tail-zone warning
where the validation says our estimator flatters (low-prob buckets at N≥10).

This surfaces RISK PRICING, not direction. Assignment still happens; the
NFLX −8.2% print was an expected-size move in the unwanted direction.
"""
from __future__ import annotations

from typing import Any

from analysis import breach

MIN_OI = 50
MAX_SPREAD_PCT = 12.0   # (ask−bid)/mark — wider than this isn't sellable retail
MAX_ROWS_PER_SIDE = 14


def _annualized_yield_pct(bid: float, basis: float, dte: int) -> float:
    """Premium / capital-at-risk, annualized. CC basis = spot (shares held);
    CSP basis = strike (cash secured)."""
    if basis <= 0 or dte <= 0:
        return 0.0
    return round(bid / basis * 365 / dte * 100, 1)


def _row(c: dict, spot: float, closes: list[float],
         earnings_days: int | None) -> dict[str, Any] | None:
    if c["oi"] < MIN_OI or c["mark"] <= 0:
        return None
    spread_pct = (c["ask"] - c["bid"]) / c["mark"] * 100 if c["mark"] else 99
    if spread_pct > MAX_SPREAD_PCT:
        return None
    n = breach.trading_days(c["dte"])
    r = c["strike"] / spot - 1
    if c["side"] == "call":
        empirical = breach.breach_prob_above(closes, n, r)
        basis = spot
    else:
        empirical = breach.breach_prob_below(closes, n, r)
        basis = c["strike"]
    if empirical is None:
        return None
    implied = round(abs(c["delta"]), 4)
    return {
        "side": c["side"], "strike": c["strike"], "expiry": c["expiry"],
        "dte": c["dte"], "bid": c["bid"], "moneyness_pct": round(r * 100, 1),
        "yield_ann_pct": _annualized_yield_pct(c["bid"], basis, c["dte"]),
        "implied_breach": implied,
        "empirical_breach": empirical,
        "edge_pp": round((implied - empirical) * 100, 1),
        "tail_zone": empirical < breach.TAIL_WARN_BELOW,   # validated under-estimate zone
        "earnings_inside": (earnings_days is not None and earnings_days <= c["dte"]),
        "oi": c["oi"], "spread_pct": round(spread_pct, 1),
        "iv_pct": c["iv_pct"],
    }


def compose_lens(chain: dict, closes: list[float],
                 earnings_days: int | None) -> dict[str, Any]:
    """Chain + 2y closes → ranked CC/CSP tables + 25Δ skew + caveats."""
    if not chain.get("available") or len(closes) < 300:
        return {"available": False,
                "reason": ("no listed options" if not chain.get("available")
                           else "insufficient price history (need ~300 days)")}
    spot = chain["spot"] or (closes[-1] if closes else 0)

    def build(contracts: list[dict], want_otm_above: bool) -> list[dict]:
        rows = []
        for c in contracts:
            otm = c["strike"] > spot if want_otm_above else c["strike"] < spot
            if not otm:
                continue
            row = _row(c, spot, closes, earnings_days)
            if row:
                rows.append(row)
        rows.sort(key=lambda x: -x["edge_pp"])
        return rows[:MAX_ROWS_PER_SIDE]

    calls = build(chain["calls"], want_otm_above=True)
    puts = build(chain["puts"], want_otm_above=False)

    # 25-delta skew: what the crowd pays for crash insurance vs upside
    def iv_near(contracts: list[dict], target: float) -> float | None:
        best = min(contracts, key=lambda c: abs(abs(c["delta"]) - target), default=None)
        return best["iv_pct"] if best and abs(abs(best["delta"]) - target) < 0.12 else None

    put_iv, call_iv = iv_near(chain["puts"], 0.25), iv_near(chain["calls"], 0.25)
    skew = (round(put_iv - call_iv, 1)
            if put_iv is not None and call_iv is not None else None)

    return {
        "available": True,
        "spot": spot,
        "calls": calls,
        "puts": puts,
        "skew_25d_pp": skew,
        "caveats": {
            "dte_cap_calendar": breach.LENS_MAX_DTE_CALENDAR,
            "validation": {"5d": "±6.8pp", "10d": "±6.1pp", "21d": "±9.5pp",
                           "45d": "REJECTED (±12.8pp, too few independent samples)"},
            "tail_note": ("Empirical probabilities under ~15% historically UNDER-"
                          "estimate breaches by up to ~5–9pp at 2–4 week horizons "
                          "— the tail is worse than it looks, by measurement."),
            "n_effective": {str(n): breach.effective_samples(n) for n in (5, 10, 21)},
        },
    }
