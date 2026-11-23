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


# ─── valuation_range: the main measure now vs its own five fiscal year-ends ────
# (docs/VERDICT_SPEC.md §4 and §6). The research session grades value from
# it; `grade_hint` is the arithmetic only. A blank with a reason beats a wrong
# number: fewer than MIN_POINTS year-end points → status no_data; an ADR, a
# non-USD filer or a withheld market cap → status withheld.

import datetime as _dt
import math as _math
import re

MIN_POINTS = 3
MAX_POINTS = 5
CHEAP_BELOW = 0.85            # now ≤ 85% of the median → cheap
EXPENSIVE_OVER = 1.5          # now > 1.5× the median (or above the high) → expensive
NORMALIZE_OVER = 2.0          # TTM profit / cash flow > 2× its average → normalize
PRICE_GAP_DAYS = 10           # a year-end price may come from up to 10 days before
SHARE_DRIFT_MAX = 2.0         # a year's share count this far from today's → point left out
OUTLIER_OVER = 5.0            # a year-end multiple > 5× the other years' median → excluded
_YEARS = {1: "one-year", 2: "two-year", 3: "three-year", 4: "four-year", 5: "five-year"}


def _fmt_measure(measure: str | None):
    """23.8x for a multiple, 3.1% for a distribution rate — one decimal."""
    unit = "%" if measure == "distribution_rate" else "x"
    return lambda v: f"{v:.1f}{unit}"

MEASURE_LABELS = {"p_fcf": "Price to free cash flow", "ev_ebit": "EV to EBIT",
                  "pe": "Price to earnings", "p_tbv": "Price to tangible book",
                  "p_sales": "Price to sales",
                  "distribution_rate": "Distribution rate (trailing 12 months, %)"}
_FY_METRIC = {"p_fcf": ("fcf", "free cash flow"), "pe": ("net_income", "net income"),
              "p_tbv": ("tangible_book", "tangible book"), "p_sales": ("revenue", "revenue")}
# yield-type measures: higher = cheaper (§4: cheap when the rate is ≥ 15%
# above its own median; expensive below its low or under two-thirds of it)
YIELD_CHEAP_OVER = 1.15
YIELD_EXPENSIVE_UNDER = 2 / 3


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def grade_hint(now: float | None, median: float | None, high: float | None,
               low: float | None = None, inverted: bool = False) -> str | None:
    """§4 arithmetic. For a multiple (lower = cheaper): cheap when now is at
    least 15% below the median; expensive when above the high or more than
    1.5× the median; else fair. `inverted` for a yield-type measure (a
    fund's distribution rate — higher = cheaper): cheap when the rate is at
    least 15% above its median; expensive when below its low or under
    two-thirds of the median; else fair."""
    if now is None or median is None or median <= 0:
        return None
    if inverted:
        if now < 0:
            return None
        if (low is not None and now < low) or now < YIELD_EXPENSIVE_UNDER * median:
            return "expensive"
        if now >= YIELD_CHEAP_OVER * median:
            return "cheap"
        return "fair"
    if (high is not None and now > high) or now > EXPENSIVE_OVER * median:
        return "expensive"
    if now <= CHEAP_BELOW * median:
        return "cheap"
    return "fair"


def _empty(measure: str | None, status: str, warnings: list[str], **extra) -> dict[str, Any]:
    return {"measure": measure, "label": MEASURE_LABELS.get(measure or "", None),
            "now": extra.pop("now", None), "now_basis": extra.pop("now_basis", None),
            "points": extra.pop("points", []), "median": None, "low": None, "high": None,
            "grade_hint": None, "normalized": False, "now_normalized": None,
            "normalized_grade_hint": None, "grade": None,
            "direction": ("higher_is_cheaper" if measure == "distribution_rate"
                          else "lower_is_cheaper"),
            "warnings": warnings, "status": status, **extra}


def _close_at(bars: list[dict], day: str) -> float | None:
    """The last close on or before `day`, at most PRICE_GAP_DAYS earlier."""
    best = None
    for b in bars:
        d = str(b.get("date") or "")[:10]
        if d and d <= day and b.get("close"):
            best = b
        elif d > day:
            break
    if not best:
        return None
    gap = (_dt.date.fromisoformat(day) - _dt.date.fromisoformat(str(best["date"])[:10])).days
    return float(best["close"]) if gap <= PRICE_GAP_DAYS else None


def split_factor(splits: list[dict] | None, after: str | None) -> float:
    """Product of the split ratios (new shares per old share) of every split
    dated after `after` — what a share count filed then must be multiplied
    by to be on today's share basis (prices are split-adjusted)."""
    f = 1.0
    for s in splits or []:
        try:
            r = float(s.get("ratio") or 0)
        except (TypeError, ValueError):
            continue
        if r > 0 and after and str(s.get("date") or "")[:10] > str(after)[:10]:
            f *= r
    return f


