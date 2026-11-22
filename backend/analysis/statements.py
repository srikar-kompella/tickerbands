"""
analysis/statements.py — pick the freshest COMPLETE source per statement.

The income statement, cash-flow statement and balance sheet update on
different schedules and in different feeds (2026-11-21: Yahoo had NVDA's Q3
income statement but not its Q3 cash flow or balance sheet; the SEC's XBRL
had neither until the 10-Q is filed). Treating "statements" as one unit
produced "TTM through Oct 31" over a cash-flow figure that ran through July.

Rule: for each statement, use the source whose latest COMPLETE period is
newest (SEC wins ties — it's the filed number). Every merged field keeps the
period and source of the statement it came from, so the report can label
each figure and warn only the ones that lag.
"""
from __future__ import annotations

from typing import Any

INCOME_KEYS = ("revenue", "gross_profit", "gross_profit_derived", "cost_of_revenue",
               "operating_income", "pretax_income",
               "net_income", "eps_diluted", "ebit", "eff_tax_rate", "tax")
CASHFLOW_KEYS = ("ocf", "capex", "fcf", "dividends_paid", "acquisitions")
STATEMENTS = ("income", "cashflow", "balance")


def _complete(s: dict | None, which: str) -> bool:
    if not s or not s.get("available"):
        return False
    t = s.get("ttm") or {}
    if which == "income":
        return t.get("revenue") is not None and t.get("net_income") is not None
    if which == "cashflow":
        return t.get("fcf") is not None
    return (s.get("balance") or {}).get("cash_st") is not None or \
        (s.get("balance") or {}).get("cash") is not None


def _period(s: dict | None, which: str) -> str:
    return str(((s or {}).get("periods") or {}).get(which)
               or (s or {}).get("latest_quarter_end") or "")


def _label(s: dict, which: str) -> str:
    if (s.get("source") or "").startswith("SEC"):
        f = s.get("cashflow_filing") if which == "cashflow" else s.get("filing")
        return (f"SEC filing ({f['form']} filed {f['filed']})" if f else "SEC XBRL")
    return s.get("source") or "statements"


def _newer_quarter(a: str, b: str) -> bool:
    """True only if `a` is a LATER QUARTER than `b`. Feeds label the same
    quarter differently (Yahoo month-end 2026-07-31 vs NVDA's fiscal
    2026-07-26 in its filing) — within 20 days is the same quarter, and then
    the earlier source in priority order (SEC) keeps it."""
    import datetime as _dt
    if not a:
        return False
    if not b:
        return True
    try:
        return (_dt.date.fromisoformat(a[:10]) - _dt.date.fromisoformat(b[:10])).days > 20
    except ValueError:
        return a > b


def merge(sources: list[dict | None]) -> dict[str, Any] | None:
    """sources in priority order (SEC first). → merged statements dict with
    `periods`, `period_sources` and per-statement `chosen` provenance."""
    cands = [s for s in sources if s and s.get("available")]
    if not cands:
        return None
    chosen: dict[str, dict] = {}
    for which in STATEMENTS:
        best = None
        for s in cands:
            if not _complete(s, which):
                continue
            if best is None or _newer_quarter(_period(s, which), _period(best, which)):
                best = s
        if best is not None:
            chosen[which] = best
    if not chosen:
        return None

    inc, cf, bal = chosen.get("income"), chosen.get("cashflow"), chosen.get("balance")
    ttm: dict[str, Any] = {}
    if inc:
        ttm.update({k: (inc.get("ttm") or {}).get(k) for k in INCOME_KEYS})
    if cf:
        ttm.update({k: (cf.get("ttm") or {}).get(k) for k in CASHFLOW_KEYS})
    quarters = []
    for q in (inc or {}).get("quarters") or []:
        q = dict(q)
        # a quarter's FCF only from the cash-flow source, for a period it covers
        if cf is not inc:
            match = next((x for x in (cf.get("quarters") or [])
                          if x.get("end") == q.get("end")), None) if cf else None
            q["fcf"] = match.get("fcf") if match else None
        quarters.append(q)
    periods = {w: (_period(chosen[w], w) or None) if w in chosen else None
               for w in STATEMENTS}
    labels = {w: _label(chosen[w], w) for w in chosen}
    # Fiscal-year series: the SEC's, but only when its own income statement
    # is complete — the revenue tag that fails to give a TTM (banks file
    # revenue under other lines) gives an unreliable FY series too. Otherwise
    # the series of the source the income statement came from.
    annual_src = next((s for s in cands if (s.get("source") or "").startswith("SEC")
                       and s.get("annual") and _complete(s, "income")), None) \
        or (inc if inc and inc.get("annual") else None) \
        or next((s for s in cands if s.get("annual")), None)
    annual = (annual_src or {}).get("annual") or []
    ends = [p for p in periods.values() if p]
    return {
        "available": True,
        "ttm": ttm, "quarters": quarters, "annual": annual,
        "annual_source": _label(annual_src, "income") if annual_src else None,
        "balance": dict((bal or {}).get("balance") or {}),
        # weighted-average share counts travel with the income statement
        "shares": dict((inc or {}).get("shares") or {}),
        "bank_like": any(bool(s.get("bank_like")) for s in cands),
        "periods": periods,
        "period_sources": labels,
        "latest_quarter_end": min(ends) if ends else None,
        "source": "; ".join(f"{w}: {labels[w]}" for w in STATEMENTS if w in labels),
        "as_of": max(str(s.get("as_of") or "") for s in cands) or None,
    }
