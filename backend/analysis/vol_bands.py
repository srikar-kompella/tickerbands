"""
analysis/vol_bands.py — the one capability this project ever validated.

Math duplicated (NOT imported — Q11 isolation ruling) from the experiment
repo's compute_signals(), which produced 79.5% empirical coverage against an
80% target over 9,620 point-in-time predictions (BACKTEST_REPORT.md §5, R5).

Every function here is pure: lists in, dict out, no I/O — unit-testable
against pinned values from the backtest.

Vocabulary discipline (the whole point of this product): these are RANGES.
"80% expected range", "typical daily move" — never "prediction". The range
says nothing about direction; the parent experiment proved direction is not
predictable from these inputs (~9,600 predictions, three methods).
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

from config import BAND_Z, TRADING_DAYS


def completed_bars(history: list[dict], today: dt.date | None = None) -> list[dict]:
    """Drop any bar dated >= today.

    LOOKAHEAD_AUDIT F1: a partial same-day bar silently corrupts every derived
    signal (prev_close becomes a nowcast, volume_z collapses on the partial
    bar). The experiment's pipeline never had this guard; TickerLens signals
    are computed from completed sessions only, by construction.
    """
    cutoff = (today or dt.date.today()).isoformat()
    return [b for b in history if b["date"] < cutoff]


def realized_vol(closes: list[float], window: int) -> float:
    """Annualized realized volatility (%) from the last `window` close-to-close
    returns — sample std (n−1), same convention as the experiment."""
    if len(closes) < window + 1:
        raise ValueError(f"need {window + 1} closes, got {len(closes)}")
    rets = [closes[i] / closes[i - 1] - 1 for i in range(-window, 0)]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS) * 100


def compute_signals(closes: list[float], volumes: list[int]) -> dict[str, Any]:
    """Technical snapshot from completed daily bars (most recent last).

    Same fields + same math as the experiment's compute_signals() — kept
    field-compatible so the pinned-value unit tests can assert equivalence
    against rows from data/backtest_cache/backtest_rows.csv.
    """
    if len(closes) < 21:
        raise ValueError(f"insufficient history ({len(closes)} days)")

    prev_close = closes[-1]
    sma20 = sum(closes[-20:]) / 20.0
    sma50 = sum(closes[-50:]) / 50.0 if len(closes) >= 50 else sma20

    return {
        "prev_close": round(prev_close, 4),
        "return_1d": round((closes[-1] / closes[-2] - 1) * 100, 3),
        "return_5d": round((closes[-1] / closes[-6] - 1) * 100, 3),
        "return_20d": round((closes[-1] / closes[-21] - 1) * 100, 3),
        "rv_20d": round(realized_vol(closes, 20), 2),
        # rv_60d is TickerLens-only: baseline for the vol-regime conviction
        # multiplier (Q8a). None when history is short — the multiplier
        # degrades to 1.0 rather than inventing a regime.
        "rv_60d": round(realized_vol(closes, 60), 2) if len(closes) >= 61 else None,
        "sma20_dist": round((closes[-1] / sma20 - 1) * 100, 2),
        "sma50_dist": round((closes[-1] / sma50 - 1) * 100, 2),
        "volume_z": round(_volume_z(volumes), 2),
    }


def _volume_z(volumes: list[int]) -> float:
    """Yesterday's volume vs its trailing 20-day distribution."""
    vol_mean = sum(volumes[-20:]) / 20.0
    vol_var = sum((v - vol_mean) ** 2 for v in volumes[-20:]) / 19.0
    vol_std = math.sqrt(vol_var) if vol_var > 0 else 1.0
    return (volumes[-1] - vol_mean) / vol_std


def ewma_vol(closes: list[float], lam: float = 0.94) -> float:
    """EWMA (RiskMetrics) annualized vol %, λ=0.94. Validated on 7,309
    point-in-time days: 82.7% coverage at ±3.24% mean width — REJECTED as
    default (wider AND over-covering vs flat20's 80.6% at ±3.16%), but kept
    as a selectable engine because the evidence lives in band_validation.json
    and users deserve the choice with the numbers attached."""
    if len(closes) < 21:
        raise ValueError("need ≥21 closes")
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    var = sum((r - sum(rets[:20]) / 20) ** 2 for r in rets[:20]) / 20  # seed
    for r in rets[20:]:
        var = lam * var + (1 - lam) * r * r
    return math.sqrt(var) * math.sqrt(TRADING_DAYS) * 100