def _shares_today_basis(a: dict, splits: list[dict], ref_shares: float | None
                        ) -> tuple[float | None, str | None]:
    """(the fiscal year's diluted share count on today's share basis, why it
    was left out). A count filed before a later split is multiplied by that
    split; with no filing date the count closer to today's is taken
    (as filed, or split-adjusted). Further than SHARE_DRIFT_MAX from today's
    count → left out (an unreconciled split or share change)."""
    sh = _f(a.get("shares_diluted"))
    if not sh or sh <= 0:
        return None, "no fiscal-year share count"
    end = str(a.get("end") or "")[:10]
    filed = a.get("shares_filed")
    if filed:
        adj = sh * split_factor(splits, filed)
    else:
        f = split_factor(splits, end)
        adj = sh
        if f != 1.0:
            if not ref_shares:
                return None, "a stock split after it and no filing date to tell whether the count is restated"
            adj = min((sh, sh * f), key=lambda x: abs(_math.log(x / ref_shares)))
    if ref_shares and not (1 / SHARE_DRIFT_MAX <= adj / ref_shares <= SHARE_DRIFT_MAX):
        return None, (f"share count {adj / 1e6:,.0f}M is more than {SHARE_DRIFT_MAX:g}× away "
                      f"from today's {ref_shares / 1e6:,.0f}M (a split or share change the app "
                      "cannot reconcile)")
    return adj, None


def year_end_points(annual: list[dict], measure: str, bars: list[dict],
                    splits: list[dict] | None, ref_shares: float | None
                    ) -> tuple[list[dict], list[str]]:
    """Up to MAX_POINTS {fy_end, value} newest first for a stock or bank
    measure — price at the fiscal year-end ÷ that year's figure per share
    (share count on today's basis). Years whose figure is ≤ 0, or that have
    no price or no reconcilable share count, are left out with a warning."""
    key, name = _FY_METRIC[measure]
    pts: list[dict] = []
    warns: list[str] = []
    first_bar = str(bars[0]["date"])[:10] if bars else None
    for a in (annual or [])[:MAX_POINTS]:
        end = str(a.get("end") or "")[:10]
        if not end:
            warns.append("a fiscal year with no end date — left out")
            continue
        fig = _f(a.get(key))
        if fig is None:
            warns.append(f"FY ended {end}: no {name} figure — left out")
            continue
        if fig <= 0:
            warns.append(f"FY ended {end}: {name} ≤ 0 — the multiple is undefined; left out")
            continue
        px = _close_at(bars, end)
        if px is None:
            warns.append(f"FY ended {end}: no price within {PRICE_GAP_DAYS} days of the year-end"
                         + (f" (price history starts {first_bar})" if first_bar and end < first_bar else "")
                         + " — left out")
            continue
        sh, why = _shares_today_basis(a, splits or [], ref_shares)
        if sh is None:
            warns.append(f"FY ended {end}: {why} — left out")
            continue
        pts.append({"fy_end": end, "value": round(px / (fig / sh), 2),
                    "price": round(px, 2)})
        if a.get("shares_derived"):
            warns.append(f"FY ended {end}: share count derived as {a['shares_derived']} "
                         "(no share count filed without a class split)")
        filled = {k: v for k, v in (a.get("filled_from") or {}).items()
                  if k in (key, "shares_diluted")}
        if filled:
            warns.append(f"FY ended {end}: " + ", ".join(
                f"{'share count' if k == 'shares_diluted' else name} from {v}"
                for k, v in filled.items()) + " (not in the primary fiscal-year series)")
    return pts, warns


def distribution_points(bars: list[dict], dividends: list[dict], today: _dt.date
                        ) -> tuple[list[dict], list[str]]:
    """Trailing-12-month distribution rate (%) at each of the last five
    calendar year-ends: distributions ex-dated in (Dec 31 − 365 days, Dec 31]
    ÷ the close on Dec 31 (both split-adjusted, from the same history).
    A year-end less than a year after the first bar is left out."""
    pts: list[dict] = []
    warns: list[str] = []
    if not bars:
        return pts, ["no long price history"]
    # the same trailing-12-month count as the rate now: a regular payer's
    # 13th ex-date inside 365 days is last year's payment (report_view)
    from analysis.report_view import forward_distribution, trailing_distributions
    fd = forward_distribution(dividends or [])
    per_year = (fd["per_year"] if fd["per_year"] is not None
                and not (fd["why"] or "").startswith("payment schedule") else None)
    first = _dt.date.fromisoformat(str(bars[0]["date"])[:10])
    for y in range(today.year - 1, today.year - 1 - MAX_POINTS, -1):
        d = _dt.date(y, 12, 31)
        if first > d - _dt.timedelta(days=365):
            warns.append(f"{d.isoformat()}: price history starts {first.isoformat()} — no full "
                         "trailing year; left out")
            continue
        px = _close_at(bars, d.isoformat())
        if px is None:
            warns.append(f"{d.isoformat()}: no close near the year-end — left out")
            continue
        kept, _ = trailing_distributions(dividends or [], d, per_year)
        paid = sum(float(e.get("amount") or 0) for e in kept)
        pts.append({"fy_end": d.isoformat(), "value": round(paid / px * 100, 2),
                    "price": round(px, 2), "paid": round(paid, 4)})
    return pts, warns


_YEAR_WARN = re.compile(r"^(FY ended \d{4}-\d{2}-\d{2}|\d{4}-\d{2}-\d{2}): (.+)$")


