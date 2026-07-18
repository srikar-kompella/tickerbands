"""
analysis/portfolio_risk.py — the validated band machinery, applied to the
WHOLE account (roadmap #6). Pure math, no new data.

Variance–covariance: daily returns (60d) per holding → sample covariance Σ →
portfolio σ = √(wᵀΣw) over INVESTED weights; cash is a zero-vol asset that
scales the band down at the total-value level. Also: diversification ratio
(weighted-avg σ ÷ portfolio σ — how much vol diversification removes),
concentration (HHI), portfolio beta vs SPY. Same honesty rules as the ticker
band: a RANGE for tomorrow, never a direction.
"""
from __future__ import annotations

import math
from typing import Any

from config import BAND_Z, TRADING_DAYS

WINDOW = 60


def _returns(closes: list[float], n: int) -> list[float]:
    return [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - n, len(closes))]


def portfolio_risk(positions: list[dict], closes_map: dict[str, list[float]],
                   cash: float, spy_closes: list[float] | None) -> dict[str, Any]:
    """positions: [{symbol, market_value}]; closes_map: symbol → daily closes.
    Holdings without enough history are excluded from Σ and listed — the
    number says what it covers instead of silently pretending."""
    usable, excluded = [], []
    for p in positions:
        closes = closes_map.get(p["symbol"]) or []
        if len(closes) >= WINDOW + 1:
            usable.append((p["symbol"], p["market_value"], _returns(closes, WINDOW)))
        else:
            excluded.append(p["symbol"])

    invested = sum(mv for _, mv, _ in usable)
    total = invested + cash + sum(p["market_value"] for p in positions
                                  if p["symbol"] in excluded)
    if not usable or invested <= 0:
        return {"available": False, "excluded": excluded}

    w = [mv / invested for _, mv, _ in usable]
    rets = [r for _, _, r in usable]

    means = [sum(r) / WINDOW for r in rets]
    cov = [[sum((rets[i][k] - means[i]) * (rets[j][k] - means[j])
                for k in range(WINDOW)) / (WINDOW - 1)
            for j in range(len(rets))] for i in range(len(rets))]

    var_p = sum(w[i] * w[j] * cov[i][j]
                for i in range(len(w)) for j in range(len(w)))
    sigma_p = math.sqrt(max(var_p, 0.0))                 # daily, invested sleeve
    sigmas = [math.sqrt(cov[i][i]) for i in range(len(w))]
    weighted_avg_sigma = sum(w[i] * sigmas[i] for i in range(len(w)))

    # scale to TOTAL account: cash + excluded treated as zero-vol
    sigma_total = sigma_p * invested / total if total else 0.0
    band_dollars = BAND_Z * sigma_total * total

    beta = None
    if spy_closes and len(spy_closes) >= WINDOW + 1:
        spy_r = _returns(spy_closes, WINDOW)
        port_r = [sum(w[i] * rets[i][k] for i in range(len(w))) for k in range(WINDOW)]
        ms = sum(spy_r) / WINDOW
        mp = sum(port_r) / WINDOW
        cov_ps = sum((port_r[k] - mp) * (spy_r[k] - ms) for k in range(WINDOW)) / (WINDOW - 1)
        var_s = sum((x - ms) ** 2 for x in spy_r) / (WINDOW - 1)
        beta = round(cov_ps / var_s, 2) if var_s > 0 else None

    weights_total = [(sym, mv / total) for sym, mv, _ in usable]
    return {
        "available": True,
        "sigma_daily_pct": round(sigma_total * 100, 3),
        "annualized_vol_pct": round(sigma_total * math.sqrt(TRADING_DAYS) * 100, 1),
        "band_low": round(total - band_dollars, 2),
        "band_high": round(total + band_dollars, 2),
        "band_dollars": round(band_dollars, 2),
        "total_value": round(total, 2),
        "coverage_target": 0.80,
        # >1 means diversification is genuinely removing vol; 1 = none removed
        "diversification_ratio": (round(weighted_avg_sigma / sigma_p, 2)
                                  if sigma_p > 0 else None),
        "hhi": round(sum(wt ** 2 for _, wt in weights_total), 3),
        "effective_positions": (round(1 / sum(wt ** 2 for _, wt in weights_total), 1)
                                if weights_total else None),
        "portfolio_beta": beta,
        "window_days": WINDOW,
        "excluded": excluded,
    }