def conformal_halfwidth(closes: list[float], window: int = 250,
                        q: float = 0.80) -> float | None:
    """Distribution-free half-width: empirical q-quantile of the last `window`
    absolute daily moves (%). Validated: 79.1% coverage at ±2.85% — ~10%
    narrower than flat20 but slightly under target with wide per-ticker spread
    (min 71.8%). Selectable, not default. None if history is short."""
    if len(closes) < window + 1:
        return None
    rets = sorted(abs(closes[i] / closes[i - 1] - 1)
                  for i in range(len(closes) - window, len(closes)))
    return rets[int(q * window)] * 100


def band(prev_close: float, rv_20d: float) -> dict[str, Any]:
    """The 80% expected range for the next session.

    daily_sigma = rv_20d / √252 (de-annualize); half-width = 1.28σ (two-sided
    80% z). This exact construction (prev_close-centered) covered 80.5% of
    9,620 historical next-day moves when replayed over the experiment's
    backtest rows — the experiment's point_est-centered variant scored 79.5%;
    removing the dead point estimate slightly IMPROVED calibration. Zero
    fitting; do not tune BAND_Z.
    """
    daily_sigma_pct = rv_20d / math.sqrt(TRADING_DAYS)
    half_width_pct = BAND_Z * daily_sigma_pct
    return {
        "daily_sigma_pct": round(daily_sigma_pct, 3),
        "half_width_pct": round(half_width_pct, 3),
        "low": round(prev_close * (1 - half_width_pct / 100), 2),
        "high": round(prev_close * (1 + half_width_pct / 100), 2),
        "prev_close": round(prev_close, 4),
        "coverage_target": 0.80,
        "engine": "flat20",
    }


def band_with_engine(prev_close: float, closes: list[float],
                     engine: str = "flat20") -> dict[str, Any]:
    """Engine-selectable band. Falls back to flat20 whenever the requested
    engine can't compute (short history) — a band must always exist."""
    if engine == "ewma" and len(closes) >= 21:
        b = band(prev_close, ewma_vol(closes))
        b["engine"] = "ewma"
        return b
    if engine == "conformal":
        half = conformal_halfwidth(closes)
        if half is not None:
            return {
                "daily_sigma_pct": round(half / BAND_Z, 3),  # implied σ for z-score reuse
                "half_width_pct": round(half, 3),
                "low": round(prev_close * (1 - half / 100), 2),
                "high": round(prev_close * (1 + half / 100), 2),
                "prev_close": round(prev_close, 4),
                "coverage_target": 0.80,
                "engine": "conformal",
            }
    return band(prev_close, realized_vol(closes, 20))


def move_z_score(prev_close: float, current_price: float, rv_20d: float) -> dict[str, Any]:
    """How unusual is today's move so far? (S3 — DIAGNOSTIC_REPORT's own framing:
    'here's how unusual today's move was'.)

    z = today's % move / daily σ. |z| ≥ 1.28 means today already left the 80%
    range; the percentile contextualizes it (two-sided, normal approx —
    labeled as such in the glossary; fat tails make extreme percentiles
    understated, which is the conservative direction for a risk lens).
    """
    daily_sigma_pct = rv_20d / math.sqrt(TRADING_DAYS)
    move_pct = (current_price / prev_close - 1) * 100 if prev_close else 0.0
    z = move_pct / daily_sigma_pct if daily_sigma_pct > 0 else 0.0
    pctile = 2 * _phi(abs(z)) - 1  # fraction of days with a SMALLER |move|
    return {
        "move_pct": round(move_pct, 2),
        "z": round(z, 2),
        "abs_percentile": round(pctile * 100, 1),
        # epsilon so a move at exactly the band edge counts as "left the range"
        # (float noise must not flip the boundary case):
        "outside_band": abs(z) >= BAND_Z - 1e-9,
    }


def _phi(x: float) -> float:
    """Standard normal CDF via erf (stdlib-only)."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def beta_and_correlation(closes: list[float], spy_closes: list[float],
                         window: int = 60) -> dict[str, Any]:
    """OLS beta + Pearson correlation of daily returns vs SPY (S5).

    Uses the overlapping tail of both series; requires `window` paired returns.
    Pure context numbers — no signal claim.
    """
    n = min(len(closes), len(spy_closes))
    if n < window + 1:
        return {"beta": None, "correlation": None, "window": window}
    a = [closes[i] / closes[i - 1] - 1 for i in range(-window, 0)]
    b = [spy_closes[i] / spy_closes[i - 1] - 1 for i in range(-window, 0)]
    ma, mb = sum(a) / window, sum(b) / window
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (window - 1)
    var_a = sum((x - ma) ** 2 for x in a) / (window - 1)
    var_b = sum((y - mb) ** 2 for y in b) / (window - 1)
    if var_b <= 0 or var_a <= 0:
        return {"beta": None, "correlation": None, "window": window}
    return {
        "beta": round(cov / var_b, 2),
        "correlation": round(cov / math.sqrt(var_a * var_b), 2),
        "window": window,
    }