def merge_year_warnings(warns: list[str]) -> list[str]:
    """The same per-year note repeated for several years → one line naming
    the years ("FY ended 2021-01-31, 2020-01-31: no price … — left out")."""
    out: list[str] = []
    groups: dict[str, list[str]] = {}
    for w in warns:
        m = _YEAR_WARN.match(w)
        if not m:
            out.append(w)
            continue
        head, rest = m.group(1), m.group(2)
        if rest not in groups:
            groups[rest] = []
            out.append(("\0", rest))          # placeholder, filled below
        groups[rest].append(head)
    merged = []
    for w in out:
        if isinstance(w, tuple):
            heads = groups[w[1]]
            if len(heads) > 1 and all(h.startswith("FY ended ") for h in heads):
                heads = ["FY ended " + ", ".join(h[9:] for h in heads)]
            merged.append(f"{', '.join(heads)}: {w[1]}")
        else:
            merged.append(w)
    return merged


def summarize_range(measure: str, now: float | None, now_basis: str | None,
                    points: list[dict], warnings: list[str],
                    ttm_metric: float | None = None,
                    annual_metric: list[float | None] | None = None,
                    inverted: bool = False, basis_note: str | None = None) -> dict[str, Any]:
    """The §6 object from the points: median / low / high and the grade hint
    on the reported figure. A year-end multiple more than OUTLIER_OVER× the
    median of the other years (a near-zero profit or cash-flow year) is left
    out of median / low / high (`excluded_points`) and named in a warning.
    When TTM profit or cash flow (`ttm_metric`) is more than double its
    fiscal-year average (`annual_metric`) and every one of those years is
    ≥ 0, the grade on the average is added (`now_normalized`,
    `normalized_grade_hint`) beside the reported one — a cycle peak reads
    better on the average, a changed business on the reported figure; the
    research session picks one and says why. A year below zero (a
    turnaround) has no meaningful average: no normalization."""
    out = _empty(measure, "ok", merge_year_warnings(list(warnings)), now=now,
                 now_basis=now_basis, points=points)
    if basis_note:
        out["points_basis"] = basis_note
    fmt = _fmt_measure(measure)
    name = _FY_METRIC.get(measure, (None, "the figure"))[1]
    if not inverted and len(points) >= MIN_POINTS:
        keep, dropped = [], []
        for i, p in enumerate(points):
            m_o = _median([q["value"] for j, q in enumerate(points) if j != i])
            (dropped if m_o > 0 and p["value"] > OUTLIER_OVER * m_o else keep).append(p)
        for p in dropped:
            out["warnings"].append(
                f"FY{p['fy_end'][:4]} (ended {p['fy_end']}) excluded: {name} near zero — "
                f"{fmt(p['value'])} at that year-end is more than {OUTLIER_OVER:g}× the "
                "other years' median")
        if dropped:
            points = keep
            out["points"] = keep
            out["excluded_points"] = dropped
    vals = [p["value"] for p in points]
    if len(vals) < MIN_POINTS:
        out["status"] = "no_data"
        out["warnings"].append(
            f"only {len(vals)} usable year-end point{'' if len(vals) == 1 else 's'} (fewer than "
            f"{MIN_POINTS}) — too few for an own-history comparison; the research note "
            "compares with peers instead")
        return out
    med = _median(vals)
    out.update({"median": round(med, 2), "low": round(min(vals), 2),
                "high": round(max(vals), 2)})
    if now is None:
        out["warnings"].append("the measure now is not available — no grade from the app")
    out["grade_hint"] = grade_hint(now, out["median"], out["high"], out["low"], inverted)
    xs = [x for x in (annual_metric or []) if x is not None]
    if now is not None and ttm_metric is not None and xs and min(xs) >= 0:
        avg = sum(xs) / len(xs)
        if avg > 0 and ttm_metric > NORMALIZE_OVER * avg:
            nn = round(now * ttm_metric / avg, 2)
            ng = grade_hint(nn, out["median"], out["high"], out["low"], inverted)
            out.update({"normalized": True, "now_normalized": nn, "normalized_grade_hint": ng})
            span = _YEARS.get(len(xs), f"{len(xs)}-year")
            g = out["grade_hint"]
            head = (f"current {name} is {ttm_metric / avg:.1f}× its {span} average; on that "
                    f"average the measure is {fmt(nn)} instead of {fmt(now)}")
            out["warnings"].append(
                f"{head} — the grade is {g} either way" if ng == g else
                f"{head} — reported grade {g}, normalized grade {ng}; the value grade is "
                "the less favorable of the two")
    # §4: the less favorable of the reported and the normalized grade
    from analysis.research_quality import combined_grade
    out["grade"] = combined_grade(out["grade_hint"], out["normalized_grade_hint"])
    return out


