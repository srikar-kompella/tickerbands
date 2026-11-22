"""
providers/sec_xbrl.py — financial statements AS FILED with the SEC (XBRL
company facts), keyless.

Why (2026-11-21 audit): the Yahoo statement feed updated NVDA's income
statement for Q3 but not its cash flow or balance sheet, and its
balance-sheet rows mixed categories (NVDA "marketable equity securities"
$51.2B vs $44.2B filed; VST's nuclear decommissioning trust counted as
securities). SEC company facts are the filed numbers themselves, with the
form, filing date and accession number attached — verifiable to the line.

Limit, stated honestly: XBRL appears when the 10-Q/10-K is FILED, which can
be days after the earnings press release. Until then the latest quarter is
simply not here; the composer then uses a newer source per statement if one
exists and otherwise says "does not include the latest quarter".

Derivation (standard): 10-Q cash-flow statements are year-to-date only, so
  TTM(E)      = YTD(E) + FY(prior) − YTD(same period a year earlier)
  quarter(E)  = 3-month fact if filed, else YTD(E) − YTD(previous quarter)
                (Q4 = FY − 9-month YTD)
Pure parsing (`normalize`) is separated from I/O (`fetch_statements`).
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from providers.base import ProviderError

name = "sec"

FLOW = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "pretax_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic"],
    "net_income": ["NetIncomeLoss", "ProfitLoss",
                   "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "tax": ["IncomeTaxExpenseBenefit"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquireProductiveAssets",
              "PaymentsToAcquirePropertyPlantAndEquipment"],
    "dividends_paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends",
                       "PaymentsOfOrdinaryDividends"],
    "acquisitions": ["PaymentsToAcquireBusinessesNetOfCashAcquired"],
}
INSTANT = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    # NVDA files current marketable debt securities as DebtSecuritiesCurrent
    # (found via /api/audit/sec-tags/NVDA on 2026-11-21)
    "st_investments": ["DebtSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
                       "MarketableSecuritiesCurrent", "ShortTermInvestments",
                       "AvailableForSaleSecuritiesCurrent"],
    "equity_securities": ["EquitySecuritiesFvNiCurrent", "EquitySecuritiesFvNi"],
    "debt_current": ["LongTermDebtCurrent", "DebtCurrent",
                     "LongTermDebtAndCapitalLeaseObligationsCurrent"],
    "short_borrowings": ["ShortTermBorrowings", "CommercialPaper"],
    "debt_noncurrent": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"],
    "debt_total": ["LongTermDebt",
                   "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
}
STATEMENT_OF = {"revenue": "income", "gross_profit": "income",
                "operating_income": "income", "pretax_income": "income",
                "net_income": "income", "tax": "income", "eps_diluted": "income",
                "ocf": "cashflow", "capex": "cashflow", "dividends_paid": "cashflow",
                "acquisitions": "cashflow"}


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s[:10])


def _days(f: dict) -> int:
    return (_d(f["end"]) - _d(f["start"])).days


def _facts(us: dict, tags: list[str], unit: str | None = None,
           duration: bool = True) -> tuple[str | None, list[dict]]:
    """Candidate tag with the most recent period end → its deduped facts
    (latest filing wins for the same period)."""
    best_tag, best, best_end = None, [], ""
    for tag in tags:
        units = ((us.get(tag) or {}).get("units") or {})
        rows = units.get(unit) if unit else (units.get("USD") or [])
        if not rows:
            continue
        rows = [r for r in rows if ("start" in r) == duration and r.get("val") is not None]
        if not rows:
            continue
        dedup: dict[tuple, dict] = {}
        for r in rows:
            k = (r.get("start"), r["end"])
            if k not in dedup or r.get("filed", "") > dedup[k].get("filed", ""):
                dedup[k] = r
        rows = list(dedup.values())
        end = max(r["end"] for r in rows)
        if end > best_end:
            best_tag, best, best_end = tag, rows, end
    return best_tag, best


def _find(rows: list[dict], end: dt.date, lo: int, hi: int,
          tol: int = 7) -> dict | None:
    c = [r for r in rows if abs((_d(r["end"]) - end).days) <= tol and lo <= _days(r) <= hi]
    return max(c, key=lambda r: r.get("filed", "")) if c else None


def _ytd(rows: list[dict], end: dt.date) -> dict | None:
    """Longest ≤ ~11-month fact ending at `end` (the fiscal YTD)."""
    c = [r for r in rows if abs((_d(r["end"]) - end).days) <= 3 and 60 <= _days(r) <= 300]
    return max(c, key=_days) if c else None


def ttm_at(rows: list[dict], end: dt.date) -> float | None:
    fy = _find(rows, end, 350, 380, tol=3)
    if fy:
        return float(fy["val"])
    y = _ytd(rows, end)
    if not y:
        return None
    fy_prior = _find(rows, _d(y["start"]) - dt.timedelta(days=1), 350, 380)
    y_prior = _find(rows, end - dt.timedelta(days=364), _days(y) - 14, _days(y) + 14, tol=10)
    if not fy_prior or not y_prior:
        return None
    return float(y["val"]) + float(fy_prior["val"]) - float(y_prior["val"])


def quarter_at(rows: list[dict], end: dt.date) -> float | None:
    q = _find(rows, end, 80, 100, tol=3)
    if q:
        return float(q["val"])
    cur = _ytd(rows, end) or _find(rows, end, 350, 380, tol=3)
    if not cur:
        return None
    if _days(cur) <= 100:
        return float(cur["val"])
    prev = [r for r in rows if r.get("start") == cur["start"]
            and 60 <= (end - _d(r["end"])).days <= 120]
    if not prev:
        return None
    p = max(prev, key=lambda r: r["end"])
    return float(cur["val"]) - float(p["val"])


def _quarter_ends(rows: list[dict]) -> list[dt.date]:
    ends = sorted({_d(r["end"]) for r in rows if 60 <= _days(r) <= 380}, reverse=True)
    out: list[dt.date] = []
    for e in ends:                       # collapse near-duplicate ends
        if not out or (out[-1] - e).days > 45:
            out.append(e)
    return out


def _instant_at(rows: list[dict], end: dt.date | None = None) -> tuple[float | None, str | None]:
    if not rows:
        return None, None
    if end is None:
        r = max(rows, key=lambda r: (r["end"], r.get("filed", "")))
    else:
        c = [r for r in rows if abs((_d(r["end"]) - end).days) <= 3]
        if not c:
            return None, None
        r = max(c, key=lambda r: r.get("filed", ""))
    return float(r["val"]), r["end"]


def normalize(facts: dict) -> dict[str, Any]:
    """companyfacts JSON → the app's statements shape (+ per-statement period
    ends and the filing each came from)."""
    us = (facts.get("facts") or {}).get("us-gaap") or {}
    flows: dict[str, list[dict]] = {}
    tags: dict[str, str | None] = {}
    for key, cands in FLOW.items():
        unit = "USD/shares" if key == "eps_diluted" else None
        tags[key], flows[key] = _facts(us, cands, unit)

    def latest_end(keys: list[str]) -> dt.date | None:
        ends = [max(_d(r["end"]) for r in flows[k]) for k in keys if flows.get(k)]
        return min(ends) if ends else None   # statement is as new as its oldest core line

    inc_end = latest_end(["revenue", "net_income"])
    cf_end = latest_end(["ocf", "capex"])

    def latest_filing(rows: list[dict], end: dt.date | None) -> dict | None:
        if not rows or not end:
            return None
        c = [r for r in rows if abs((_d(r["end"]) - end).days) <= 3]
        r = max(c, key=lambda r: r.get("filed", "")) if c else None
        return {"form": r.get("form"), "filed": r.get("filed"), "accn": r.get("accn")} if r else None

    ttm: dict[str, Any] = {}
    for key in FLOW:
        end = inc_end if STATEMENT_OF[key] == "income" else cf_end
        ttm[key] = ttm_at(flows[key], end) if end and flows[key] else None
    if ttm.get("capex") is not None:
        ttm["capex"] = -abs(ttm["capex"])
    if ttm.get("ocf") is not None and ttm.get("capex") is not None:
        ttm["fcf"] = ttm["ocf"] + ttm["capex"]
    else:
        ttm["fcf"] = None
    if ttm.get("dividends_paid") is not None:
        ttm["dividends_paid"] = abs(ttm["dividends_paid"])
    ttm["ebit"] = ttm.get("operating_income")
    ttm["eff_tax_rate"] = (ttm["tax"] / ttm["pretax_income"]
                           if ttm.get("tax") is not None and ttm.get("pretax_income") else None)

    quarters = []
    if inc_end:
        ends = [e for e in _quarter_ends(flows.get("revenue") or flows.get("net_income") or [])
                if e <= inc_end][:5]
        for e in ends:
            ocf_q = quarter_at(flows["ocf"], e) if flows.get("ocf") else None
            cap_q = quarter_at(flows["capex"], e) if flows.get("capex") else None
            quarters.append({
                "end": e.isoformat(),
                "revenue": quarter_at(flows["revenue"], e) if flows.get("revenue") else None,
                "gross_profit": quarter_at(flows["gross_profit"], e) if flows.get("gross_profit") else None,
                "operating_income": quarter_at(flows["operating_income"], e) if flows.get("operating_income") else None,
                "pretax_income": quarter_at(flows["pretax_income"], e) if flows.get("pretax_income") else None,
                "net_income": quarter_at(flows["net_income"], e) if flows.get("net_income") else None,
                "eps_diluted": quarter_at(flows["eps_diluted"], e) if flows.get("eps_diluted") else None,
                "fcf": ((ocf_q - abs(cap_q)) if ocf_q is not None and cap_q is not None
                        and cf_end and e <= cf_end else None),
            })

    annual = []
    fy_rows = sorted([r for r in (flows.get("revenue") or []) if 350 <= _days(r) <= 380],
                     key=lambda r: r["end"], reverse=True)
    for r in fy_rows[:5]:
        e = _d(r["end"])

        def fy(key):
            f = _find(flows.get(key) or [], e, 350, 380, tol=3)
            return float(f["val"]) if f else None
        ocf_a, cap_a = fy("ocf"), fy("capex")
        pre_a, tax_a = fy("pretax_income"), fy("tax")
        annual.append({
            "end": r["end"], "revenue": float(r["val"]), "net_income": fy("net_income"),
            "ebit": fy("operating_income"),
            "eff_tax_rate": (tax_a / pre_a if tax_a is not None and pre_a else None),
            "fcf": (ocf_a - abs(cap_a)) if ocf_a is not None and cap_a is not None else None,
            "acquisitions": -abs(fy("acquisitions")) if fy("acquisitions") is not None else None,
        })

    inst: dict[str, list[dict]] = {}
    for key, cands in INSTANT.items():
        tags[key], inst[key] = _facts(us, cands, duration=False)
    cash, bal_end = _instant_at(inst["cash"])
    bal_date = _d(bal_end) if bal_end else None

    def at(key):
        return _instant_at(inst[key], bal_date)[0] if inst.get(key) and bal_date else None
    st_inv, eq_sec = at("st_investments"), at("equity_securities")
    nonc, cur, shortb = at("debt_noncurrent"), at("debt_current"), at("short_borrowings")
    total = at("debt_total")
    # long-term debt: total (incl. current maturities) when filed, else
    # noncurrent + current. A short-term line alone is NOT the debt figure
    # (VST 11/21: ShortTermBorrowings = 0 rendered "debt $0" next to $21.1B
    # of filed long-term debt). Nothing found → None (withheld), never 0.
    if total is not None:
        long_part = total
    elif nonc is not None or cur is not None:
        long_part = (nonc or 0.0) + (cur or 0.0)
    else:
        long_part = None
    debt = long_part + (shortb or 0.0) if long_part is not None else None

    cash_st = (cash or 0.0) + (st_inv or 0.0) if cash is not None else None
    filing = latest_filing(flows.get("revenue") or flows.get("net_income") or [], inc_end)
    cf_filing = latest_filing(flows.get("ocf") or [], cf_end)
    return {
        "available": bool(inc_end or cf_end),
        "ttm": ttm, "quarters": quarters, "annual": annual,
        "balance": {"total_debt": debt, "cash": cash_st, "cash_st": cash_st,
                    "cash_only": cash, "st_investments": st_inv,
                    "equity_securities": eq_sec, "equity": at("equity"),
                    "as_of_date": bal_end,
                    "debt_note": "excludes lease liabilities"},
        "periods": {"income": inc_end.isoformat() if inc_end else None,
                    "cashflow": cf_end.isoformat() if cf_end else None,
                    "balance": bal_end},
        "latest_quarter_end": min([d for d in (inc_end, cf_end, bal_date) if d]).isoformat()
                              if (inc_end or cf_end or bal_date) else None,
        "filing": filing, "cashflow_filing": cf_filing,
        "tags": tags,
        "source": (f"SEC XBRL ({filing['form']} filed {filing['filed']})"
                   if filing else "SEC XBRL"),
        "as_of": dt.date.today().isoformat(),
    }


def fetch_statements(symbol: str) -> dict[str, Any]:
    from providers import edgar
    import requests
    cik = edgar._cik_for(symbol)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    try:
        r = requests.get(url, headers=edgar._ua(), timeout=30)
    except Exception as e:
        raise ProviderError("unavailable", f"SEC companyfacts: {e}")
    if r.status_code == 404:
        raise ProviderError("no_data", f"no XBRL financials for {symbol} (funds/ETFs/foreign filers)")
    if r.status_code != 200:
        raise ProviderError("unavailable", f"SEC companyfacts HTTP {r.status_code}")
    out = normalize(r.json())
    if not out["available"]:
        raise ProviderError("no_data", f"no usable XBRL statements for {symbol}")
    out["cik"] = cik
    return out


def fetch_recent_filings(symbol: str, since: str, forms: tuple = ("8-K", "10-Q", "10-K", "8-K/A")) -> dict[str, Any]:
    """Filings since `since` (ISO date) from the submissions API — the
    regulatory/legal pass's primary source (8-K items say what was filed)."""
    from providers import edgar
    cik = edgar._cik_for(symbol)
    sub = edgar._get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    recent = (sub.get("filings") or {}).get("recent") or {}
    out = []
    for form, date, acc, items, doc in zip(
            recent.get("form", []), recent.get("filingDate", []),
            recent.get("accessionNumber", []), recent.get("items", []),
            recent.get("primaryDocument", [])):
        if date < since[:10]:
            break                         # newest first
        if form in forms:
            out.append({"form": form, "date": date, "items": items or "",
                        "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                               f"{acc.replace('-', '')}/{doc}"})
    return {"available": True, "filings": out, "since": since[:10], "cik": cik}
