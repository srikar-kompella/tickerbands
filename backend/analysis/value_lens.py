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


def _money(v: float | None, ccy: str = "USD") -> str:
    """$1.2B — or 'TWD 1.2B' for a filer reporting in another currency."""
    if v is None:
        return "—"
    a, sign = abs(v), ("−" if v < 0 else "")
    cur = "$" if (ccy or "USD").upper() == "USD" else f"{ccy.upper()} "
    if a >= 1e12:
        return f"{sign}{cur}{a / 1e12:.2f}T"
    if a >= 1e9:
        return f"{sign}{cur}{a / 1e9:.1f}B"
    return f"{sign}{cur}{a / 1e6:.1f}M"


def _cagr(newest: float | None, oldest: float | None, years: int) -> float | None:
    if not newest or not oldest or oldest <= 0 or newest <= 0 or years <= 0:
        return None
    return ((newest / oldest) ** (1 / years) - 1) * 100


def build_value_lens(market_cap: float | None,
                     stmts: dict | None, currency: str = "USD") -> dict[str, Any] | None:
    """market_cap in DOLLARS; stmts = financial_statements provider payload;
    `currency` = the currency the statements are reported in (amounts in
    the labels carry it). Returns the value-lens block or None when
    statements are unavailable."""
    if not stmts or not stmts.get("available"):
        return None
    ccy = (currency or "USD").upper()
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
    # same construction as the report (report_view): marketable equity
    # securities count as cash-like — the panel said EV $3.00T while the
    # report said $2.96T for NVDA on 11/21
    eq_sec = _f(bal.get("equity_securities")) or 0.0
    ev = None
    if market_cap is not None and debt is not None and cash is not None:
        ev = market_cap + debt - cash - eq_sec
        if ev < market_cap:
            warnings.append(
                "net cash position (EV < market cap) — balance-sheet staying "
                "power; you pay less for operations than the sticker price")

    # revenue CAGRs from actual reported FYs (labeled with real spans)
    revs = [_f(a.get("revenue")) for a in annual]
    cagr3 = _cagr(revs[0], revs[3], 3) if len(revs) >= 4 else None
    span5 = min(len(revs) - 1, 5)
    cagr5 = _cagr(revs[0], revs[span5], span5) if len(revs) >= 3 and span5 >= 2 else None

    def fy(i: int) -> str:
        """'FY ended 2025-12-31 ($90.2M)' — which years the CAGR spans. A row
        with no end date reads 'latest FY' / 'FY −2 (undated)', never
        'FY −0'."""
        end = str(annual[i].get("end") or "")[:10] if i < len(annual) else ""
        if end:
            name = f"FY ended {end}"
        else:
            name = "latest FY (undated)" if i == 0 else f"FY −{i} (undated)"
        return f"{name} ({_money(revs[i], ccy)})"

    cagr3_basis = (f"reported FY revenues, 3-yr span: {fy(3)} → {fy(0)}"
                   if cagr3 is not None else "reported FY revenues, 3-yr span")
    cagr5_basis = (f"reported FY revenues, {span5}-yr span: {fy(span5)} → {fy(0)}"
                   if cagr5 is not None else f"reported FY revenues, {span5}-yr span")

    # The CAGR ends at the last FISCAL YEAR; TTM figures run through the
    # latest quarter. ISTR 11/22: 3-yr CAGR −5.9% (FY2022 → FY2025) beside
    # TTM revenue growth of +27% — both true (the latest quarters include an
    # acquisition), but shown with no dates they read as a contradiction.
    rev_ttm = _f(ttm.get("revenue"))
    if rev_ttm and revs and revs[0] and revs[0] > 0 and (cagr3 is not None or cagr5 is not None):
        gap = rev_ttm / revs[0] - 1
        fy_end = str(annual[0].get("end") or "")[:10]
        if not (0.5 <= rev_ttm / revs[0] <= 2.0):
            # the two series cannot be the same revenue line — withhold
            warnings.append(
                f"revenue CAGR withheld — TTM revenue {_money(rev_ttm, ccy)} is "
                f"{gap * 100:+.0f}% vs the latest fiscal year"
                + (f" (ended {fy_end})" if fy_end else "")
                + f" {_money(revs[0], ccy)}; the fiscal-year series and the TTM figure "
                "look like different revenue definitions or sources")
            cagr3 = cagr5 = None
        elif abs(gap) >= 0.15:
            warnings.append(
                "revenue CAGR is measured on fiscal years through "
                + (fy_end or "the last 10-K")
                + f"; TTM revenue {_money(rev_ttm, ccy)} is {gap * 100:+.0f}% vs that year "
                f"({_money(revs[0], ccy)}) — the latest quarters (for example an "
                "acquisition) are not in the CAGR")

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
        acq_note = (f"~${acq/1e9:.1f}B cash paid for acquisitions over {n_acq} FYs "
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
        "ev": m(ev and ev / 1e6, "market cap + total debt − cash & short-term "
                                 "investments − marketable equity securities ($M)"),
        "net_cash": bool(ev is not None and market_cap is not None and ev < market_cap),
        "rev_cagr_3y": m(cagr3, cagr3_basis),
        "rev_cagr_5y": m(cagr5, cagr5_basis),
        "roic_ttm": m(roic_ttm, "EBIT×(1−eff tax) ÷ (debt+equity−cash)", approx=True),
        "roic_avg": m(roic_avg, f"same construction, avg of {n_roic} FYs "
                                "(current balance sheet)", approx=True),
        # the cash-flow line only: a deal paid in stock (ISTR's bank mergers)
        # never appears in it, so this is not "total acquisitions"
        "acquisitions_total": {**m(acq / 1e6 if n_acq else None,
                                   f"cash paid for acquisitions, net of cash acquired, "
                                   f"{n_acq} FYs — excludes deals paid in stock ($M)"),
                               "label": "Cash paid for acquisitions"},
        "readings": readings,
        "warnings": warnings,
    }


