"""
analysis/breach.py — empirical N-day breach probabilities (the premium lens core).

P(N-day return > r) = fraction of the trailing 500-day window's N-day returns
above r. Validated point-in-time (analysis/breach_validation.json):
  N=5:  max bucket deviation 6.8pp   ✓
  N=10: 6.1pp                        ✓
  N=21: 9.5pp                        ✓ with caveat
  N=45: 12.8pp, ~5–60 independent samples — REJECTED (LENS_MAX_DTE enforces it)
Known bias the UI must surface: low-probability buckets UNDERESTIMATE tail
breaches at N≥10 (pred 10% → realized 14–19%) — the exact zone where premium
sellers get run over. Rows in that zone carry a tail warning, not an adjustment
(we display measured error; we don't fudge estimates).

Pure functions; overlapping windows mean effective sample ≈ window/N — reported,
not hidden.
"""
from __future__ import annotations

WINDOW = 500
LENS_MAX_DTE_CALENDAR = 35   # ≈21 trading days — the validation boundary
TAIL_WARN_BELOW = 0.15       # empirical prob under 15% = documented tail zone


def nday_returns(closes: list[float], n: int, window: int = WINDOW) -> list[float]:
    """Trailing `window` overlapping N-day simple returns (most recent last)."""
    if n < 1 or len(closes) < n + 2:
        return []
    rets = [closes[i + n] / closes[i] - 1 for i in range(len(closes) - n)]
    return rets[-window:]


def breach_prob_above(closes: list[float], n: int, r: float,
                      window: int = WINDOW) -> float | None:
    """P(N-day return > r). None when history is too thin to say anything."""
    rets = nday_returns(closes, n, window)
    if len(rets) < 250:
        return None
    return round(sum(1 for x in rets if x > r) / len(rets), 4)


def breach_prob_below(closes: list[float], n: int, r: float,
                      window: int = WINDOW) -> float | None:
    """P(N-day return < r) — the cash-secured-put side."""
    rets = nday_returns(closes, n, window)
    if len(rets) < 250:
        return None
    return round(sum(1 for x in rets if x < r) / len(rets), 4)


def trading_days(dte_calendar: int) -> int:
    """Calendar DTE → trading-day horizon for the estimator."""
    return max(1, round(dte_calendar * 252 / 365))


def effective_samples(n: int, window: int = WINDOW) -> int:
    """Independent-ish sample count under overlap — honesty for the UI."""
    return max(1, window // max(n, 1))