def build_valuation_range(kind: str, fields: dict, stmts: dict | None,
                          long_hist: dict | None, mc_withheld: str | None,
                          currency: str, today: _dt.date) -> dict[str, Any]:
    """valuation_range for the analysis payload. `kind`: "stock" / "bank" /
    "fund" (a new listing is graded as a stock — it will lack the points);
    `fields`: report_view fields (the measure now is the report's own
    figure, at the report price); `long_hist`: {"bars", "splits",
    "dividends"} — about six years of daily closes."""
    def fv(k: str) -> tuple[float | None, str | None]:
        f = fields.get(k) or {}
        v = _f(f.get("value")) if f.get("status") == "ok" else None
        return (round(v, 2) if v is not None and k not in ("market_cap", "price") else v,
                f.get("basis"))

    lh = long_hist if isinstance(long_hist, dict) and long_hist.get("available") else None
    bars = (lh or {}).get("bars") or []
    no_hist = ("no five-year daily price history from the provider"
               + (f" ({long_hist.get('error')})" if isinstance(long_hist, dict)
                  and long_hist.get("error") else ""))

    if kind == "fund":
        now, nb = fv("div_yield_trailing")
        if not lh:
            return _empty("distribution_rate", "no_data", [no_hist], now=now, now_basis=nb)
        pts, w = distribution_points(bars, lh.get("dividends") or [], today)
        if pts and all(p["value"] == 0 for p in pts) and not now:
            return _empty("distribution_rate", "no_data",
                          w + ["pays no distributions — the research note grades it on its "
                               "index's valuation or against peers"],
                          now=now, now_basis=nb, points=pts)
        return summarize_range("distribution_rate", now, nb, pts, w, inverted=True,
                               basis_note="trailing-12-month distributions ÷ close, at "
                                          "calendar year-ends")

    ccy = (currency or "USD").upper()
    measure = "p_tbv" if kind == "bank" else "p_fcf"
    ttm = (stmts or {}).get("ttm") or {}
    if kind != "bank":
        fcf = _f(ttm.get("fcf"))
        if fcf is None or fcf <= 0:
            measure = "pe"
            ni = _f(ttm.get("net_income"))
            if ni is None or ni <= 0:
                # loss-making: price to sales (§4) — never paused for that
                measure = "p_sales"
    if mc_withheld or ccy != "USD":
        why = []
        if mc_withheld:
            why.append(f"market cap withheld — {mc_withheld}")
        if ccy != "USD":
            why.append(f"statements reported in {ccy} — the price is in US dollars")
        return _empty(measure, "withheld", why + ["value grade paused — share count or "
                                                  "currency not reconciled"])
    if not stmts or not stmts.get("available"):
        return _empty(measure, "no_data", ["no financial statements"])
    mc, _ = fv("market_cap")
    px, _ = fv("price")

    def now_of(m: str) -> tuple[float | None, str | None]:
        if m == "p_sales":
            rev = _f(ttm.get("revenue"))
            if mc and rev and rev > 0:
                return round(mc / rev, 2), ("report market cap ÷ TTM revenue ("
                                            f"{(stmts.get('periods') or {}).get('income') or 'latest'})")
            return None, None
        return fv({"p_fcf": "p_fcf", "pe": "pe_ttm", "p_tbv": "p_tbv"}[m])

    now, nb = now_of(measure)
    warns: list[str] = []
    if measure == "pe" and kind != "bank":
        warns.append("free cash flow is negative or not available — measured on P/E")
    if measure == "p_sales":
        warns.append("free cash flow and net income are negative or not available — measured "
                     "on price to sales")
    annual = stmts.get("annual") or []
    if annual and not any("shares_diluted" in a for a in annual):
        return _empty(measure, "no_data", warns + ["the fiscal-year statements carry no share "
                                                   "counts — year-end multiples not computed"],
                      now=now, now_basis=nb)
    if not lh:
        return _empty(measure, "no_data", warns + [no_hist], now=now, now_basis=nb)
    ref = mc / px if mc and px else None
    pts, w = year_end_points(annual, measure, bars, lh.get("splits") or [], ref)
    # fewer than three usable year-ends because some years' figure was ≤ 0
    # (a negative measure): price to sales against its own history instead
    if (measure in ("p_fcf", "pe") and len(pts) < MIN_POINTS
            and any("≤ 0" in x for x in w)):
        s_pts, s_w = year_end_points(annual, "p_sales", bars, lh.get("splits") or [], ref)
        s_now, s_nb = now_of("p_sales")
        if len(s_pts) >= MIN_POINTS and s_now is not None:
            name = _FY_METRIC[measure][1]
            return summarize_range(
                "p_sales", s_now, s_nb, s_pts,
                warns + [f"{name} was ≤ 0 in some fiscal years — fewer than {MIN_POINTS} usable "
                         f"year-ends on {MEASURE_LABELS[measure].lower()}; measured on price to "
                         "sales"] + s_w,
                basis_note="price at each fiscal year-end ÷ that year's revenue per diluted "
                           "share (weighted average, split-adjusted)")
    key = _FY_METRIC[measure][0]
    ttm_key = {"fcf": "fcf", "net_income": "net_income"}.get(key)
    return summarize_range(
        measure, now, nb, pts, warns + w,
        ttm_metric=_f(ttm.get(ttm_key)) if ttm_key else None,
        annual_metric=[_f(a.get(key)) for a in annual[:MAX_POINTS]] if ttm_key else None,
        basis_note=("price at each fiscal year-end ÷ that year's "
                    f"{_FY_METRIC[measure][1]} per diluted share (weighted average, "
                    "split-adjusted)"))


# ─── rule_inputs: the app's arithmetic for the data-driven soft signals ────────
# (docs/VERDICT_SPEC.md §3 and §6b). Inputs and hints only — the research
# session writes soft_signals; the app checks the list against `fires`. A
# rule whose inputs are missing, withheld or flagged gets fires None with the
# reason (the data-quality gate). Filed SEC quarters only (`rule_quarters`).

