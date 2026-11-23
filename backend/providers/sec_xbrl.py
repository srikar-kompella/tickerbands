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
    # GOOGL files no GrossProfit line (found via /api/audit/sec-tags/GOOGL,
    # 2026-11-22) — gross profit is then revenue − cost of revenue
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
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
    # banks stop filing the first tag and report cash under the bank lines
    # (ISTR: CashAndCashEquivalentsAtCarryingValue last filed for 2016)
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsAndFederalFundsSold", "CashAndDueFromBanks"],
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
    # tangible book (banks): equity − preferred − goodwill − other intangibles
    "goodwill": ["Goodwill"],
    "intangibles": ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet"],
    "goodwill_intangibles": ["IntangibleAssetsNetIncludingGoodwill"],
    # JPM stopped filing PreferredStockValue in 2009; its balance-sheet line is
    # PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount ($21.0B at
    # 2026-09-30) — the carrying value; the liquidation preference is last
    "preferred_equity": ["PreferredStockValue", "PreferredStockValueOutstanding",
                         "PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount",
                         "PreferredStockLiquidationPreferenceValue"],
}
# preferred shares issued / outstanding (unit "shares"): a filing that reports
# preferred shares but no preferred value cannot give a tangible book
PREFERRED_SHARES = ["PreferredStockSharesOutstanding", "PreferredStockSharesIssued"]
# intangibles are often filed only in the 10-K (JPM: FiniteLivedIntangible-
# AssetsNet last filed for 2025-12-31 beside a 2026-09-30 balance sheet) —
# the latest instant up to this many days before the balance-sheet date
INTANGIBLES_MAX_AGE_DAYS = 370
# lines only the soft-signal arithmetic reads (rule_quarters → rule_inputs,
# docs/VERDICT_SPEC.md §6b); quarterly flows and quarter-end instants
RULE_FLOW = {
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt",
                         "InterestAndDebtExpense", "InterestExpenseBorrowings"],
    "d_and_a": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                "DepreciationAmortizationAndAccretionNet", "Depreciation"],
    # banks: efficiency ratio = non-interest expense ÷ (net interest income +
    # non-interest income)
    "noninterest_expense": ["NoninterestExpense"],
    "net_interest_income": ["InterestIncomeExpenseNet"],
    "noninterest_income": ["NoninterestIncome"],
}
RULE_INSTANT = {
    "receivables": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent",
                    "AccountsNotesAndLoansReceivableNetCurrent"],
    "inventory": ["InventoryNet", "InventoryNetOfAllowancesCustomerAdvancesAndProgressBillings"],
    "deposits": ["Deposits"],                     # banks
}
RULE_QUARTERS = 9             # two quarters, the same two a year earlier, and TTM sums
# share counts (unit "shares"): weighted-average counts are filed without a
# share-class dimension, so they exist for multi-class companies too.
# Older years can sit under another tag (GOOGL 2020–21) — every candidate is
# read and the facts combined per period (`_facts_combined`)
SHARES_DILUTED = ["WeightedAverageNumberOfDilutedSharesOutstanding",
                  "WeightedAverageNumberOfShareOutstandingBasicAndDiluted"]
