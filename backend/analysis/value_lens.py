"""
analysis/value_lens.py — statement-derived value metrics ("Everything Money"
foundational-metrics set, incorporated 2026-09-22).

These are COMPARISON LENSES, not signals: TTM vs multi-year average answers
"has the business fundamentally changed?"; FCF-vs-NI divergence is an
accounting-smell test; dividends÷FCF is the income-sleeve affordability
check. None of this feeds the Setup Score — the parent experiment's verdict
on prediction is not up for relitigating by a PDF.

Honesty rules baked in:
  * every multi-year figure carries the ACTUAL number of fiscal years used
    (free statement data gives ~4–5, not the PDF's idealized 5/10);
  * ROIC is labeled an approximation (EBIT×(1−eff tax) ÷ (debt+equity−cash));
  * a metric that can't be computed is None — never guessed.

Pure functions; the composer feeds cached provider payloads.
"""
from __future__ import annotations

from typing import Any

NI_FCF_DIVERGENCE = 0.30    # NI exceeding FCF by >30% → "do more research" flag
PAYOUT_WARN = 0.50          # dividends > 50% of FCF → affordability check flag


def _f(x) -> float | None:
    try:
        v = float(x)
        return v if v == v else None      # NaN guard
    except (TypeError, ValueError):
        return None


def _avg(vals: list[float | None]) -> tuple[float | None, int]:
    xs = [v for v in vals if v is not None]
    return (sum(xs) / len(xs), len(xs)) if xs else (None, 0)


def _cagr(newest: float | None, oldest: float | None, years: int) -> float | None:
    if not newest or not oldest or oldest <= 0 or newest <= 0 or years <= 0:
        return None
    return ((newest / oldest) ** (1 / years) - 1) * 100