RULE_LABELS = {
    "revenue_decline": "Revenue down year on year in both of the last two quarters",
    "op_margin_decline": "Operating margin down more than 3 points year on year in both quarters",
    "fcf_negative": "Free cash flow negative in both quarters",
    "receivables_vs_revenue": "Receivables growing more than 20 points faster than revenue",
    "inventory_vs_revenue": "Inventory growing more than 20 points faster than revenue",
    "ni_over_fcf": "Net income above free cash flow by more than 25%",
    "net_debt_ebitda": "Net debt above 3× EBITDA and higher than a year earlier",
    "interest_coverage": "Interest coverage below 3×",
    "dilution": "Diluted share count up more than 5% year on year",
    "efficiency_ratio": "Efficiency ratio above 70%",
    "deposit_decline": "Deposits down more than 5% in a quarter",
    "npl_rise": "Nonperforming loans up more than 50% and above 1% of loans",
    "charge_offs": "Net charge-offs rising two quarters",
    "capital_ratio": "Core capital ratio down more than 1 point",
    "nim_decline": "Net interest margin down two quarters",
    "cre_concentration": "Commercial real estate loans above 3× capital",
    "distribution_cut": "Distribution cut more than 10% versus the recent average",
    "assets_decline": "Fund assets down more than 30%",
}
FCF_SKIP_GROWTH = 0.25        # fcf_negative is skipped above 25% revenue growth
LISTED_YEAR_SESSIONS = 252    # dilution is not counted in the first four quarters


def _rule(key: str, values: list[dict], threshold: str, fires: bool | None,
          basis: str, warnings: list[str] | None = None, summary: str = "") -> dict[str, Any]:
    return {"key": key, "label": RULE_LABELS.get(key, key), "values": values,
            "threshold": threshold, "fires": fires, "basis": basis,
            "warnings": list(warnings or []), "summary": summary}


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:+.1f}%"


def _num(x: float | None, unit: str = "") -> str:
    return "—" if x is None else f"{x:+.1f}{unit}"


def _amt(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x / 1e9:,.2f}B" if abs(x) >= 1e9 else f"{x / 1e6:,.0f}M"


def _year_ago(qs: list[dict], q: dict) -> dict | None:
    """The filed quarter ending 350–380 days before `q`."""
    try:
        e = _dt.date.fromisoformat(str(q["end"])[:10])
    except (KeyError, ValueError):
        return None
    for o in qs:
        try:
            d = (e - _dt.date.fromisoformat(str(o["end"])[:10])).days
        except (KeyError, ValueError):
            continue
        if 350 <= d <= 380:
            return o
    return None