SHARES_BASIC = ["WeightedAverageNumberOfSharesOutstandingBasic"]
# a filer with these lines is a deposit-taking bank (fallback when the SIC
# code is not at hand)
BANK_TAGS = ("InterestIncomeExpenseNet", "InterestBearingDepositLiabilities", "Deposits")
BANK_SIC = {"6021", "6022", "6029", "6035", "6036"}
STATEMENT_OF = {"revenue": "income", "gross_profit": "income", "cost_of_revenue": "income",
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


def _facts_combined(us: dict, tags: list[str], unit: str | None = None,
                    duration: bool = True) -> list[dict]:
    """Every candidate tag's facts, combined per period: the first tag in
    the list that has a period wins it (latest filing within a tag), so a
    company that moved tags keeps its older years (share counts)."""
    out: dict[tuple, dict] = {}
    for tag in tags:
        units = ((us.get(tag) or {}).get("units") or {})
        rows = units.get(unit) if unit else (units.get("USD") or [])
        per: dict[tuple, dict] = {}
        for r in rows or []:
            if ("start" in r) != duration or r.get("val") is None:
                continue
            k = (r.get("start"), r["end"])
            if k not in per or r.get("filed", "") > per[k].get("filed", ""):
                per[k] = r
        for k, r in per.items():
            out.setdefault(k, r)
    return list(out.values())


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


def _instant_within(rows: list[dict], end: dt.date,
                    max_age: int = INTANGIBLES_MAX_AGE_DAYS) -> tuple[float | None, str | None]:
    """The fact at `end` (±3 days) when filed, else the latest instant up to
    `max_age` days before it (a line filed only at fiscal-year end)."""
    v, e = _instant_at(rows, end)
    if v is not None:
        return v, e
    c = [r for r in rows or [] if 0 < (end - _d(r["end"])).days <= max_age]
    if not c:
        return None, None
    r = max(c, key=lambda r: (r["end"], r.get("filed", "")))
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
    # no GrossProfit line in the filing → revenue − cost of revenue, same
    # period, labeled as derived
    ttm["gross_profit_derived"] = False
    if (ttm.get("gross_profit") is None and ttm.get("revenue") is not None
            and ttm.get("cost_of_revenue") is not None):
        ttm["gross_profit"] = ttm["revenue"] - ttm["cost_of_revenue"]
        ttm["gross_profit_derived"] = True

    def gross_q(e: dt.date, rev_q: float | None) -> float | None:
        gp = quarter_at(flows["gross_profit"], e) if flows.get("gross_profit") else None
        if gp is None and rev_q is not None and flows.get("cost_of_revenue"):
            cost = quarter_at(flows["cost_of_revenue"], e)
            gp = rev_q - cost if cost is not None else None
        return gp

    quarters = []
    if inc_end:
        ends = [e for e in _quarter_ends(flows.get("revenue") or flows.get("net_income") or [])
                if e <= inc_end][:5]
        for e in ends:
            ocf_q = quarter_at(flows["ocf"], e) if flows.get("ocf") else None
            cap_q = quarter_at(flows["capex"], e) if flows.get("capex") else None
            rev_q = quarter_at(flows["revenue"], e) if flows.get("revenue") else None
            quarters.append({
                "end": e.isoformat(),
                "revenue": rev_q,
                "gross_profit": gross_q(e, rev_q),
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

    # goodwill + other intangibles: the combined line when filed, else the
    # parts — each at the balance-sheet date, or the latest instant within
    # INTANGIBLES_MAX_AGE_DAYS before it (dated, so the report can say so)
    def near(key):
        if not inst.get(key) or not bal_date:
            return None, None
        return _instant_within(inst[key], bal_date)
    gw_int, gwi_end = near("goodwill_intangibles")
    gw_v, gw_end = near("goodwill")
    int_v, int_end = near("intangibles")
    # a combined line older than the parts at the balance-sheet date loses
    if gw_int is not None and gwi_end != bal_end and (gw_end == bal_end or int_end == bal_end):
        gw_int = None
    missing_parts: list[str] = []
    used: list[tuple[str, str | None]] = []
    if gw_int is not None:
        used = [("goodwill & intangibles", gwi_end)]
    elif gw_v is not None or int_v is not None:
        gw_int = (gw_v or 0.0) + (int_v or 0.0)
        missing_parts = [n for n, v in (("goodwill", gw_v), ("other intangibles", int_v))
                         if v is None]
        used = [(n, e) for n, v, e in (("goodwill", gw_v, gw_end),
                                       ("other intangibles", int_v, int_end)) if v is not None]
    else:
        missing_parts = ["goodwill", "other intangibles"]
    # components taken from an earlier instant than the balance sheet
    gwi_dates = {n: e for n, e in used if e and bal_end and e[:10] != bal_end[:10]}
    pref, _ = (_instant_at(inst["preferred_equity"], bal_date)
                      if inst.get("preferred_equity") and bal_date else (None, None))
    pref_shares = None
    if bal_date:
        _, ps_rows = _facts(us, PREFERRED_SHARES, "shares", duration=False)
        pref_shares = _instant_at(ps_rows, bal_date)[0] if ps_rows else None

    # per fiscal year (valuation_range, docs/VERDICT_SPEC.md §6): the
    # fiscal-year weighted-average diluted share count with the date it was
    # filed (a count filed before a later stock split is not restated for
    # it), and the tangible book at the year-end (equity − preferred −
    # goodwill & intangibles; None when a component cannot be dated to it)
    dil_rows = _facts_combined(us, SHARES_DILUTED, "shares")
    pref_any = bool(inst.get("preferred_equity"))
    for a in annual:
        e = _d(a["end"])
        f = _find(dil_rows, e, 350, 380, tol=3) if dil_rows else None
        a["shares_diluted"] = float(f["val"]) if f else None
        a["shares_filed"] = f.get("filed") if f else None
        if f is None and flows.get("eps_diluted") and a.get("net_income"):
            # no undimensioned count (GOOGL 2020–21 filed counts per share
            # class only): net income ÷ diluted EPS, both as filed that year
            ef = _find(flows["eps_diluted"], e, 350, 380, tol=3)
            if ef and float(ef["val"]) > 0 and a["net_income"] > 0:
                a["shares_diluted"] = a["net_income"] / float(ef["val"])
                a["shares_filed"] = ef.get("filed")
                a["shares_derived"] = "net income ÷ diluted EPS"
        eq_e = _instant_at(inst["equity"], e)[0] if inst.get("equity") else None
        gwi_e = _instant_within(inst["goodwill_intangibles"], e)[0] \
            if inst.get("goodwill_intangibles") else None
        if gwi_e is None and (inst.get("goodwill") or inst.get("intangibles")):
            g_e = _instant_within(inst["goodwill"], e)[0] if inst.get("goodwill") else None
            i_e = _instant_within(inst["intangibles"], e)[0] if inst.get("intangibles") else None
            gwi_e = (g_e or 0.0) + (i_e or 0.0) if g_e is not None or i_e is not None else None
        # no preferred line within a year of the year-end → none outstanding
        pref_e = (_instant_within(inst["preferred_equity"], e)[0] if pref_any else None) or 0.0
        a["tangible_book"] = (eq_e - pref_e - gwi_e
                              if eq_e is not None and gwi_e is not None else None)

    # weighted-average share counts for the latest income-statement quarter
    # (the 3-month fact; a 10-K's Q4 has none, then the fiscal-year average)
    shares: dict[str, Any] = {}
    if inc_end:
        for key, cands in (("diluted", SHARES_DILUTED), ("basic", SHARES_BASIC)):
            _, rows = _facts(us, cands, "shares")
            f = _find(rows, inc_end, 80, 100, tol=3) or _find(rows, inc_end, 350, 380, tol=3)
            if f:
                shares[key] = float(f["val"])
                shares.setdefault("period", f["end"])
                shares.setdefault("span", "quarter" if _days(f) <= 100 else "fiscal year")
    if shares:
        shares["source"] = "SEC filing (weighted average)"

    # deposit-taking bank? (interest margin + deposits lines filed recently)
    def recent(tag: str) -> bool:
        rows = [r for u in ((us.get(tag) or {}).get("units") or {}).values() for r in u]
        latest = max((r["end"] for r in rows), default="")
        ref = (inc_end or bal_date)
        return bool(latest and ref and (ref - _d(latest)).days <= 400)
    bank_like = recent(BANK_TAGS[0]) and (recent(BANK_TAGS[1]) or recent(BANK_TAGS[2]))

    rule_quarters = _rule_quarters(us, flows, inst, inc_end, cf_end)

    return {
        "available": bool(inc_end or cf_end),
        "rule_quarters": rule_quarters,
        # format marker: a cached copy without it is refetched once (composer)
        "rule_format": 3,
        "ttm": ttm, "quarters": quarters, "annual": annual,
        "balance": {"total_debt": debt, "cash": cash_st, "cash_st": cash_st,
                    "cash_only": cash, "st_investments": st_inv,
                    "equity_securities": eq_sec, "equity": at("equity"),
                    "goodwill_intangibles": gw_int,
                    # parts not found (treated as 0) and parts dated before
                    # the balance sheet (the latest filed instant)
                    "goodwill_intangibles_missing": missing_parts,
                    "goodwill_intangibles_dates": gwi_dates,
                    "preferred_equity": pref,
                    "preferred_found": pref is not None,
                    "preferred_shares": pref_shares,
                    "as_of_date": bal_end,
                    "debt_note": "excludes lease liabilities"},
        "shares": shares, "bank_like": bank_like,
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


def _rule_quarters(us: dict, flows: dict[str, list[dict]], inst: dict[str, list[dict]],
                   inc_end: dt.date | None, cf_end: dt.date | None) -> list[dict]:
    """Up to RULE_QUARTERS filed quarters, newest first, with what the
    soft-signal arithmetic needs (docs/VERDICT_SPEC.md §6b): revenue,
    operating / pretax / net income, tax, free cash flow (only through the
    cash-flow statement's end), interest expense, D&A, the quarter's diluted
    weighted-average share count (3-month fact only — a 10-K has none for
    Q4), and quarter-end receivables, inventory, cash + short-term
    investments and debt. Bank lines when filed. A line not filed → None."""
    if not inc_end:
        return []
    rf = {k: _facts(us, c)[1] for k, c in RULE_FLOW.items()}
    ri = {k: _facts(us, c, duration=False)[1] for k, c in RULE_INSTANT.items()}
    dil = _facts_combined(us, SHARES_DILUTED, "shares")

    def q(rows: list[dict] | None, e: dt.date) -> float | None:
        return quarter_at(rows, e) if rows else None

    def i(rows: list[dict] | None, e: dt.date) -> float | None:
        return _instant_at(rows, e)[0] if rows else None

    def debt_at(e: dt.date) -> float | None:
        total = i(inst.get("debt_total"), e)
        nonc, cur = i(inst.get("debt_noncurrent"), e), i(inst.get("debt_current"), e)
        shortb = i(inst.get("short_borrowings"), e)
        if total is not None:
            long_part = total
        elif nonc is not None or cur is not None:
            long_part = (nonc or 0.0) + (cur or 0.0)
        else:
            return None
        return long_part + (shortb or 0.0)

    out = []
    # quarter ends from net income (every filer files it each quarter; a
    # bank's revenue tag can stop — JPM's Revenues ran only to FY2025), else
    # revenue; only a run of quarterly ends (≤ 120 days apart) is used
    ends: list[dt.date] = []
    for rows in (flows.get("net_income"), flows.get("revenue")):
        cand = _quarter_ends(rows or [])[:RULE_QUARTERS]
        run = cand[:1]
        for e in cand[1:]:
            if (run[-1] - e).days > 120:
                break
            run.append(e)
        if len(run) > len(ends) or (run and ends and run[0] > ends[0]):
            ends = run
    if len(ends) < 2:
        return []
    for e in ends:
        ocf = q(flows.get("ocf"), e)
        cap = q(flows.get("capex"), e)
        cash = i(inst.get("cash"), e)
        st = i(inst.get("st_investments"), e)
        sh = _find(dil, e, 80, 100, tol=3) if dil else None
        out.append({
            "end": e.isoformat(),
            "revenue": q(flows.get("revenue"), e),
            "operating_income": q(flows.get("operating_income"), e),
            "pretax_income": q(flows.get("pretax_income"), e),
            "net_income": q(flows.get("net_income"), e),
            "tax": q(flows.get("tax"), e),
            "ocf": ocf,
            "fcf": ((ocf - abs(cap)) if ocf is not None and cap is not None
                    and cf_end and e <= cf_end else None),
            "interest_expense": (abs(v) if (v := q(rf["interest_expense"], e)) is not None
                                 else None),
            "d_and_a": q(rf["d_and_a"], e),
            "shares_diluted": float(sh["val"]) if sh else None,
            "receivables": i(ri["receivables"], e),
            "inventory": i(ri["inventory"], e),
            "cash_st": (cash + (st or 0.0)) if cash is not None else None,
            "debt": debt_at(e),
            "noninterest_expense": q(rf["noninterest_expense"], e),
            "net_interest_income": q(rf["net_interest_income"], e),
            "noninterest_income": q(rf["noninterest_income"], e),
            "deposits": i(ri["deposits"], e),
        })
    return out


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


# Forms the filing list carries → (description, red flag, group). 8-K
# descriptions come from its items (news_relevance.describe_8k); 10-Q / 10-K
# keep the bare form name. `group`: company (the issuer's own reports — the
# ones a move's cause can be in), insider (Form 4 / 144), ownership (13D/13G
# holders), fund (N-PORT, 497, N-CSR — a trust files these for every series)
FORM_INFO: dict[str, tuple[str | None, bool, str]] = {
    "8-K": (None, False, "company"), "8-K/A": (None, False, "company"),
    "10-Q": ("10-Q", False, "company"), "10-K": ("10-K", False, "company"),
    "10-Q/A": ("amended quarterly report (10-Q/A)", False, "company"),
    "10-K/A": ("amended annual report (10-K/A)", False, "company"),
    "NT 10-Q": ("late-filing notice — the 10-Q was not filed on time (NT 10-Q)", True, "company"),
    "NT 10-K": ("late-filing notice — the 10-K was not filed on time (NT 10-K)", True, "company"),
    "S-1": ("registration statement (S-1)", False, "company"),
    "S-1/A": ("registration statement, amended (S-1/A)", False, "company"),
    "424B4": ("final prospectus (424B4)", False, "company"),
    "4": ("insider trade (Form 4)", False, "insider"),
    "4/A": ("insider trade, amended (Form 4/A)", False, "insider"),
    "144": ("planned insider sale (Form 144)", False, "insider"),
    "SC 13D": ("5%+ holder that may seek influence (Schedule 13D)", False, "ownership"),
    "SC 13D/A": ("5%+ holder that may seek influence, amended (Schedule 13D/A)", False, "ownership"),
    "SC 13G": ("passive 5%+ holder (Schedule 13G)", False, "ownership"),
    "SC 13G/A": ("passive 5%+ holder, amended (Schedule 13G/A)", False, "ownership"),
    # EDGAR's form names since the 2024-25 beneficial-ownership changes
    "SCHEDULE 13D": ("5%+ holder that may seek influence (Schedule 13D)", False, "ownership"),
    "SCHEDULE 13D/A": ("5%+ holder that may seek influence, amended (Schedule 13D/A)", False, "ownership"),
    "SCHEDULE 13G": ("passive 5%+ holder (Schedule 13G)", False, "ownership"),
    "SCHEDULE 13G/A": ("passive 5%+ holder, amended (Schedule 13G/A)", False, "ownership"),
    "N-PORT-P": ("fund monthly portfolio holdings (N-PORT)", False, "fund"),
    "497": ("fund prospectus supplement (497)", False, "fund"),
    "N-CSR": ("fund annual shareholder report (N-CSR)", False, "fund"),
    "N-CSRS": ("fund semi-annual shareholder report (N-CSRS)", False, "fund"),
}
FILING_FORMS = tuple(FORM_INFO)
FILINGS_CAP = 25          # payload size: the 25 most recent (company reports kept first)


def describe_form(form: str) -> tuple[str, bool]:
    """(description, red flag) for a non-8-K form; unknown → (form, False)."""
    desc, red, _ = FORM_INFO.get(form, (None, False, "company"))
    return (desc or form), red


def form_group(form: str) -> str:
    return FORM_INFO.get(form, (None, False, "company"))[2]


def cap_filings(rows: list[dict], cap: int = FILINGS_CAP) -> list[dict]:
    """At most `cap` filings, newest first. The issuer's own reports
    (group company: 8-K, 10-Q, 10-K, NT, S-1…) are kept ahead of Form 4 /
    144 / 13G / fund filings, so a burst of insider filings cannot push the
    earnings 8-K off the list. Pure."""
    core = [r for r in rows if form_group(r.get("form", "")) == "company"][:cap]
    rest = [r for r in rows if form_group(r.get("form", "")) != "company"]
    kept = core + rest[:max(0, cap - len(core))]
    return sorted(kept, key=lambda r: str(r.get("date") or ""), reverse=True)


def fetch_recent_filings(symbol: str, since: str, forms: tuple = FILING_FORMS) -> dict[str, Any]:
    """Filings since `since` (ISO date) from the submissions API — the
    regulatory/legal pass's primary source (8-K items say what was filed).
    Capped at FILINGS_CAP (cap_filings); `omitted` counts what was left out."""
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
                        "accession": acc, "group": form_group(form),
                        "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                               f"{acc.replace('-', '')}/{doc}"})
    kept = cap_filings(out)
    sic = str(sub.get("sic") or "").strip() or None
    return {"available": True, "filings": kept, "since": since[:10], "cik": cik,
            "omitted": len(out) - len(kept),
            # which forms the list was filtered to: a cached copy without
            # this key predates the wider list and is refetched once
            "forms": list(forms),
            # SIC code of the filer (6021/6022/6029/6035/6036 = banks)
            "sic": sic, "sic_description": sub.get("sicDescription") or None,
            "is_bank": (sic in BANK_SIC) if sic else None}


def exhibit_from_index(index_html: str, base: str = "https://www.sec.gov") -> str | None:
    """The EX-99.1 document's URL from an EDGAR filing-index page (the
    "-index.htm" table: Seq | Description | Document | Type | Size). An 8-K's
    primary document is only the cover page; the earnings release is the
    exhibit. EX-99.1 first, else the first EX-99.* row. Pure."""
    import re
    best = None
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", index_html or "", flags=re.S | re.I):
        href = re.search(r'href="([^"]+)"', row, flags=re.I)
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S | re.I)]
        kind = next((c.upper() for c in cells if c.upper().startswith("EX-99")), None)
        if not href or not kind:
            continue
        path = href.group(1)
        if "/ix?doc=" in path:                       # inline-XBRL viewer link
            path = path.split("/ix?doc=", 1)[1]
        url = path if path.startswith("http") else base + path
        if kind in ("EX-99.1", "EX-99.01"):
            return url
        best = best or url
    return best


def fetch_release_exhibit(filing_url: str) -> dict[str, Any]:
    """Given an 8-K's primary-document URL, find its EX-99.1 (the earnings
    release itself) through the filing index. → {"exhibit_url": url | None}."""
    import re

    import requests

    from providers import edgar
    m = re.search(r"/Archives/edgar/data/(\d+)/(\d{18})/", filing_url or "")
    if not m:
        return {"exhibit_url": None}
    cik, acc = m.group(1), m.group(2)
    dashed = f"{acc[:10]}-{acc[10:12]}-{acc[12:]}"
    index = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{dashed}-index.htm"
    try:
        r = requests.get(index, headers=edgar._ua(), timeout=10)
    except Exception as e:
        raise ProviderError("unavailable", f"EDGAR filing index: {e}")
    if r.status_code != 200:
        raise ProviderError("unavailable", f"EDGAR filing index HTTP {r.status_code}")
    return {"exhibit_url": exhibit_from_index(r.text), "index_url": index}