def build_value_lens(market_cap: float | None,
                     stmts: dict | None) -> dict[str, Any] | None:
    """market_cap in DOLLARS; stmts = financial_statements provider payload.
    Returns the value-lens block or None when statements are unavailable."""
    if not stmts or not stmts.get("available"):
        return None
    ttm = stmts.get("ttm") or {}
    annual = stmts.get("annual") or []       # newest first
    bal = stmts.get("balance") or {}
    warnings: list[str] = []

    fcf_ttm = _f(ttm.get("fcf"))
    ni_ttm = _f(ttm.get("net_income"))
    divs_paid = _f(ttm.get("dividends_paid"))
    ann_fcf, n_fcf = _avg([_f(a.get("fcf")) for a in annual])
    ann_ni, n_ni = _avg([_f(a.get("net_income")) for a in annual])

    # price multiples on FCF / 5yr-avg earnings
    p_fcf = (market_cap / fcf_ttm) if market_cap and fcf_ttm and fcf_ttm > 0 else None
    p_fcf_avg = (market_cap / ann_fcf) if market_cap and ann_fcf and ann_fcf > 0 else None
    pe_avg = (market_cap / ann_ni) if market_cap and ann_ni and ann_ni > 0 else None

    # FCF vs Net Income divergence (PDF: "apprehensive if NI >> FCF")
    ni_fcf_flag = False
    if ni_ttm is not None and fcf_ttm is not None and ni_ttm > 0:
        if fcf_ttm <= 0 or (ni_ttm - fcf_ttm) / ni_ttm > NI_FCF_DIVERGENCE:
            ni_fcf_flag = True
            warnings.append(
                "net income materially exceeds free cash flow (TTM) — earnings "
                "may be accounting-flattered; check accruals/capex before "
                "trusting the P/E")

    # dividend affordability — the income-sleeve check
    payout_fcf = None
    if divs_paid is not None and fcf_ttm and fcf_ttm > 0:
        payout_fcf = divs_paid / fcf_ttm * 100
        if payout_fcf > PAYOUT_WARN * 100:
            warnings.append(
                f"dividends consume {payout_fcf:.0f}% of TTM free cash flow "
                "(>50%) — verify affordability through a rough patch")

    # enterprise value + net-cash flag
    debt, cash = _f(bal.get("total_debt")), _f(bal.get("cash"))
    ev = None
    if market_cap is not None and debt is not None and cash is not None:
        ev = market_cap + debt - cash
        if ev < market_cap:
            warnings.append(
                "net cash position (EV < market cap) — balance-sheet staying "
                "power; you pay less for operations than the sticker price")

    # revenue CAGRs from actual reported FYs (labeled with real spans)
    revs = [_f(a.get("revenue")) for a in annual]
    cagr3 = _cagr(revs[0], revs[3], 3) if len(revs) >= 4 else None
    span5 = min(len(revs) - 1, 5)
    cagr5 = _cagr(revs[0], revs[span5], span5) if len(revs) >= 3 and span5 >= 2 else None

    # approximate ROIC: NOPAT ÷ invested capital
    equity = _f(bal.get("equity"))
    roic_ttm = None
    ebit_ttm, tax_rate = _f(ttm.get("ebit")), _f(ttm.get("eff_tax_rate"))
    if (ebit_ttm is not None and equity is not None and debt is not None
            and cash is not None):
        invested = debt + equity - cash
        if invested > 0:
            nopat = ebit_ttm * (1 - (tax_rate if tax_rate is not None else 0.21))
            roic_ttm = nopat / invested * 100
    roics = []
    for a in annual:
        e, tr = _f(a.get("ebit")), _f(a.get("eff_tax_rate"))
        if e is not None and equity is not None and debt is not None and cash:
            inv = debt + equity - cash
            if inv > 0:
                roics.append(e * (1 - (tr if tr is not None else 0.21)) / inv * 100)
    roic_avg, n_roic = _avg(roics)

    # acquisitions vs organic growth (PDF's red flag)
    acq, n_acq = 0.0, 0
    for a in annual:
        v = _f(a.get("acquisitions"))
        if v is not None:
            acq += abs(v)
            n_acq += 1
    acq_note = None
    if n_acq and market_cap and acq > 0.05 * market_cap and (cagr3 or 0) < 5:
        acq_note = (f"~${acq/1e9:.1f}B spent on acquisitions over {n_acq} FYs "
                    "with low organic revenue growth — growth may be bought, "
                    "not built")
        warnings.append(acq_note)

    # ── readings: the PDF's comparison discipline, verbalized ─────────────────
    # Neutral, descriptive observations for the research layer. These feed the
    # Claude prompt (where the stance is made) — NEVER a computed buy/sell.
    readings: list[str] = []
    if ni_ttm is not None and ann_ni and ann_ni > 0:
        d = (ni_ttm / ann_ni - 1) * 100
        if abs(d) > 25:
            readings.append(
                f"TTM net income is {d:+.0f}% vs its {n_ni}-FY average — "
                + ("is the jump sustainable or one-time?" if d > 0
                   else "what deteriorated, and is it cyclical or structural?"))
    if fcf_ttm is not None and ann_fcf and ann_fcf > 0:
        d = (fcf_ttm / ann_fcf - 1) * 100
        if abs(d) > 25:
            readings.append(f"TTM free cash flow {d:+.0f}% vs its {n_fcf}-FY "
                            f"average — {'growing' if d > 0 else 'shrinking'} "
                            "cash generation")
    if p_fcf is not None and p_fcf_avg is not None and p_fcf_avg > 0:
        d = (p_fcf / p_fcf_avg - 1) * 100
        if abs(d) > 20:
            readings.append(
                f"P/FCF {p_fcf:.1f}x vs {p_fcf_avg:.1f}x on {n_fcf}-FY-avg FCF "
                f"— priced {'richer' if d > 0 else 'cheaper'} than its own "
                "cash-flow history")
    if roic_ttm is not None and roic_avg is not None and abs(roic_ttm - roic_avg) > 3:
        readings.append(
            f"ROIC~ {roic_ttm:.1f}% TTM vs {roic_avg:.1f}% multi-yr — capital "
            f"returns {'improving' if roic_ttm > roic_avg else 'decaying'} "
            "(approximate construction)")
    if cagr3 is not None and cagr5 is not None and abs(cagr3 - cagr5) > 3:
        readings.append(
            f"revenue growth {'accelerating' if cagr3 > cagr5 else 'slowing'}: "
            f"{cagr3:.1f}%/yr (3y) vs {cagr5:.1f}%/yr ({span5}y)")

    def m(value, basis, approx=False):
        return {"value": round(value, 2) if value is not None else None,
                "basis": basis, "approx": approx}

    return {
        "as_of": stmts.get("as_of"),
        "source": stmts.get("source", "yfinance statements"),
        "fcf_ttm": m(fcf_ttm and fcf_ttm / 1e6, "OCF − capex, trailing 4 quarters ($M)"),
        "p_fcf": m(p_fcf, "market cap ÷ TTM FCF"),
        "p_fcf_avg": m(p_fcf_avg, f"market cap ÷ avg FCF of last {n_fcf} FYs"),
        "pe_avg": m(pe_avg, f"market cap ÷ avg net income of last {n_ni} FYs"),
        "ni_vs_fcf_flag": ni_fcf_flag,
        "payout_of_fcf_pct": m(payout_fcf, "TTM dividends paid ÷ TTM FCF"),
        "ev": m(ev and ev / 1e6, "market cap + total debt − cash ($M)"),
        "net_cash": bool(ev is not None and market_cap is not None and ev < market_cap),
        "rev_cagr_3y": m(cagr3, "reported FY revenues, 3-yr span"),
        "rev_cagr_5y": m(cagr5, f"reported FY revenues, {span5}-yr span"),
        "roic_ttm": m(roic_ttm, "EBIT×(1−eff tax) ÷ (debt+equity−cash)", approx=True),
        "roic_avg": m(roic_avg, f"same construction, avg of {n_roic} FYs "
                                "(current balance sheet)", approx=True),
        "acquisitions_total": m(acq / 1e6 if n_acq else None,
                                f"sum of business purchases, {n_acq} FYs ($M)"),
        "readings": readings,
        "warnings": warnings,
    }