def _growth(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b <= 0:
        return None
    return a / b - 1


def _ttm_sum(qs: list[dict], start: int, key: str) -> float | None:
    """Sum of four consecutive filed quarters from index `start` (newest
    first); None when one is missing or the quarters are not consecutive."""
    win = qs[start:start + 4]
    if len(win) < 4:
        return None
    try:
        ds = [_dt.date.fromisoformat(str(q["end"])[:10]) for q in win]
    except (KeyError, ValueError):
        return None
    if any(not 75 <= (a - b).days <= 105 for a, b in zip(ds, ds[1:])):
        return None
    vals = [_f(q.get(key)) for q in win]
    return None if any(v is None for v in vals) else sum(vals)


def adjusted_net_income(q: dict) -> tuple[float | None, str | None]:
    """A quarter's GAAP net income less the one-time items the report
    flags (report_view: an income-tax benefit ≥ 10% of pretax profit → taxed
    at 21% instead; non-operating gains ≥ 5% of pretax income → removed,
    after tax). (figure, what was adjusted)."""
    from analysis.report_view import NON_OP_FLAG_SHARE, STATUTORY_TAX, TAX_BENEFIT_SHARE
    ni, pre = _f(q.get("net_income")), _f(q.get("pretax_income"))
    op, tax = _f(q.get("operating_income")), _f(q.get("tax"))
    if ni is None:
        return None, None
    if tax is not None and tax < 0 and pre is not None and pre > 0 \
            and -tax >= TAX_BENEFIT_SHARE * pre:
        return pre * (1 - STATUTORY_TAX), (f"income-tax benefit {-tax / 1e6:,.0f}M removed "
                                           f"(taxed at {STATUTORY_TAX * 100:.0f}%)")
    if pre is not None and op is not None and pre > 0 and (pre - op) / pre >= NON_OP_FLAG_SHARE:
        gain = (pre - op) * (1 - STATUTORY_TAX)
        return ni - gain, f"non-operating gains {(pre - op) / 1e6:,.0f}M removed (after tax)"
    return ni, None


def _two(qs: list[dict], fn) -> tuple[list[dict], list[bool | None], list[str]]:
    """Apply `fn(q, year_ago)` → (value dict, bool | None, warning | None) to
    the last two filed quarters."""
    vals, res, warns = [], [], []
    for q in qs[:2]:
        v, ok, w = fn(q, _year_ago(qs, q))
        vals.append({"period": q.get("end"), **v})
        res.append(ok)
        if w:
            warns.append(f"{q.get('end')}: {w}")
    return vals, res, warns


def _both(res: list[bool | None]) -> bool | None:
    if len(res) < 2 or any(r is None for r in res):
        return None
    return all(res)


def _stock_rules(qs: list[dict], basis: str, sessions_listed: int | None,
                 bank: bool = False) -> list[dict]:
    out: list[dict] = []

    def rev_of(q: dict | None) -> float | None:
        if not q:
            return None
        r = _f(q.get("revenue"))
        if r is None and bank:
            nii, noi = _f(q.get("net_interest_income")), _f(q.get("noninterest_income"))
            r = nii + noi if nii is not None and noi is not None else None
        return r

    def g_rev(q, ya):
        return _growth(rev_of(q), rev_of(ya))

    # revenue_decline
    def f_rev(q, ya):
        g = g_rev(q, ya)
        w = None if g is not None else ("no revenue a year earlier" if ya is None or rev_of(ya) is None
                                        else "no revenue for the quarter")
        return ({"revenue": rev_of(q), "revenue_year_ago": rev_of(ya),
                 "growth_pct": round(g * 100, 1) if g is not None else None},
                (g < 0) if g is not None else None, w)
    v, r, w = _two(qs, f_rev)
    out.append(_rule("revenue_decline", v, "growth < 0% in both quarters", _both(r), basis, w,
                     ", ".join(f"{x['period']} {_pct(x['growth_pct'] / 100 if x['growth_pct'] is not None else None)}"
                               for x in v)))

    if not bank:
        # op_margin_decline
        def f_om(q, ya):
            def m(x):
                oi, rv = _f((x or {}).get("operating_income")), rev_of(x)
                return oi / rv if oi is not None and rv else None
            a, b = m(q), m(ya)
            ch = (a - b) * 100 if a is not None and b is not None else None
            return ({"op_margin_pct": round(a * 100, 1) if a is not None else None,
                     "op_margin_year_ago_pct": round(b * 100, 1) if b is not None else None,
                     "change_pts": round(ch, 1) if ch is not None else None},
                    (ch < -3) if ch is not None else None,
                    None if ch is not None else "operating income or revenue not filed for both years")
        v, r, w = _two(qs, f_om)
        out.append(_rule("op_margin_decline", v, "change < −3 points in both quarters", _both(r),
                         basis, w, ", ".join(f"{x['period']} {_num(x['change_pts'], ' pts')}" for x in v)))

        # fcf_negative (skipped for a fast grower)
        def f_fcf(q, ya):
            fcf = _f(q.get("fcf"))
            return ({"fcf": fcf, "revenue_growth_pct": round(g * 100, 1)
                     if (g := g_rev(q, ya)) is not None else None},
                    (fcf < 0) if fcf is not None else None,
                    None if fcf is not None else "free cash flow for the quarter not filed")
        v, r, w = _two(qs, f_fcf)
        fires = _both(r)
        g0 = g_rev(qs[0], _year_ago(qs, qs[0])) if qs else None
        if fires and g0 is not None and g0 > FCF_SKIP_GROWTH:
            fires = False
            w = w + [f"skipped — revenue growing {g0 * 100:.0f}% year on year (> 25%)"]
        out.append(_rule("fcf_negative", v, "free cash flow < 0 in both quarters (skipped when "
                         "revenue grows > 25%)", fires, basis, w,
                         ", ".join(f"{x['period']} {_amt(x['fcf'])}" for x in v)))

        # receivables / inventory vs revenue
        for key, line, name in (("receivables_vs_revenue", "receivables", "receivables"),
                                ("inventory_vs_revenue", "inventory", "inventory")):
            def f_wc(q, ya, line=line, name=name):
                gw, gr = _growth(_f(q.get(line)), _f((ya or {}).get(line))), g_rev(q, ya)
                gap = (gw - gr) * 100 if gw is not None and gr is not None else None
                why = None
                if gap is None:
                    why = (f"no {name} line filed" if _f(q.get(line)) is None
                           else f"no {name} or revenue a year earlier")
                return ({f"{line}_growth_pct": round(gw * 100, 1) if gw is not None else None,
                         "revenue_growth_pct": round(gr * 100, 1) if gr is not None else None,
                         "gap_pts": round(gap, 1) if gap is not None else None},
                        (gap > 20) if gap is not None else None, why)
            v, r, w = _two(qs, f_wc)
            out.append(_rule(key, v, f"{name} growth − revenue growth > 20 points in both "
                             "quarters", _both(r), basis, w,
                             ", ".join(f"{x['period']} gap {_num(x['gap_pts'], ' pts')}" for x in v)))

        # ni_over_fcf (net income less flagged one-time items)
        def f_ni(q, ya):
            ni_adj, adj = adjusted_net_income(q)
            fcf = _f(q.get("fcf"))
            ok = None
            if ni_adj is not None and fcf is not None:
                ok = ni_adj > 0 and (fcf <= 0 or ni_adj > 1.25 * fcf)
            why = None if ok is not None else ("free cash flow for the quarter not filed"
                                               if fcf is None else "net income not filed")
            if adj:
                why = (why + "; " if why else "") + adj
            return ({"net_income": _f(q.get("net_income")), "net_income_adj": ni_adj, "fcf": fcf,
                     "ratio": round(ni_adj / fcf, 2) if ni_adj is not None and fcf and fcf > 0 else None},
                    ok, why)
        v, r, w = _two(qs, f_ni)
        out.append(_rule("ni_over_fcf", v, "adjusted net income > 1.25 × free cash flow in both "
                         "quarters", _both(r), basis + "; net income less flagged one-time items",
                         w, ", ".join(f"{x['period']} ratio {x['ratio']}" if x['ratio'] is not None
                                      else f"{x['period']} net income {_amt(x['net_income_adj'])}, "
                                           f"fcf {_amt(x['fcf'])}" for x in v)))

        # net_debt_ebitda (TTM EBITDA = operating income + D&A, four quarters)
        def nd_at(i):
            if len(qs) <= i:
                return None, None
            d, c = _f(qs[i].get("debt")), _f(qs[i].get("cash_st"))
            nd = d - c if d is not None and c is not None else None
            oi, da = _ttm_sum(qs, i, "operating_income"), _ttm_sum(qs, i, "d_and_a")
            eb = oi + da if oi is not None and da is not None else None
            return nd, eb
        nd0, eb0 = nd_at(0)
        ya_i = next((i for i, q in enumerate(qs) if qs and q is _year_ago(qs, qs[0])), None)
        nd1, eb1 = nd_at(ya_i) if ya_i is not None else (None, None)
        warns, fires = [], None
        if nd0 is None:
            warns.append("debt or cash not filed for the latest quarter")
        elif nd0 <= 0:
            fires = False
        elif eb0 is None:
            warns.append("four consecutive quarters of operating income and D&A not filed")
        elif eb0 <= 0:
            warns.append("trailing EBITDA ≤ 0 — the ratio is undefined")
        elif nd0 / eb0 <= 3:
            fires = False
        elif nd1 is None:
            warns.append("net debt a year earlier not filed")
        else:
            fires = nd0 > nd1
        out.append(_rule("net_debt_ebitda",
                         [{"period": qs[0]["end"] if qs else None, "net_debt": nd0, "ebitda_ttm": eb0,
                           "ratio": round(nd0 / eb0, 2) if nd0 is not None and eb0 and eb0 > 0 else None,
                           "net_debt_year_ago": nd1}],
                         "net debt > 3 × trailing EBITDA and net debt higher than a year earlier",
                         fires, basis, warns,
                         f"net debt {_amt(nd0)}, ratio "
                         f"{round(nd0 / eb0, 1) if nd0 is not None and eb0 and eb0 > 0 else '—'}"))

        # interest_coverage (TTM operating income ÷ TTM interest expense)
        oi, ie = _ttm_sum(qs, 0, "operating_income"), _ttm_sum(qs, 0, "interest_expense")
        warns, fires = [], None
        debt0 = _f(qs[0].get("debt")) if qs else None
        if ie is None:
            if debt0 == 0:
                fires = False
                warns.append("no interest expense filed and no debt")
            else:
                warns.append("no interest-expense line filed for four consecutive quarters")
        elif ie <= 0:
            fires = False
        elif oi is None:
            warns.append("operating income not filed for four consecutive quarters")
        else:
            fires = oi / ie < 3
        out.append(_rule("interest_coverage",
                         [{"period": qs[0]["end"] if qs else None, "operating_income_ttm": oi,
                           "interest_expense_ttm": ie,
                           "coverage": round(oi / ie, 2) if oi is not None and ie else None}],
                         "trailing operating income ÷ interest expense < 3", fires, basis, warns,
                         f"coverage {round(oi / ie, 1) if oi is not None and ie else '—'}x"))

    # dilution (latest quarter with a 3-month diluted count and one a year earlier)
    pair = next(((q, ya) for q in qs[:2] if _f(q.get("shares_diluted"))
                 and (ya := _year_ago(qs, q)) and _f(ya.get("shares_diluted"))), None)
    warns, fires, vals = [], None, []
    if pair:
        q, ya = pair
        g = _f(q["shares_diluted"]) / _f(ya["shares_diluted"]) - 1
        vals = [{"period": q["end"], "shares_diluted": q["shares_diluted"],
                 "shares_diluted_year_ago": ya["shares_diluted"], "growth_pct": round(g * 100, 1)}]
        fires = g > 0.05
        if sessions_listed is not None and sessions_listed < LISTED_YEAR_SESSIONS:
            fires = False
            warns.append("not counted in the first four quarters after a listing")
    else:
        warns.append("no quarterly diluted share count a year apart filed")
    out.append(_rule("dilution", vals, "diluted share count up > 5% year on year", fires, basis,
                     warns, f"{vals[0]['growth_pct']:+.1f}%" if vals else ""))

    if bank:
        def f_eff(q, ya):
            nie = _f(q.get("noninterest_expense"))
            nii, noi = _f(q.get("net_interest_income")), _f(q.get("noninterest_income"))
            rv = nii + noi if nii is not None and noi is not None else None
            e = nie / rv if nie is not None and rv and rv > 0 else None
            return ({"efficiency_pct": round(e * 100, 1) if e is not None else None},
                    (e > 0.70) if e is not None else None,
                    None if e is not None else "non-interest expense or revenue lines not filed")
        v, r, w = _two(qs, f_eff)
        out.append(_rule("efficiency_ratio", v, "non-interest expense ÷ (net interest income + "
                         "non-interest income) > 70% in both quarters", _both(r), basis, w,
                         ", ".join(f"{x['period']} {'—' if x['efficiency_pct'] is None else str(x['efficiency_pct']) + '%'}" for x in v)))
        d0 = _f(qs[0].get("deposits")) if qs else None
        d1 = _f(qs[1].get("deposits")) if len(qs) > 1 else None
        g = _growth(d0, d1)
        out.append(_rule("deposit_decline",
                         [{"period": qs[0]["end"] if qs else None, "deposits": d0,
                           "deposits_prior_quarter": d1,
                           "change_pct": round(g * 100, 1) if g is not None else None}],
                         "deposits down > 5% in the latest quarter",
                         (g < -0.05) if g is not None else None, basis,
                         [] if g is not None else ["deposits not filed for both quarter-ends"],
                         _pct(g)))
        for key in ("npl_rise", "charge_offs", "capital_ratio", "nim_decline", "cre_concentration"):
            out.append(_rule(key, [], "", None, basis,
                             ["not in the filed data consistently — the research note checks it"]))
    return out


def _distribution_rules(events: list[dict], today: _dt.date) -> list[dict]:
    from analysis.report_view import _sorted_events, dividend_frequency
    ev = [e for e in _sorted_events(events) if str(e["date"])[:10] <= today.isoformat()]
    basis = "distribution history (ex-dates, split-adjusted)"
    out = []
    if len(ev) < 2:
        out.append(_rule("distribution_cut", [], "", None, basis,
                         ["fewer than two distributions on record"]))
    else:
        per_year = dividend_frequency(ev)
        monthly = bool(per_year and per_year >= 10)
        months = 3
        last = ev[-1]
        d_last = _dt.date.fromisoformat(str(last["date"])[:10])
        lo = (d_last - _dt.timedelta(days=int(months * 30.5))).isoformat()
        # monthly payers: the 3 months before the latest; quarterly and other
        # payers: the four payments before it (one 6-month window held only
        # one or two payments — a single odd one decided the rule)
        prior = ([e for e in ev[:-1] if str(e["date"])[:10] > lo] if monthly
                 else ev[-5:-1])
        span = f"{months}-month average" if monthly else "average of the prior four payments"
        warns = []
        amts = [float(e["amount"]) for e in prior]
        if amts:
            med = _median(amts + [float(last["amount"])])
            special = [a for a in amts if a > 2 * med]
            if special:
                warns.append(f"{len(special)} payment(s) more than twice the median left out "
                             "of the average (a special or capital-gains distribution)")
                amts = [a for a in amts if a <= 2 * med]
        if not amts:
            out.append(_rule("distribution_cut", [{"period": last["date"], "latest": last["amount"]}],
                             f"latest < 90% of the {span}", None, basis,
                             warns + ["no earlier distribution to compare with the latest"]))
        else:
            avg = sum(amts) / len(amts)
            chg = float(last["amount"]) / avg - 1 if avg > 0 else None
            out.append(_rule("distribution_cut",
                             [{"period": str(last["date"])[:10], "latest": float(last["amount"]),
                               "average": round(avg, 6), "months": months if monthly else None,
                               "payments": len(amts),
                               "change_pct": round(chg * 100, 1) if chg is not None else None}],
                             f"latest < 90% of the {span}",
                             (chg < -0.10) if chg is not None else None, basis, warns,
                             f"latest {float(last['amount']):.4f} vs {span} "
                             f"{avg:.4f} ({_pct(chg)})"))
    out.append(_rule("assets_decline", [], "fund assets down > 30%", None,
                     "fund profile", ["no history of fund assets — the research note checks it"]))
    return out


def build_rule_inputs(kind: str, stmts: dict | None, dist_events: list[dict] | None,
                      today: _dt.date, sessions_listed: int | None = None,
                      currency: str = "USD") -> dict[str, Any]:
    """rule_inputs for the analysis payload (docs/VERDICT_SPEC.md §6b).
    `kind`: "stock" / "bank" / "fund". Stocks and banks: the filed SEC
    quarters (`stmts["rule_quarters"]`, newest first). Funds: the
    distribution history. Ratios of two amounts in one currency hold for a
    non-USD filer too."""
    if kind == "fund":
        rules = _distribution_rules(dist_events or [], today)
        last = next((r["values"][0]["period"] for r in rules if r["values"]), None)
        return {"kind": "fund", "rules": rules, "as_of": last, "filed": None,
                "status": "ok" if any(r["fires"] is not None for r in rules) else "no_data",
                "warnings": []}
    qs = [q for q in (stmts or {}).get("rule_quarters") or [] if isinstance(q, dict)]
    filing = (stmts or {}).get("rule_filing") or {}
    basis = (f"SEC filed quarters ({filing.get('form')} filed {filing.get('filed')})"
             if filing.get("filed") else "SEC filed quarters")
    warns = []
    if not qs:
        warns.append("no filed quarters from the SEC (XBRL) — the soft signals are not computed; "
                     "the research note checks them from the filings")
    # no filed quarters at all (a foreign filer, a mock feed): no rules, the
    # reason in `warnings` — the session checks the filings itself
    rules = _stock_rules(qs, basis, sessions_listed, bank=(kind == "bank")) if qs else []
    if currency and currency.upper() != "USD":
        warns.append(f"statements in {currency.upper()} — the ratios hold; amounts are not dollars")
    return {"kind": kind, "rules": rules, "as_of": qs[0]["end"] if qs else None,
            "filed": filing.get("filed"),
            "status": "ok" if qs else "no_data", "warnings": warns}