# acquisitions_total: a bank's "cash paid for acquisitions" line (JPM: $12.3B
# over 5 FYs) is mostly deposits/loan books taken over, not a corporate deal
# spend comparable with a company's — blanked with the other cash-flow rows
NOT_FOR_BANKS = ("fcf_ttm", "p_fcf", "p_fcf_avg", "ev", "roic_ttm", "roic_avg",
                 "payout_of_fcf_pct", "acquisitions_total")
BANK_NOTE = ("bank: free cash flow, P/FCF, enterprise value and ROIC are not shown — "
             "deposits and borrowings fund the loan book, they are not corporate debt")


def for_bank(vl: dict | None) -> dict | None:
    """The lens for a bank: the cash-flow and enterprise-value rows mean
    something else for a deposit-taker, so they are blanked with one note."""
    if not vl:
        return vl
    out = dict(vl)
    for k in NOT_FOR_BANKS:
        if isinstance(out.get(k), dict):
            out[k] = {**out[k], "value": None}
    out["net_cash"] = False
    out["ni_vs_fcf_flag"] = False
    drop = ("net income materially exceeds free cash flow", "net cash position",
            "dividends consume")
    out["warnings"] = [BANK_NOTE] + [w for w in out.get("warnings") or []
                                     if not w.startswith(drop)]
    out["readings"] = [r for r in out.get("readings") or []
                       if not r.startswith(("TTM free cash flow", "P/FCF", "ROIC"))]
    return out


# priced off market cap (US dollars) — wrong for an ADR whose share count is
# not in ADR units, and for statements in another currency
PRICE_MULTIPLES = ("p_fcf", "p_fcf_avg", "pe_avg", "ev")
# absolute amounts in the filing's currency
ABSOLUTE_AMOUNTS = ("fcf_ttm", "ev", "acquisitions_total")


def withhold_priced(vl: dict | None, why: str | None = None,
                    currency: str | None = None) -> dict | None:
    """The lens when market cap is withheld (`why`, e.g. an ADR) and/or the
    statements are reported in a non-USD `currency`: price multiples blank;
    for a foreign currency also the absolute amounts. Ratios of two amounts
    in the same currency (CAGR, ROIC~, payout of FCF) are kept."""
    if not vl:
        return vl
    ccy = (currency or "USD").upper()
    foreign = ccy != "USD"
    if not why and not foreign:
        return vl
    out = dict(vl)
    keys = set(PRICE_MULTIPLES) | (set(ABSOLUTE_AMOUNTS) if foreign else set())
    for k in keys:
        if isinstance(out.get(k), dict):
            out[k] = {**out[k], "value": None}
    out["net_cash"] = False
    notes = []
    if why:
        notes.append(f"price multiples withheld — {why}")
    if foreign:
        notes.append(f"reported in {ccy} — free cash flow, EV, acquisitions and every "
                     "multiple of the US-dollar market cap are not shown; growth and "
                     "return ratios are currency-neutral")
    drop = ("net cash position",)
    out["warnings"] = notes + [w for w in out.get("warnings") or [] if not w.startswith(drop)]
    out["readings"] = [r for r in out.get("readings") or [] if not r.startswith("P/FCF")]
    return out
