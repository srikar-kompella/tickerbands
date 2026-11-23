"""
analysis/report_view.py — every number the research report renders, computed
from ONE price basis, each with its source, as-of time, basis and formula,
then cross-checked before anything is shown.

Doctrine (from the NVDA 2026-11-21 incident — see docs/REPORT_FIELDS.md):
  * A blank with a visible warning is acceptable. A wrong number is not.
  * Every price-dependent figure is recomputed at render time from
    basis["price"]: market cap, P/E, P/FCF, EV, dividend yields, 52-week
    position, position value / P&L / weight.
  * Every field says what it is: TTM or latest quarter, forward or trailing,
    whose beta over what window, which session, confirmed or estimated.
  * `apply_checks()` re-derives the core identities from raw inputs; a field
    that fails is replaced by "—" plus a chip naming the two numbers that
    disagree (the 2026-09-10 TTM P/E/yield pattern, extended to every field).

Nothing here feeds the Setup Score or any computed buy/sell — the value
lens rule is unchanged. Pure functions; `now` is injectable.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from typing import Any

from analysis import market_clock as mc
from analysis import options_iv, research_quality
from analysis.fundamentals_ttm import DIVERGENCE_LIMIT
from analysis.news_relevance import MIN_HEADLINES_FOR_SENTIMENT, MIN_TAGGED_SOCIAL, signed

BAND_Z = 1.28
ANALYST_MAX_AGE_DAYS = 62     # Finnhub monthly snapshot older than ~2 months = stale
NON_OP_FLAG_SHARE = 0.05      # non-operating items ≥ 5% of pretax income → flag
TAX_BENEFIT_SHARE = 0.10      # an income-tax BENEFIT ≥ 10% of pretax profit → flag
NI_OVER_OP_RATIO = 1.25       # net income > 1.25 × operating income → flag
STATUTORY_TAX = 0.21          # the rate the "at a normal tax rate" figure uses
SHARE_COUNT_TOLERANCE = 0.05  # two share-count sources further apart than this disagree
DILUTED_TOLERANCE = 0.10      # diluted vs outstanding shares further apart than this
SHARE_COUNT_WITHHOLD = 0.50   # share counts this far apart → market cap withheld
RG_MAX_GAP_POINTS = 50.0      # vendor TTM vs filed-quarter revenue growth, in points
BETA_MIN_CORRELATION = 0.3    # |correlation to SPY| below this → beta is noise
LATEST_QUARTER_GAP_DAYS = 95  # last earnings date − latest quarter end > this → missing quarter

# share classes that are the same company (report on one, hold the other)
SHARE_CLASSES = {
    "GOOG": "GOOGL", "GOOGL": "GOOG", "FOX": "FOXA", "FOXA": "FOX",
    "NWS": "NWSA", "NWSA": "NWS", "UA": "UAA", "UAA": "UA",
    "BRK.A": "BRK.B", "BRK.B": "BRK.A", "BRK/A": "BRK/B", "BRK/B": "BRK/A",
    "BF.A": "BF.B", "BF.B": "BF.A", "LEN": "LEN.B", "LEN.B": "LEN",
}


# ─── formatting (one place, so tests can assert exact strings) ─────────────────

def money(v: float | None) -> str:
    return "—" if v is None else f"${v:,.2f}"


def signed_money(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{'−' if v < 0 else '+'}${abs(v):,.2f}"


def big_money(v: float | None) -> str:
    if v is None:
        return "—"
    a, sign = abs(v), ("−" if v < 0 else "")
    if a >= 1e12:
        return f"{sign}${a / 1e12:.2f}T"
    if a >= 1e9:
        return f"{sign}${a / 1e9:.1f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.1f}M"
    return f"{sign}${a:,.0f}"


def pct(v: float | None, digits: int = 2, signed: bool = False) -> str:
    if v is None:
        return "—"
    s = f"{abs(v):.{digits}f}%"
    if signed:
        return ("−" if v < 0 else "+") + s
    return ("−" if v < 0 else "") + s


def ratio(v: float | None, digits: int = 1, suffix: str = "x") -> str:
    return "—" if v is None else f"{v:.{digits}f}{suffix}"


def _f(x) -> float | None:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _field(key: str, label: str, value: Any = None, display: str = "—",
           source: str = "", as_of: str | None = None, basis: str = "",
           formula: str = "", warnings: list[str] | None = None,
           status: str = "ok", **extra) -> dict[str, Any]:
    if value is None and status == "ok" and display == "—":
        status = "no_data"
    return {"key": key, "label": label, "value": value,
            "display": display if status == "ok" else "—",
            "source": source, "as_of": as_of, "basis": basis,
            "formula": formula, "warnings": list(warnings or []),
            "status": status, **extra}


def _withhold(f: dict, why: str) -> None:
    f["status"] = "withheld"
    f["display"] = "—"
    if why not in f["warnings"]:
        f["warnings"].append(why)


# ─── statement freshness ───────────────────────────────────────────────────────

_STMT_NAME = {"income": "income statement", "cashflow": "cash flow",
              "balance": "balance sheet"}


def statement_coverage(stmts: dict | None, last_earnings: str | None,
                       release: dict | None = None) -> dict[str, Any]:
    """Does each statement include the quarter reported at the last earnings
    release? A statement whose period ends more than LATEST_QUARTER_GAP_DAYS
    before the release is missing it. Statements are judged SEPARATELY —
    feeds update them on different days."""
    st = stmts or {}
    q_end = st.get("latest_quarter_end")
    periods = st.get("periods") or ({w: q_end for w in _STMT_NAME} if q_end else {})
    out = {"latest_quarter_end": q_end, "last_earnings": last_earnings,
           "covers_latest": None, "warning": None, "lag": {}}
    if not periods or not last_earnings:
        return out
    try:
        e = dt.date.fromisoformat(last_earnings[:10])
    except ValueError:
        return out
    rel = ""
    if release and release.get("date"):
        rel = (f"; those results are only in the earnings release (8-K filed "
               f"{release['date']}) until the 10-Q/10-K is filed")
    lagging = []
    for w, p in periods.items():
        if not p:
            continue
        try:
            gap = (e - dt.date.fromisoformat(str(p)[:10])).days
        except ValueError:
            continue
        if gap > LATEST_QUARTER_GAP_DAYS:
            lagging.append((w, p))
            out["lag"][w] = (f"does not include the latest quarter — {_STMT_NAME[w]} "
                             f"ends {p}; earnings were reported {last_earnings[:10]}{rel}")
    known = [p for p in periods.values() if p]
    out["covers_latest"] = (not lagging) if known else None
    if lagging:
        parts = ", ".join(f"{_STMT_NAME[w]} ends {p}" for w, p in lagging)
        out["warning"] = (f"does not include the latest quarter — {parts}; earnings "
                          f"were reported {last_earnings[:10]}{rel}")
    return out


def dividend_frequency(events: list[dict]) -> int | None:
    """Payments per year inferred from the spacing of recent ex-dates."""
    ds = sorted({str(e.get("date"))[:10] for e in events if e.get("date")})
    if len(ds) < 3:
        return None
    recent = ds[-5:]
    gaps = []
    for a, b in zip(recent, recent[1:]):
        gaps.append((dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days)
    gaps.sort()
    med = gaps[len(gaps) // 2]
    for days, per_year in ((30, 12), (91, 4), (182, 2), (365, 1)):
        if abs(med - days) <= days * 0.25:
            return per_year
    return None


def _sorted_events(events: list[dict]) -> list[dict]:
    return sorted((e for e in events or [] if e.get("date") and _f(e.get("amount"))),
                  key=lambda e: str(e["date"])[:10])


def schedule_gaps(events: list[dict], n: int = 5) -> list[int]:
    """Days between the last `n` ex-dates, oldest gap first."""
    ds = sorted({str(e.get("date"))[:10] for e in events or [] if e.get("date")})[-n:]
    return [(dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days
            for a, b in zip(ds, ds[1:])]


def trailing_distributions(events: list[dict], asof: dt.date,
                           per_year: int | None = None) -> tuple[list[dict], int]:
    """Distributions that make up the trailing-12-month figure → (events,
    number left out as calendar drift).

    The window is ex-dates in (asof − 365 days, asof]. A regular payer's
    ex-date moves around inside its month, so 365 days can hold one payment
    too many: QQQI on 2026-11-22 had 13 monthly ex-dates between 2025-11-26
    and 2026-11-18, and the yield read 18.0% for a fund paying ~16.6%. When
    the payments in the window are evenly spaced (no special sitting between
    two regular ones) and outnumber the schedule's payments per year, only
    the latest `per_year` count."""
    lo = (asof - dt.timedelta(days=365)).isoformat()
    hi = asof.isoformat()
    win = [e for e in _sorted_events(events) if lo < str(e["date"])[:10] <= hi]
    if per_year and len(win) > per_year:
        ds = [dt.date.fromisoformat(str(e["date"])[:10]) for e in win]
        gaps = [(b - a).days for a, b in zip(ds, ds[1:])]
        if gaps and min(gaps) >= 0.6 * 365 / per_year:
            return win[-per_year:], len(win) - per_year
    return win, 0


FWD_MAX_DEVIATION = 0.50      # latest payment vs the median of the prior ones
FWD_GAP_RATIO = 1.75          # longest ÷ shortest gap between recent ex-dates
FWD_PAIR_AGREE = 0.10         # the two latest payments within 10% = one level
FWD_PRIOR_MAX_CV = 0.30       # earlier payments varying more than this → no "new level"


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def sustained_level(ev: list[dict], per_year: int | None) -> bool:
    """Have the two latest payments set a new level (a raise or a cut)?
    True only when they agree within FWD_PAIR_AGREE, both sit more than
    FWD_MAX_DEVIATION / 2 from the median of the payments before them (same
    side), and those earlier payments were steady — coefficient of variation
    (sample stdev ÷ mean) at most FWD_PRIOR_MAX_CV. `ev` sorted oldest first
    with positive amounts. Pure."""
    if len(ev) < 4:
        return False
    a, b = float(ev[-1]["amount"]), float(ev[-2]["amount"])
    if not b or abs(a / b - 1) > FWD_PAIR_AGREE:
        return False
    before = [float(e["amount"]) for e in ev[:-2][-max(per_year or 4, 4):]]
    if len(before) < 2:
        return False
    med, mean = _median(before), sum(before) / len(before)
    if not med or not mean:
        return False
    sd = math.sqrt(sum((x - mean) ** 2 for x in before) / (len(before) - 1))
    if sd / mean > FWD_PRIOR_MAX_CV:
        return False
    da, db = a / med - 1, b / med - 1
    band = FWD_MAX_DEVIATION / 2
    return abs(da) > band and abs(db) > band and (da > 0) == (db > 0)


def forward_distribution(events: list[dict]) -> dict[str, Any]:
    """Can the latest distribution be annualized? → {per_year, latest,
    latest_date, median_prior, n_prior, ok, why, note}.

    `latest × payments per year` is only a yield when the payer pays a
    similar amount on a steady schedule. UFO on 2026-11-22: a $0.004 payout
    × 2/yr rendered a 0.02% "forward yield" for a fund that paid $0.15 over
    the year. Not computed (ok=False, `why` says which test failed) when the
    schedule has no regular spacing, when the gaps between recent ex-dates
    differ by more than FWD_GAP_RATIO, or when the latest payment is more
    than FWD_MAX_DEVIATION away from the median of the payments before it."""
    ev = _sorted_events(events)
    out: dict[str, Any] = {"per_year": None, "latest": None, "latest_date": None,
                           "median_prior": None, "n_prior": 0, "ok": False,
                           "why": None, "note": None}
    if not ev:
        return out
    last = ev[-1]
    out.update({"latest": float(last["amount"]), "latest_date": str(last["date"])[:10]})
    per_year = dividend_frequency(ev)
    out["per_year"] = per_year
    gaps = schedule_gaps(ev)
    if per_year is None:
        out["why"] = ("payment schedule irregular"
                      + (f" (gaps of {min(gaps)}–{max(gaps)} days between the last "
                         f"{len(gaps) + 1} ex-dates)" if gaps else " (fewer than 3 ex-dates)"))
        return out
    if gaps and min(gaps) > 0 and max(gaps) / min(gaps) > FWD_GAP_RATIO:
        out["why"] = (f"payment schedule irregular (gaps of {min(gaps)}–{max(gaps)} days "
                      f"between the last {len(gaps) + 1} ex-dates)")
        return out
    prior = sorted(float(e["amount"]) for e in ev[:-1][-max(per_year, 4):])
    if len(prior) >= 2:
        mid = len(prior) // 2
        med = prior[mid] if len(prior) % 2 else (prior[mid - 1] + prior[mid]) / 2
        out.update({"median_prior": med, "n_prior": len(prior)})
        dev = out["latest"] / med - 1 if med else 0.0
        # a new level that has already been paid twice is a raise or a cut,
        # not a one-off (NVDA: $0.01 → $0.25, then $0.25 again) — but only
        # when the two latest payments agree with each other, BOTH sit
        # outside the band around the median of the payments before them,
        # and those earlier payments were steady (coefficient of variation ≤
        # FWD_PRIOR_MAX_CV). TQQQ 11/22: $0.171 then $0.156 after payments
        # of $0.049–$0.109 read as a "new level" — a 1.51% forward yield
        # beside 1.18% actually paid; its payouts simply vary.
        sustained = sustained_level(ev, per_year)
        if abs(dev) > FWD_MAX_DEVIATION and not sustained:
            out["why"] = (f"variable distributions — the latest (${out['latest']:.4g}, ex "
                          f"{out['latest_date']}) is {abs(dev) * 100:.0f}% "
                          f"{'below' if dev < 0 else 'above'} the median of the prior "
                          f"{len(prior)} (${med:.4g}) and has not repeated")
            return out
        if abs(dev) > FWD_MAX_DEVIATION / 2 and not sustained:
            out["note"] = (f"the latest distribution (${out['latest']:.4g}) is "
                           f"{abs(dev) * 100:.0f}% {'below' if dev < 0 else 'above'} the "
                           f"median of the prior {len(prior)} (${med:.4g}) — the forward "
                           "yield assumes it repeats")
    out["ok"] = True
    return out


_SCHEDULE_WORD = {12: "monthly", 4: "quarterly", 2: "semi-annual", 1: "annual"}

# ─── field groups (the UI renders a report generically, group by group) ────────

GROUPS = ("price", "fundamentals", "fund", "bank", "valuation", "rules", "options", "context",
          "position", "news", "research")
_GROUP_OF = {
    **{k: "price" for k in ("price", "after_hours", "day_move", "day_move_sigma", "band_next")},
    **{k: "fundamentals" for k in (
        "market_cap", "eps_ttm", "pe_ttm", "fcf_ttm", "p_fcf", "ev", "net_cash",
        "gross_margin", "operating_margin", "net_margin", "net_margin_q",
        "revenue_growth", "revenue_growth_q", "div_yield_forward", "div_yield_trailing")},
    **{k: "fund" for k in ("fund_schedule", "fund_distributions", "fund_expense_ratio",
                           "fund_aum", "fund_nav", "fund_category")},
    **{k: "bank" for k in ("bank_note", "p_tbv", "roe")},
    **{k: "options" for k in ("put_call", "atm_iv", "implied_move", "implied_vs_band")},
    **{k: "context" for k in ("beta", "correlation", "week52", "rv20", "social",
                              "analyst_mix", "analyst_actions", "earnings_next")},
    **{k: "position" for k in ("position_value", "position_pl", "position_day_pl",
                               "position_weight")},
    **{k: "news" for k in ("news_sentiment", "headlines", "regulatory", "move_cause")},
    **{k: "research" for k in ("claude_news", "claude_stance")},
    **{k: "valuation" for k in ("valuation_now", "valuation_points", "valuation_range")},
    "rule_inputs": "rules",
}

# ─── what kind of security is this ─────────────────────────────────────────────

SECURITY_KINDS = ("stock", "fund", "bank", "new_listing")
NEW_LISTING_SESSIONS = 21     # fewer completed sessions than this → new_listing
YOUNG_LISTING_SESSIONS = 60   # share counts are still settling below this
FUND_SUB_TYPES = {"ETF", "CEF", "ETN", "MF", "OEF", "UIT"}
FUND_ASSET_TYPES = {"ETF", "MUTUAL_FUND", "MUTUALFUND", "FUND"}
FUND_QUOTE_TYPES = {"ETF", "MUTUALFUND"}
BANK_SIC = {"6021", "6022", "6029", "6035", "6036"}
# name fallback for a fund (only with no sub-type, quote type or statements);
# "Trust" and "Shares" are not fund words — Northern Trust Corp, Vornado
# Realty Trust, "… Ordinary Shares", "American Depositary Shares" are stocks
FUND_NAME_RE = re.compile(r"\b(ETF|ETN|Fund|iShares)\b", re.I)
COMPANY_NAME_RE = re.compile(
    r"\b(Corp|Corporation|Inc|Incorporated|Bancorp|Holdings?|Ordinary|Depositary|REIT|Realty)\b",
    re.I)

# company-fundamentals rows a fund does not have; rows that mislead for a bank
COMPANY_ONLY = frozenset({
    "market_cap", "eps_ttm", "pe_ttm", "fcf_ttm", "p_fcf", "ev", "net_cash",
    "gross_margin", "operating_margin", "net_margin", "net_margin_q",
    "revenue_growth", "revenue_growth_q"})
BANK_SUPPRESSED = frozenset({"ev", "net_cash", "p_fcf", "fcf_ttm", "gross_margin"})
BANK_NOTE = ("EV, net cash, free cash flow, P/FCF and gross margin are not shown for a "
             "bank — deposits and borrowings fund the loan book, they are not corporate debt")


def classify_security(quote: dict | None, n_sessions: int | None,
                      has_statements: bool, profile: dict | None = None,
                      sic: str | None = None, bank_like: bool = False) -> dict[str, Any]:
    """→ {"kind": stock|fund|bank|new_listing, "is_fund", "is_bank",
    "new_listing", "is_new_listing", "why": [...]}. `kind` is what the
    payload carries as `security_kind`. Precedence (B2/C2 code against it):
    fund, then bank, then new_listing, else stock. `is_new_listing` (=
    `new_listing`) is set whatever the kind — a fund or bank that listed
    days ago is kind fund/bank with is_new_listing true.

    fund: the broker's asset type or sub-type (Schwab files ETFs as EQUITY
      with sub-type ETF), else Yahoo's quoteType, else — only when there is
      no sub-type, no quote type and no financial statements — ETF / ETN /
      Fund / iShares in the name, unless the name also says Corp, Inc,
      Bancorp, Holdings, Ordinary, Depositary, REIT or Realty ("Northern
      Trust Corp", "Vornado Realty Trust", "… Ordinary Shares" are stocks).
      Unknown → not a fund.
    bank: SEC SIC code 6021/6022/6029/6035/6036; only when the SIC is
      unknown, Yahoo's industry text ("Banks - …") or bank lines (net
      interest income + deposits) in the XBRL filing.
    new_listing: fewer than NEW_LISTING_SESSIONS completed sessions of
      history (`n_sessions` None = history unknown → not claimed)."""
    q, p = quote or {}, profile or {}
    name = str(q.get("name") or "")
    main =str(q.get("asset_type") or "").upper()
    sub = str(q.get("asset_sub_type") or "").upper()
    qt = str(p.get("quote_type") or "").upper()
    fund_why = None
    if main in FUND_ASSET_TYPES:
        fund_why = f"broker asset type {main}"
    elif sub in FUND_SUB_TYPES:
        fund_why = f"broker asset sub-type {sub}"
    elif qt in FUND_QUOTE_TYPES:
        fund_why = f"Yahoo quote type {qt}"
    elif (not sub and not qt and not has_statements
          and FUND_NAME_RE.search(name) and not COMPANY_NAME_RE.search(name)):
        fund_why = "fund word in the name and no financial statements"
    is_fund = fund_why is not None
    bank_why = None
    sic_s = str(sic).strip() if sic is not None else ""
    if not is_fund:
        industry = str(p.get("industry") or "")
        if sic_s in BANK_SIC:
            bank_why = f"SEC SIC code {sic_s}"
        elif sic_s:
            pass                          # a known non-bank SIC is the answer
        elif industry.lower().startswith("banks"):
            bank_why = f"Yahoo industry '{industry}'"
        elif bank_like:
            bank_why = "net interest income and deposits in the SEC filing"
    is_bank = bank_why is not None
    why = [w for w in (fund_why, bank_why) if w]
    new = n_sessions is not None and n_sessions < NEW_LISTING_SESSIONS
    if new:
        why.append(f"{n_sessions} completed session(s) of price history")
    kind = "fund" if is_fund else "bank" if is_bank else "new_listing" if new else "stock"
    return {"kind": kind, "is_fund": is_fund, "is_bank": is_bank, "new_listing": new,
            "is_new_listing": new, "why": why}


LISTED_YEAR_SESSIONS = 252    # fewer completed sessions since the first trade → listed under a year


def listing_facts(history: list[dict] | None, whole_life: bool,
                  long_bars: list[dict] | None, long_years: float | None,
                  today: dt.date | None = None,
                  public_filing: dict | None = None) -> dict[str, Any]:
    """docs/VERDICT_SPEC.md §3 — {"sessions_listed", "first_trade_date",
    "listed_under_a_year", "public_quarter_filed"}.

    The first trade is the first bar of a history shorter than what was
    asked for: the short daily history when `whole_life` (`history` = its
    completed bars), else the long history (`long_bars`, `long_years`
    asked) when its first bar is more than 30 days after the start asked
    for (an in-progress bar of today is not counted). Unknown →
    sessions_listed None and listed_under_a_year None (never claimed) — a
    long history that covers what was asked for is False. `public_filing` =
    {"form", "filed"} of the latest filed 10-Q / 10-K: public_quarter_filed
    True when filed after the first trade."""
    from analysis import vol_bands
    td = (today or dt.date.today()).isoformat()
    first = sessions = None
    under: bool | None = None
    if whole_life and history:
        first, sessions = str(history[0]["date"])[:10], len(history)
    elif long_bars:
        start = (dt.date.fromisoformat(td) - dt.timedelta(days=int((long_years or 6) * 365.25)))
        lb_first = str(long_bars[0]["date"])[:10]
        if dt.date.fromisoformat(lb_first) > start + dt.timedelta(days=30):
            first = lb_first
            sessions = len(vol_bands.completed_bars(long_bars))
        else:
            under = False
    if sessions is not None:
        under = sessions < LISTED_YEAR_SESSIONS
    pq = None
    if first and isinstance(public_filing, dict):
        form = str(public_filing.get("form") or "").upper()
        filed = str(public_filing.get("filed") or "")[:10]
        if form.startswith(("10-Q", "10-K")) and filed:
            pq = filed > first
    return {"sessions_listed": sessions, "first_trade_date": first,
            "listed_under_a_year": under, "public_quarter_filed": pq}


# ─── the builder ───────────────────────────────────────────────────────────────

def build(inp: dict[str, Any]) -> dict[str, Any]:
    """inp keys (all optional except symbol/basis): symbol, basis, move,
    band, signals, quote, vendor, vendor_meta, shares_out_m, statements,
    statements_meta, eps_check, dividends, options, news, headlines_view,
    social, recs, earnings, earnings_confirm, beta, position, account,
    claude_news, claude_stance, research_flags, news_window, now."""
    sym = inp["symbol"]
    b = inp.get("basis") or {}
    now = mc.to_ny(inp.get("now") or mc.now_ny())
    today = now.date()
    price = b.get("price")
    fields: dict[str, dict] = {}
    raw: dict[str, Any] = {"price": price}   # the inputs the checks re-derive from
    # what kind of security this is decides which rows exist (a fund has no
    # company fundamentals; EV / FCF mislead for a bank)
    sec = inp.get("security") or {}
    kind = sec.get("kind") if sec.get("kind") in SECURITY_KINDS else "stock"
    is_fund, is_bank = bool(sec.get("is_fund")), bool(sec.get("is_bank"))
    # new-listing behavior keys off this flag, never off `kind` (a fund or a
    # bank that listed days ago is kind fund / bank)
    is_new = bool(sec.get("is_new_listing", sec.get("new_listing")))
    profile = sec.get("profile") or {}

    def add(f: dict) -> None:
        k = f["key"]
        if is_fund and k in COMPANY_ONLY:
            return
        if is_bank and (k in BANK_SUPPRESSED
                        or (k == "operating_margin" and f.get("value") is None)):
            return
        f.setdefault("group", "fund" if is_fund and k.startswith("div_yield")
                     else _GROUP_OF.get(k, "context"))
        fields[k] = f

    # ── price, extended hours, day move ──────────────────────────────────────
    src_price = ("broker live regular-session quote" if b.get("price_kind") == "live"
                 else "daily bars — official regular-session close"
                 + (" (broker regular-session last print; bar not yet published)"
                    if b.get("synthetic_close") else ""))
    add(_field("price", "Price", price, money(price), src_price, b.get("price_label"),
               "the report price — every price-dependent figure uses it",
               "live regular-session last" if b.get("price_kind") == "live"
               else "official close of the last completed regular session",
               warnings=b.get("warnings") if price is None else [],
               status="withheld" if price is None and b.get("warnings") else "ok"))
    ah = b.get("after_hours")
    add(_field("after_hours", "Extended hours", ah and ah["price"],
               (f"{money(ah['price'])} ({pct(ah['change_pct'], 2, True)}) · {ah['label']}"
                if ah else "—"),
               "broker quote (extended session)", ah and ah.get("quote_time"),
               "shown separately; never used as the report price",
               "extended-hours last ÷ official close − 1"))
    word = b.get("move_word") or "—"
    session_label = "today" if word == "today" else f"{word} session"
    add(_field("day_move", "Day move", b.get("day_move_pct"),
               f"{pct(b.get('day_move_pct'), 2, True)} · {session_label}",
               "daily bars (official closes)" if b.get("price_kind") != "live"
               else "live quote vs prior official close",
               b.get("session_date"), f"session {b.get('session_date')} vs {b.get('prior_date')}",
               "price ÷ prior official close − 1 (prior close less any distribution "
               "going ex on the session date)",
               warnings=(["distribution date known but amount unpublished — move is "
                          "unadjusted"] if b.get("ex_div_pending") else []),
               session_date=b.get("session_date")))
    raw.update({"prior_close": b.get("prior_close"),
                "ex_div_amount": b.get("ex_div_amount") or 0.0})

    mv = inp.get("move") or {}
    sig_txt = "—"
    if mv.get("z") is not None:
        sig_txt = (f"{signed(mv['z'], 2, 'σ')} · {'outside' if mv.get('outside_band') else 'inside'}"
                   f" the 80% band ({session_label})")
    add(_field("day_move_sigma", "Move in σ", mv.get("z"), sig_txt,
               "TickerBands (realized vol)", b.get("session_date"),
               f"σ = 20-day realized vol through {b.get('prior_date')} (the band that "
               "applied to that session)",
               "move % ÷ daily σ; outside the band when |z| ≥ 1.28",
               outside_band=mv.get("outside_band"), daily_sigma_pct=mv.get("daily_sigma_pct")))

    band = inp.get("band") or {}
    band_label = ("80% expected range (rest of today's session)"
                  if b.get("price_kind") == "live" else "80% expected range (next session)")
    add(_field("band_next", band_label, band.get("half_width_pct"),
               (f"{money(band.get('low'))} – {money(band.get('high'))} "
                f"(±{band['half_width_pct']:.2f}%)" if band else "—"),
               f"TickerBands {band.get('engine', 'flat20')} engine", band.get("anchor_date"),
               f"centered on the official close of {band.get('anchor_date')}",
               "anchor × (1 ± 1.28 × daily σ)",
               warnings=band.get("warnings") or []))

    # ── fundamentals: one price, statement-derived where possible ────────────
    vendor = inp.get("vendor") or {}
    vmeta = inp.get("vendor_meta") or {}
    st = inp.get("statements") or {}
    smeta = inp.get("statements_meta") or {}
    ttm = st.get("ttm") or {}
    bal = st.get("balance") or {}
    q_end = st.get("latest_quarter_end")
    periods = st.get("periods") or {w: q_end for w in _STMT_NAME}
    psrc = st.get("period_sources") or {}
    lag = smeta.get("lag") or {}
    stmt_src = f"{st.get('source', 'statements')} (fetched {smeta.get('fetched_date') or st.get('as_of') or '?'})"
    stmt_warn = [smeta["warning"]] if smeta.get("warning") else []

    def s_src(w: str) -> str:
        return psrc.get(w) or stmt_src

    def s_basis(w: str) -> str:
        p = periods.get(w) or q_end
        if w == "balance":
            return f"balance sheet as of {p}" if p else "balance sheet (date unknown)"
        return f"TTM = 4 quarters through {p}" if p else "TTM (period unknown)"

    def s_warn(w: str) -> list[str]:
        if "lag" in smeta:
            return [lag[w]] if lag.get(w) else []
        return list(stmt_warn)

    stmt_basis = s_basis("income")

    shares_m = _f(inp.get("shares_out_m"))
    shares = shares_m * 1e6 if shares_m else None
    raw["shares"] = shares
    mcap = price * shares if price and shares else None
    raw["market_cap"] = mcap
    mc_warn = []
    v_mcap = _f(vendor.get("market_cap_m"))
    if mcap and v_mcap and abs(v_mcap * 1e6 / mcap - 1) > 0.01:
        mc_warn.append(f"vendor market cap {big_money(v_mcap * 1e6)} implies a price "
                       f"of {money(v_mcap * 1e6 / shares)} — not the report price; "
                       "recomputed")
    # which share count, and do the other sources agree with it
    shares_src = inp.get("shares_source") or "vendor profile"

    def sh(v: float) -> str:                         # a share count, in millions
        return f"{v / 1e6:,.0f}M" if v >= 1e8 else f"{v / 1e6:,.1f}M"

    sh_stmt = st.get("shares") or {}
    y_all = _f(profile.get("implied_shares_outstanding"))
    y_listed = _f(profile.get("shares_outstanding"))
    dil = _f(sh_stmt.get("diluted"))
    if shares:
        if y_all and abs(y_all / shares - 1) > SHARE_COUNT_TOLERANCE:
            mc_warn.append(
                f"share-count sources disagree: {sh(shares)} ({shares_src}) is used; Yahoo "
                f"reports {sh(y_all)} across share classes — market cap on that count "
                f"would be {big_money(price * y_all) if price else '—'}")
        if dil and abs(dil / shares - 1) > DILUTED_TOLERANCE:
            over = dil > shares
            mc_warn.append(
                f"diluted share count {sh(dil)} (weighted average, {sh_stmt.get('span', 'quarter')} "
                f"ended {sh_stmt.get('period') or '?'}) is {abs(dil / shares - 1) * 100:.0f}% "
                f"{'above' if over else 'below'} the {sh(shares)} shares outstanding used — "
                + ("convertible securities (for example convertible preferred) or options, "
                   "or share classes not in the count used; "
                   f"on the diluted count market cap would be about "
                   f"{big_money(price * dil) if price else '—'}" if over else
                   "shares were issued after that period, or the sources count different "
                   "share classes"))
        n_listed = sec.get("sessions_listed")
        if (n_listed is not None and n_listed < YOUNG_LISTING_SESSIONS) or is_new:
            others = [f"{sh(v)} {what}" for v, what in ((y_listed, "for the listed class"),
                                                        (y_all, "across share classes")) if v]
            mc_warn.append(
                (f"listed {n_listed} session{'s' if n_listed != 1 else ''} ago"
                 if n_listed is not None else "newly listed")
                + (f" (first trading day {sec['first_trade_date']})" if sec.get("first_trade_date") else "")
                + f" — the share count used ({sh(shares)}, {shares_src}) may not yet reflect "
                "the offering or every share class"
                + (f"; Yahoo lists {' and '.join(others)}" if others else "")
                + "; check the prospectus")
    # An ADR trades as a depositary receipt worth N ordinary shares; the share
    # counts at hand are ordinary shares, ADR equivalents or a mix (TSM 11/22:
    # 25,932M ordinary shares × the ADR price = $8.26T). The ratio is not in
    # the data, so market cap and everything priced off it is withheld. Same
    # when two share-count sources differ by more than 50%.
    sub_type = str(sec.get("asset_sub_type") or "").upper()
    # (Yahoo's listed-class count is not compared: for a multi-class company
    # it is legitimately a fraction of the total — GOOGL class A ≈ half)
    counts = [(v, what) for v, what in ((y_all, "Yahoo, all classes"),
                                        (dil, "diluted weighted average")) if v]
    far = [(v, what) for v, what in counts
           if shares and max(v, shares) / min(v, shares) - 1 > SHARE_COUNT_WITHHOLD]
    mcap_reason = None
    if mcap and sub_type == "ADR":
        others = [f"{sh(v)} {what}" for v, what in counts if abs(v / shares - 1) > 0.01]
        mcap_reason = ("ADR — the share counts available are not in ADR units "
                       f"({sh(shares)} {shares_src} used"
                       + (f"; {', '.join(others)}" if others else "")
                       + "); the ADR-to-ordinary-share ratio is not in the data, so market "
                       "cap and every multiple priced off it is withheld")
    elif mcap and far:
        mcap_reason = (f"share-count sources differ by more than "
                       f"{SHARE_COUNT_WITHHOLD * 100:.0f}% ({sh(shares)} {shares_src} used vs "
                       + ", ".join(f"{sh(v)} {what}" for v, what in far)
                       + ") — market cap and every multiple priced off it is withheld")
    mcap_withheld = [f"market cap withheld — {mcap_reason}"] if mcap_reason else []
    raw["market_cap_withheld"] = mcap_reason
    add(_field("market_cap", "Market cap", mcap, big_money(mcap),
               f"shares outstanding {shares_m:,.0f}M ({shares_src})"
               if shares_m else "shares outstanding unavailable",
               b.get("price_label"),
               f"report price × {sh(shares)} shares outstanding ({shares_src})"
               if shares else "report price", "price × shares outstanding",
               warnings=mc_warn + ([mcap_reason] if mcap_reason else []),
               status="withheld" if mcap_reason else "ok"))
    if mcap_reason:
        mcap = None                       # nothing downstream prices off it

    # A filer that reports in another currency (TSM: TWD) — absolute
    # statement amounts are not dollars; ratios of two amounts in the same
    # currency (margins, growth) are still right
    fin_ccy = str(sec.get("financial_currency") or profile.get("financial_currency") or "USD").upper()
    fx_reason = (f"reported in {fin_ccy} — the filing's amounts are not US dollars; not shown"
                 if fin_ccy != "USD" else None)
    raw["financial_currency"] = fin_ccy

    def samt(v: float | None) -> str:
        """A statement amount in the filing's currency ($1.2B / TWD 1.2B)."""
        s = big_money(v)
        return s if fin_ccy == "USD" or v is None else s.replace("$", f"{fin_ccy} ", 1)

    # EPS: statement quarters first, then Yahoo trailingEps, then vendor
    eps_q = _f(ttm.get("eps_diluted"))
    eps_y = _f((inp.get("eps_check") or {}).get("eps_ttm"))
    eps_v = _f(vendor.get("eps_ttm"))
    eps, eps_src, eps_warn = None, "", []
    cands = [(eps_q, f"sum of 4 reported quarters' diluted EPS ({stmt_basis})"),
             (eps_y, "Yahoo trailingEps"), (eps_v, "Finnhub epsTTM")]
    # The filed statements can trail the earnings release by days (NVDA 11/21:
    # statements through Q2 → $7.91, while Yahoo and Finnhub both carry the
    # post-release $9.31). When the income statement lags AND the two vendor
    # figures agree within 2%, the vendor TTM is the one that includes the
    # latest quarter — use it and say why.
    vendor_agree = (eps_y is not None and eps_v is not None
                    and abs(eps_y - eps_v) <= 0.02 * max(abs(eps_y), abs(eps_v)))
    if lag.get("income") and vendor_agree and eps_q is not None:
        cands = [(eps_y, "Yahoo trailingEps (agrees with Finnhub within 2%; includes the "
                         "latest release, which the filed statements don't yet)"),
                 (eps_v, "Finnhub epsTTM"),
                 (eps_q, f"filed statements ({stmt_basis})")]
    for v, s in cands:
        if v is not None:
            eps, eps_src = v, s
            break
    others = [(v, s) for v, s in cands if v is not None and s != eps_src]
    for v, s in others:
        if eps and abs(v - eps) / max(abs(v), abs(eps)) > DIVERGENCE_LIMIT:
            eps_warn.append(f"TTM EPS sources disagree >50% ({eps_src} {eps} vs {s} {v})")
        elif eps and abs(v - eps) / max(abs(v), abs(eps)) > 0.05:
            eps_warn.append(f"filed statements give TTM EPS of {round(v, 2)} "
                            f"({stmt_basis.replace('TTM = ', '', 1)})"
                            if s.startswith("filed statements") else
                            f"{s} reports {round(v, 2)} (may predate the latest quarter)")
    eps_status = "withheld" if any(">50%" in w for w in eps_warn) else "ok"
    raw["eps"] = eps if eps_status == "ok" else None

    # non-operating items inflating net income
    pre, op = _f(ttm.get("pretax_income")), _f(ttm.get("operating_income"))
    non_op = (pre - op) if pre is not None and op is not None else None
    non_op_warn = None
    if non_op is not None and pre and pre > 0 and non_op / pre >= NON_OP_FLAG_SHARE:
        non_op_warn = (f"TTM pretax income includes {samt(non_op)} of non-operating "
                       f"items ({non_op / pre * 100:.0f}% — e.g. gains on equity "
                       "securities); net income, net margin and P/E are flattered")

    # a one-time income-tax benefit inflating net income (TWLO 11/22: net
    # margin 21.8% beside an operating margin of 6.8% — about $1.0B of
    # deferred-tax valuation allowance released into the tax line)
    ni_ttm, tax_ttm = _f(ttm.get("net_income")), _f(ttm.get("tax"))
    tax_derived = False
    if tax_ttm is None and pre is not None and ni_ttm is not None and ni_ttm > pre:
        tax_ttm = pre - ni_ttm            # net income above pretax income = a benefit
        tax_derived = True                # …or discontinued operations: no tax line to tell
    tax_warn = None
    if (tax_derived and tax_ttm < 0 and pre is not None and pre > 0
            and -tax_ttm >= TAX_BENEFIT_SHARE * pre):
        tax_warn = (f"TTM net income exceeds pretax income by {samt(-tax_ttm)} (a tax "
                    "benefit or discontinued operations) — net income "
                    f"{samt(ni_ttm)} on pretax income of {samt(pre)}; the source "
                    "has no tax line to say which. EPS, P/E and net margin may be "
                    "flattered by a one-time item")
    elif (tax_ttm is not None and tax_ttm < 0 and pre is not None and pre > 0
            and -tax_ttm >= TAX_BENEFIT_SHARE * pre):
        tax_warn = (f"TTM net income {samt(ni_ttm)} includes an income-tax benefit of "
                    f"{samt(-tax_ttm)} on pretax income of {samt(pre)} (for example "
                    "a deferred-tax valuation allowance released) — a one-time item; EPS, "
                    "P/E and net margin are flattered. Taxed at "
                    f"{STATUTORY_TAX * 100:.0f}% instead, net income would be about "
                    f"{samt(pre * (1 - STATUTORY_TAX))}")
    elif (not non_op_warn and ni_ttm is not None and op is not None and ni_ttm > 0
          and ni_ttm > NI_OVER_OP_RATIO * max(op, 0.0)):
        tax_warn = (f"TTM net income {samt(ni_ttm)} exceeds operating income "
                    f"{samt(op)} — the difference comes from below the operating line "
                    "(non-operating gains or a tax benefit); EPS, P/E and net margin are "
                    "flattered")
    below_line = [w for w in (non_op_warn, tax_warn) if w]

    # EPS in the filing's currency against a US-dollar price is not a P/E
    fx_w = [fx_reason] if fx_reason else []
    if fx_reason and eps is not None:
        eps_status = "withheld"
        raw["eps"] = None
    add(_field("eps_ttm", "EPS (TTM)", eps, money(eps), eps_src,
               periods.get("income") if eps_src.startswith("sum") else (inp.get("eps_check") or {}).get("as_of"),
               "trailing twelve months", "sum of the last 4 quarters' diluted EPS",
               warnings=eps_warn + (s_warn("income") if eps_src.startswith("sum") else [])
               + ([tax_warn] if tax_warn else []) + fx_w,
               status=eps_status))

    pe = price / eps if price and eps and eps > 0 and eps_status == "ok" else None
    pe_warn = list(eps_warn) + below_line + fx_w
    if eps is not None and eps <= 0:
        pe_warn.append("negative TTM EPS — P/E undefined")
    add(_field("pe_ttm", "P/E (TTM)", pe, ratio(pe, 1, ""), f"price ÷ EPS ({eps_src})",
               b.get("price_label"), "report price ÷ TTM EPS", "price ÷ TTM EPS",
               warnings=pe_warn,
               status="withheld" if eps_status == "withheld" else "ok"))

    fcf = _f(ttm.get("fcf"))
    raw["fcf"] = fcf
    add(_field("fcf_ttm", "Free cash flow (TTM)", fcf, big_money(fcf), s_src("cashflow"),
               periods.get("cashflow"), s_basis("cashflow"),
               "operating cash flow − capex, last 4 quarters",
               warnings=s_warn("cashflow") + (fx_w if fcf is not None else []),
               status="withheld" if fx_reason and fcf is not None else "ok"))
    p_fcf = mcap / fcf if mcap and fcf and fcf > 0 and not fx_reason else None
    pfcf_off = bool((mcap_reason or fx_reason) and fcf is not None)
    add(_field("p_fcf", "P/FCF (TTM)", p_fcf, ratio(p_fcf), "market cap ÷ TTM FCF",
               b.get("price_label"), f"report price; {s_basis('cashflow')}",
               "market cap ÷ TTM free cash flow",
               warnings=s_warn("cashflow") + (["FCF ≤ 0 — P/FCF undefined"] if fcf is not None and fcf <= 0 else [])
               + (mcap_withheld if fcf is not None else []) + (fx_w if fcf is not None else []),
               status="withheld" if pfcf_off else "ok"))

    debt, cash_st = _f(bal.get("total_debt")), _f(bal.get("cash_st") or bal.get("cash"))
    debt_missing = ["debt not found in the filing — EV and net cash withheld"] \
        if debt is None and cash_st is not None and st else []
    eq_sec = _f(bal.get("equity_securities")) or 0.0
    raw.update({"debt": debt, "cash_st": cash_st, "equity_securities": eq_sec})
    ev = (mcap + debt - cash_st - eq_sec) if mcap is not None and debt is not None and cash_st is not None else None
    net_cash = (cash_st + eq_sec - debt) if debt is not None and cash_st is not None else None
    breakdown = (f"cash & short-term investments {big_money(cash_st)}"
                 + (f" + marketable equity securities {big_money(eq_sec)}" if eq_sec else "")
                 + f" − debt {big_money(debt)}") if net_cash is not None else ""
    debt_note = f" ({bal['debt_note']})" if bal.get("debt_note") else ""
    # a balance sheet dated before the first trading day has none of the
    # offering's proceeds in it (ANT 11/22: listed Nov 19, balance sheet of
    # Sep 30) — net cash is shown with that said; EV, which would be too
    # high by the proceeds, is withheld
    bal_date = str(bal.get("as_of_date") or periods.get("balance") or "")[:10]
    first_trade = str(sec.get("first_trade_date") or "")[:10]
    pre_listing = ([f"balance sheet predates the listing — IPO proceeds not included "
                    f"(balance sheet {bal_date}; first trading day {first_trade})"]
                   if bal_date and first_trade and bal_date < first_trade else [])
    if fx_reason:
        ev = None                         # US-dollar market cap + foreign-currency balance sheet
    have_bal = debt is not None and cash_st is not None
    ev_off = bool(have_bal and (mcap_reason or fx_reason))
    add(_field("ev", "Enterprise value", ev, big_money(ev),
               f"market cap + {s_src('balance')}",
               b.get("price_label"), f"report price; {s_basis('balance')}",
               "market cap + total debt − cash & short-term investments − marketable securities"
               + debt_note, warnings=s_warn("balance") + debt_missing + pre_listing
               + (mcap_withheld if have_bal else []) + (fx_w if have_bal else []),
               status="withheld" if (pre_listing and ev is not None) or ev_off else "ok"))
    add(_field("net_cash", "Net cash", net_cash,
               (f"{big_money(net_cash)} ({breakdown})" if net_cash is not None else "—"),
               s_src("balance"), bal.get("as_of_date") or periods.get("balance"),
               s_basis("balance"),
               "cash & short-term investments + marketable securities − total debt" + debt_note,
               warnings=s_warn("balance") + pre_listing + (fx_w if net_cash is not None else []),
               status="withheld" if fx_reason and net_cash is not None else "ok"))

    # margins: statement TTM + latest quarter, labeled
    rev = _f(ttm.get("revenue"))
    qs = st.get("quarters") or []
    q0 = qs[0] if qs else {}

    def margin(num_key: str) -> float | None:
        n = _f(ttm.get(num_key))
        return n / rev * 100 if n is not None and rev else None

    gm, om, nm = margin("gross_profit"), margin("operating_income"), margin("net_income")
    q_rev, q_ni = _f(q0.get("revenue")), _f(q0.get("net_income"))
    nm_q = q_ni / q_rev * 100 if q_rev and q_ni is not None else None
    if rev:
        inc_src, inc_p, inc_b, inc_w = s_src("income"), periods.get("income"), s_basis("income"), s_warn("income")
        gm_derived = bool(ttm.get("gross_profit_derived"))
        add(_field("gross_margin", "Gross margin (TTM)", gm, pct(gm, 1), inc_src, inc_p,
                   inc_b + ("; gross profit = revenue − cost of revenue (the filing has "
                            "no gross-profit line)" if gm_derived else ""),
                   "TTM (revenue − cost of revenue) ÷ TTM revenue" if gm_derived
                   else "TTM gross profit ÷ TTM revenue", warnings=inc_w,
                   derived=gm_derived))
        add(_field("operating_margin", "Operating margin (TTM)", om, pct(om, 1), inc_src,
                   inc_p, inc_b, "TTM operating income ÷ TTM revenue", warnings=inc_w))
        add(_field("net_margin", "Net margin (TTM)", nm, pct(nm, 1), inc_src, inc_p,
                   inc_b, "TTM net income ÷ TTM revenue",
                   warnings=inc_w + below_line))
    else:
        for key, label, vk in (("gross_margin", "Gross margin (TTM)", "gross_margin_pct"),
                               ("operating_margin", "Operating margin (TTM)", "operating_margin_pct"),
                               ("net_margin", "Net margin (TTM)", "net_margin_pct")):
            v = _f(vendor.get(vk))
            add(_field(key, label, v, pct(v, 1), "Finnhub vendor metric (TTM)",
                       vmeta.get("fetched_date"), "vendor TTM — period not stated by vendor",
                       "vendor-computed", warnings=["statements unavailable — vendor "
                                                    "figure, not recomputed"]))
    add(_field("net_margin_q", f"Net margin (latest quarter{', ' + q0['end'] if q0.get('end') else ''})",
               nm_q, pct(nm_q, 1), s_src("income"), q0.get("end"), "latest reported quarter",
               "quarter net income ÷ quarter revenue", warnings=s_warn("income")))
    q_yoy, q_yoy_txt = None, ""
    if len(qs) >= 5 and _f(qs[0].get("revenue")) and _f(qs[4].get("revenue")):
        try:
            gap = (dt.date.fromisoformat(qs[0]["end"]) - dt.date.fromisoformat(qs[4]["end"])).days
        except (ValueError, KeyError):
            gap = 0
        if 345 <= gap <= 385:
            q_yoy = (_f(qs[0]["revenue"]) / _f(qs[4]["revenue"]) - 1) * 100
            q_yoy_txt = f"quarter ended {qs[0]['end']} vs {qs[4]['end']}"
    add(_field("revenue_growth_q", "Revenue growth (latest quarter YoY)", q_yoy,
               pct(q_yoy, 1, True), s_src("income"), qs[0].get("end") if qs else None,
               q_yoy_txt or "needs the same quarter a year earlier",
               "quarter revenue ÷ same quarter a year earlier − 1",
               warnings=s_warn("income") if q_yoy is not None else []))
    rg = _f(vendor.get("revenue_growth_ttm_pct"))
    # The vendor's TTM growth is not recomputed and its revenue line is not
    # stated. JPM 11/22: +109.0% vendor TTM beside +8.1% for the latest quarter
    # from the filing. More than RG_MAX_GAP_POINTS apart, or a bank (whose
    # "revenue" differs by source: net of interest expense or gross), the
    # vendor figure is withheld.
    rg_why = None
    if rg is not None and is_bank:
        rg_why = ("bank — the vendor's revenue line is not stated (banks report revenue "
                  "net of interest expense or gross, by source); vendor TTM growth withheld"
                  + (f"; the filing's latest quarter is {pct(q_yoy, 1, True)} YoY"
                     if q_yoy is not None else ""))
    elif rg is not None and q_yoy is not None and abs(rg - q_yoy) > RG_MAX_GAP_POINTS:
        rg_why = (f"vendor TTM growth {pct(rg, 1, True)} vs {pct(q_yoy, 1, True)} for the "
                  f"latest quarter in the filing ({q_yoy_txt}) — more than "
                  f"{RG_MAX_GAP_POINTS:.0f} points apart; vendor figure withheld")
    add(_field("revenue_growth", "Revenue growth (TTM YoY)", rg, pct(rg, 1),
               "Finnhub vendor metric", vmeta.get("fetched_date"),
               "vendor TTM vs prior TTM", "vendor-computed (not recomputed here)",
               warnings=([f"vendor data fetched {vmeta.get('fetched_date')}, before the "
                          f"{smeta.get('last_earnings')} earnings release — may not include "
                          "the latest quarter"] if vmeta.get("predates_earnings") else [])
               + ([rg_why] if rg_why else []),
               status="withheld" if rg_why else "ok"))

    # ── banks: the two ratios a bank is read on, from the same statements ────
    if is_bank:
        add(_field("bank_note", "Bank", True, BANK_NOTE, "TickerBands", None, "", "",
                   reason="; ".join(sec.get("why") or [])))
        equity = _f(bal.get("equity"))
        pref_raw = _f(bal.get("preferred_equity"))
        pref = pref_raw or 0.0
        pref_found = bool(bal.get("preferred_found", pref_raw is not None))
        pref_shares = _f(bal.get("preferred_shares"))
        gwi = _f(bal.get("goodwill_intangibles"))
        tce = (equity - pref - gwi) if equity is not None and gwi is not None else None
        tbv_warn: list[str] = []
        tbv_withhold = None
        if not pref_found:
            if pref_shares:
                # preferred shares on the balance sheet but no value for them:
                # tangible COMMON equity cannot be computed
                tbv_withhold = (f"the filing reports {pref_shares:,.0f} preferred shares "
                                "but no preferred equity value — tangible common equity "
                                "cannot be computed")
            else:
                tbv_warn.append("preferred equity not found — treated as 0; tangible book "
                                "may be overstated")
        gwi_missing = [m for m in (bal.get("goodwill_intangibles_missing") or [])
                       if gwi is not None]
        for m in gwi_missing:
            tbv_warn.append(f"{m} not found — treated as 0; tangible book may be overstated")
        gwi_dates = bal.get("goodwill_intangibles_dates") or {}
        if gwi_dates:
            tbv_warn.append("; ".join(f"{n} as of {d}" for n, d in gwi_dates.items())
                            + f" (the latest filed figure; balance sheet {bal_date or '?'})")
        if fx_reason and tce is not None:
            tbv_withhold = tbv_withhold or fx_reason
        p_tbv = mcap / tce if mcap and tce and tce > 0 and not tbv_withhold else None
        raw["tangible_common_equity"] = tce
        tbv_parts = (f"equity {samt(equity)}"
                     + (f" − preferred {samt(pref)}" if pref else "")
                     + f" − goodwill & intangibles {samt(gwi)} = {samt(tce)}"
                     ) if tce is not None else ""
        if tce is not None or equity is not None:
            add(_field("p_tbv", "Price / tangible book", p_tbv, ratio(p_tbv, 2),
                       f"market cap ÷ {s_src('balance')}", b.get("price_label"),
                       f"report price; {s_basis('balance')}" + (f"; {tbv_parts}" if tbv_parts else ""),
                       "market cap ÷ (shareholders' equity − preferred equity − goodwill "
                       "− other intangibles)",
                       warnings=s_warn("balance")
                       + (["goodwill / intangibles not found in the balance sheet — "
                           "tangible book not computed"] if tce is None else [])
                       + (["tangible common equity ≤ 0 — ratio undefined"]
                          if tce is not None and tce <= 0 else [])
                       + tbv_warn + ([tbv_withhold] if tbv_withhold else [])
                       + mcap_withheld,
                       status="withheld" if tbv_withhold or (mcap_withheld and tce is not None)
                       else "ok",
                       tangible_common_equity=tce if not tbv_withhold else None))
        roe = ni_ttm / equity * 100 if ni_ttm is not None and equity and equity > 0 else None
        if roe is not None:
            add(_field("roe", "Return on equity (TTM)", roe, pct(roe, 1),
                       f"{s_src('income')} ÷ {s_src('balance')}", periods.get("income"),
                       f"{s_basis('income')}; equity {samt(equity)} at {bal_date or '?'} "
                       "(period end, not an average)",
                       "TTM net income ÷ total shareholders' equity",
                       warnings=s_warn("income") + [w for w in s_warn("balance")
                                                    if w not in s_warn("income")]
                       + ([tax_warn] if tax_warn else [])))

    # dividends: forward and trailing, each at the report price
    dv = inp.get("dividends") or {}
    events = dv.get("events") or []
    yl = "Distribution yield" if is_fund else "Dividend yield"
    if dv.get("available"):
        fd = forward_distribution(events)
        freq, last_amt = fd["per_year"], fd["latest"]
        fwd = (last_amt * freq / price * 100) if fd["ok"] and last_amt and freq and price else None
        # evenly spaced ex-dates (the amounts may still vary)
        regular = freq is not None and not (fd["why"] or "").startswith("payment schedule")
        paid_events, drift_n = trailing_distributions(events, today, freq if regular else None)
        trailing_paid = sum(float(e["amount"]) for e in paid_events)
        trl = trailing_paid / price * 100 if price else None
        raw.update({"div_trailing_paid": trailing_paid, "div_latest": last_amt,
                    "div_per_year": freq if fd["ok"] else None})
        if not events:
            fwd = 0.0          # a non-payer's yield is a real zero, not missing
        fwd_warn = []
        if events and last_amt is None:
            fwd_warn.append("forward yield not computed — no ex-date in the history has a "
                            "positive amount")
        elif events and fwd is None and fd["why"]:
            fwd_warn.append(f"forward yield not computed — {fd['why']}; the trailing "
                            "figure is what was actually paid")
        elif fd["note"]:
            fwd_warn.append(fd["note"])
        add(_field("div_yield_forward", f"{yl} (forward)", fwd,
                   pct(fwd) if fwd is not None else ("0.00%" if not events else "—"),
                   f"{dv.get('source', '?')} distribution history", dv.get("as_of"),
                   f"latest declared ${last_amt:.4g} × {freq}/yr at the report price" if fwd else
                   "report price",
                   "latest declared distribution × payments per year ÷ price",
                   warnings=fwd_warn,
                   status="withheld" if events and fwd is None else "ok"))
        n_paid = len(paid_events)
        add(_field("div_yield_trailing", f"{yl} (trailing 12 mo)", trl, pct(trl),
                   f"{dv.get('source', '?')} distribution history", dv.get("as_of"),
                   f"${trailing_paid:.2f} paid in the last 12 months"
                   f" ({n_paid} payment{'s' if n_paid != 1 else ''}) at the report price"
                   + (f"; {drift_n} earlier ex-date inside the 365-day window is the same "
                      "payment a year ago and is not counted" if drift_n else ""),
                   "sum of distributions with ex-date in the last 365 days (at most one "
                   "year's worth of a regular schedule) ÷ price",
                   payments=n_paid))
        ev_sorted = _sorted_events(events) if is_fund and events else []
        if is_fund and events and not ev_sorted:
            # every listed ex-date has a zero or missing amount: there is no
            # latest payment to show (this used to raise → HTTP 500)
            add(_field("fund_schedule", "Distribution schedule", None, "—",
                       f"{dv.get('source', '?')} distribution history", dv.get("as_of"),
                       "spacing of the last 5 ex-dates", "median gap between ex-dates",
                       warnings=[f"{len(events)} ex-date{'s' if len(events) != 1 else ''} "
                                 "listed, none with a positive amount — the source's "
                                 "distribution history is incomplete"],
                       status="withheld", per_year=None))
        if ev_sorted:
            gaps = schedule_gaps(ev_sorted)
            sched_word = _SCHEDULE_WORD.get(freq) if regular else None
            sched = (f"{sched_word} ({freq}/yr)" if sched_word else
                     "irregular" + (f" — {min(gaps)}–{max(gaps)} days between the last "
                                    f"{len(gaps) + 1} ex-dates" if gaps else ""))
            add(_field("fund_schedule", "Distribution schedule", freq if sched_word else None,
                       f"{sched} · latest ${last_amt:.4g}, ex {fd['latest_date']}",
                       f"{dv.get('source', '?')} distribution history", dv.get("as_of"),
                       "spacing of the last 5 ex-dates", "median gap between ex-dates",
                       status="ok", per_year=freq if sched_word else None))
            last12 = list(reversed(ev_sorted[-12:]))
            add(_field("fund_distributions", f"Distributions (last {len(last12)})", len(last12),
                       " · ".join(f"{str(e['date'])[:10]} ${float(e['amount']):.4g}"
                                  for e in last12),
                       f"{dv.get('source', '?')} distribution history", dv.get("as_of"),
                       "per share, by ex-date, newest first", "as reported",
                       items=[{"date": str(e["date"])[:10], "amount": float(e["amount"])}
                              for e in last12]))
    else:
        v = vendor.get("dividend_yield_pct")
        add(_field("div_yield_forward", f"{yl} (forward)", None, "—",
                   "no distribution history", None, "", "",
                   warnings=([f"vendor yield {v}% cannot be verified at the report price — "
                              "not shown"] if v is not None else [])))
        add(_field("div_yield_trailing", f"{yl} (trailing 12 mo)", None, "—",
                   "no distribution history", None, "", ""))

    # ── funds: what the fund's own profile says (only what the source gives) ─
    if is_fund:
        p_src = profile.get("source") or "fund profile"
        er, aum, nav = (_f(profile.get("expense_ratio_pct")), _f(profile.get("total_assets")),
                        _f(profile.get("nav")))
        if er is not None:
            add(_field("fund_expense_ratio", "Expense ratio", er, pct(er, 2), p_src,
                       profile.get("as_of"), "net annual expense ratio", "as reported"))
        if aum:
            add(_field("fund_aum", "Net assets", aum, big_money(aum), p_src,
                       profile.get("as_of"), "fund total net assets", "as reported"))
        if nav:
            # Yahoo's navPrice carries no date of its own: the row is dated by
            # when the profile was fetched, and the NAV is never compared with
            # the report price unless it is known to be the report session's
            # (QQQI 11/22: NAV $48.15 beside a $45.58 close — the Nov 19 NAV)
            nav_date = str(profile.get("nav_date") or "")[:10] or None
            fetched = str(profile.get("as_of") or "")[:10] or None
            sess = str(b.get("session_date") or "")[:10] or None
            prior = _f(b.get("prior_close"))
            nav_w: list[str] = []
            prem = None
            if nav_date and sess and nav_date == sess and price:
                prem = (price / nav - 1) * 100
            elif nav_date and sess and nav_date < sess:
                nav_w.append(f"NAV as of {nav_date} is older than the report session ({sess}) "
                             "— premium/discount not computed")
            elif fetched and sess and fetched < sess:
                nav_w.append(f"NAV fetched {fetched}, before the report session ({sess}) — "
                             "older than the report price; premium/discount not computed")
            else:
                nav_w.append("the source does not date its NAV — it may predate the report "
                             f"session ({sess or '?'}); premium/discount not computed")
            if (prem is None and price and prior and abs(nav / prior - 1) <= 0.005
                    and abs(nav / price - 1) > 0.01):
                nav_w.append(f"NAV {money(nav)} is within 0.5% of the prior session's close "
                             f"({money(prior)}) and {abs(nav / price - 1) * 100:.1f}% from "
                             "the report price — likely the prior session's NAV")
            add(_field("fund_nav", "NAV per share", nav,
                       money(nav) + (f" (as of {nav_date})" if nav_date else
                                     f" (fetched {fetched}; NAV date not stated)" if fetched else ""),
                       p_src, nav_date or fetched,
                       (f"published NAV as of {nav_date}" if nav_date else
                        f"the source's latest published NAV, fetched {fetched or '?'} — its "
                        "date is not stated")
                       + (f"; {pct(prem, 2, True)} vs the report price" if prem is not None
                          else " — not compared with the report price"),
                       "as reported" + ("; premium/discount = price ÷ NAV − 1"
                                        if prem is not None else ""),
                       warnings=nav_w, nav_date=nav_date, fetched=fetched,
                       premium_discount_pct=prem))
        cat = " · ".join(x for x in (profile.get("category"), profile.get("fund_family")) if x)
        if cat:
            add(_field("fund_category", "Category", cat, cat, p_src, profile.get("as_of"),
                       "category · fund family", "as reported"))

    # ── context ─────────────────────────────────────────────────────────────
    beta = inp.get("beta") or {}
    corr = _f(beta.get("correlation"))
    # a slope fitted to returns that barely move with SPY says nothing
    # (MNDY 11/22: beta 0.03 on a correlation of 0.01)
    beta_warn = ([f"beta not meaningful — correlation {corr:.2f} over the same "
                  f"{beta.get('window', 60)} sessions (below {BETA_MIN_CORRELATION:.1f}): the "
                  "stock has not been moving with SPY"]
                 if beta.get("beta") is not None and corr is not None
                 and abs(corr) < BETA_MIN_CORRELATION else [])
    add(_field("beta", "Beta vs SPY", beta.get("beta"),
               ratio(beta.get("beta"), 2, ""), "TickerBands, computed from daily bars",
               b.get("prior_date") if b.get("price_kind") == "live" else b.get("session_date"),
               f"{beta.get('window', 60)} daily returns vs SPY",
               "OLS slope of daily returns vs SPY daily returns", warnings=beta_warn))
    add(_field("correlation", "Correlation to SPY", beta.get("correlation"),
               ratio(beta.get("correlation"), 2, ""), "TickerBands, computed from daily bars",
               None, f"{beta.get('window', 60)} daily returns", "Pearson correlation"))
    q = inp.get("quote") or {}
    lo, hi = _f(q.get("week52_low")), _f(q.get("week52_high"))
    w52 = None
    w52_warn = []
    if price and lo and hi and hi > lo:
        w52 = (price - lo) / (hi - lo) * 100
        if price < lo:
            w52_warn.append("price is below the vendor 52-week low — a new low")
    add(_field("week52", "52-week position", w52,
               f"{w52:.0f}% of {money(lo)}–{money(hi)}" if w52 is not None else "—",
               "52-week range from the broker quote", None, "report price",
               "(price − 52w low) ÷ (52w high − 52w low)", warnings=w52_warn,
               low=lo, high=hi))
    rv20 = (inp.get("signals") or {}).get("rv_20d")
    add(_field("rv20", "Realized vol (20d)", rv20, pct(rv20, 1),
               "TickerBands, from daily bars", b.get("session_date"),
               "20 close-to-close returns, annualized", "stdev × √252"))

    # options
    o = inp.get("options") or {}
    if o.get("available"):
        o_asof = o.get("as_of")
        o_warn = (["market closed — session volume and marks are from the last "
                   "session"] if b.get("price_kind") != "live" else [])
        add(_field("put_call", "Put/Call (session volume)", o.get("put_call_ratio"),
                   ratio(o.get("put_call_ratio"), 2, ""), "broker option chain", o_asof,
                   "session volume", "put volume ÷ call volume", warnings=o_warn))
        iv_note = o.get("atm_iv_note")
        add(_field("atm_iv", "ATM implied vol", o.get("atm_iv_pct"), pct(o.get("atm_iv_pct"), 1),
                   "broker option chain", o_asof,
                   "nearest expiry ≤45d" + ("; restated to the last close" if iv_note else ""),
                   "avg of ATM call/put IV" + (" × √(time to expiry now ÷ at the close)"
                                               if iv_note else ""),
                   warnings=[iv_note] if iv_note else []))
        im = o.get("implied_move_pct")
        n_sess = options_iv.sessions_to_expiry(o, b)
        exp_c = options_iv.expiry_close(o)
        span = (f"{n_sess} session{'s' if n_sess != 1 else ''}" if n_sess
                else f"{o.get('implied_move_dte', '—')}d")
        add(_field("implied_move", f"Implied move ({span})", im,
                   f"±{im:.2f}%" if im is not None else "—", "broker option chain", o_asof,
                   "ATM straddle ÷ underlying"
                   + (f", expiry {exp_c:%a %b} {exp_c.day}" if exp_c else "")
                   + (f", {o['atm_strike']:g} strike" if o.get("atm_strike") else ""),
                   "(call + put of the nearest expiry, at the strike closest to spot "
                   "that both have) ÷ spot"))
        # the band is one session wide; a straddle over n sessions is compared
        # with the band widened by √n
        k = math.sqrt(n_sess) if n_sess and n_sess > 1 else 1.0
        ivb = (im / (band["half_width_pct"] * k)) if im is not None and band.get("half_width_pct") else None
        add(_field("implied_vs_band", "Implied move vs our band", ivb, ratio(ivb, 2, "×"),
                   "derived", o_asof, f"band × √{n_sess} sessions" if k > 1 else "",
                   "implied move ÷ (band half-width × √sessions to expiry)"))

    # news sentiment (with sample size; withheld below the minimum)
    n = inp.get("news") or {}
    nn = n.get("articles_week")
    news_ok = bool(n.get("available")) and (nn or 0) >= MIN_HEADLINES_FOR_SENTIMENT
    add(_field("news_sentiment", "News sentiment", (n.get("bullish_pct") if news_ok else None),
               (f"{int((n.get('bullish_pct') or 0) * 100)}% bullish / "
                f"{int((n.get('bearish_pct') or 0) * 100)}% bearish (n={nn})" if news_ok else "—"),
               f"{n.get('method', 'finnhub')}", None, f"n = {nn} articles",
               "share of bullish vs bearish articles",
               warnings=([] if news_ok or not n.get("available") else
                         [f"only {nn} relevant articles — below the minimum of "
                          f"{MIN_HEADLINES_FOR_SENTIMENT}; not scored"]),
               sample_n=nn,
               status="ok" if news_ok else ("withheld" if n.get("available") else "no_data")))
    hv = inp.get("headlines_view") or {}
    add(_field("headlines", "Headlines", hv.get("kept"),
               f"{hv.get('kept', 0)} relevant of {hv.get('fetched', 0)} fetched",
               "Finnhub company news, relevance-filtered", hv.get("window_to"),
               f"{hv.get('window_from') or '?'} → {hv.get('window_to') or '?'}",
               f"kept only items mentioning {', '.join(hv.get('terms') or [sym])}",
               items=hv.get("items") or [], status="ok"))

    fil = inp.get("filings") or []
    hits = inp.get("regulatory_hits") or []
    reds = [f for f in fil if f.get("red_flag")]
    nw = inp.get("news_window") or {}
    reg_parts = []
    if fil:
        # the issuer's own reports first; Form 4 / 144 / 13G / fund filings
        # are summarized by form so they cannot crowd out an 8-K
        core = [f for f in fil if f.get("group", "company") in ("company", "ownership")
                or f.get("red_flag")]
        routine = [f for f in fil if f not in core]
        by_form: dict[str, int] = {}
        for f in routine:
            by_form[f["form"]] = by_form.get(f["form"], 0) + 1
        reg_parts.append("SEC filings: " + "; ".join(
            [f"{f['date']} {f['form']} ({f.get('desc')})" for f in core[:5]]
            + ([f"{len(core) - 5} more company filing{'s' if len(core) > 6 else ''}"]
               if len(core) > 5 else [])
            + ([", ".join(f"Form {k} ×{n}" if k[:1].isdigit() else f"{k} ×{n}"
                          for k, n in by_form.items())] if by_form else [])))
    if hits:
        reg_parts.append("headlines: " + "; ".join(
            f"{h['date'][5:]} {h['headline']} ({h['source']})" for h in hits[:4]))
    add(_field("regulatory", "Regulatory & legal pass",
               len(fil) + len(hits) if (fil or hits or inp.get("filings") is not None) else None,
               (" · ".join(reg_parts) if reg_parts else
                f"no SEC filings and no regulatory/legal headlines since {nw.get('from')}"),
               "SEC EDGAR submissions + relevance-filtered headlines", nw.get("to"),
               f"window {nw.get('from')} → {nw.get('to')}",
               "8-K items named; headlines matched for SEC/DOJ/AG/subpoena/probe/class "
               "action/export rules/restatement",
               warnings=([f"8-K red flag: {f['date']} {f['desc']}" for f in reds]
                         + (["headline feed is not a complete news search — the research "
                             "session must still search the window"] if not hits else [])),
               status="ok", filings=fil, hits=hits))
    mc_ = inp.get("move_cause")
    if mc_ is not None:
        found = mc_.get("found") or []
        disp = ("; ".join(f"{x['date'][5:]} {x['headline']} ({x['source']})" for x in found[:3])
                or "")
        if mc_.get("filings"):
            disp = (disp + "; " if disp else "") + "; ".join(
                f"{f['date']} {f['form']} ({f.get('desc')})" for f in mc_["filings"])
        add(_field("move_cause", f"Reported cause of the {word} move",
                   len(found) + len(mc_.get("filings") or []),
                   disp or "cause not found in the feed — verify before acting",
                   "relevant headlines ±1 day + SEC filings", mc_.get("session_date"),
                   "required after a ≥2σ session", "keyword match (candidates, not proof)",
                   warnings=([] if (found or mc_.get("filings")) else
                             ["a ≥2σ move with no reported cause in the feed — do not treat "
                              "this as 'no news'; search before acting"]),
                   status="ok"))

    flags = inp.get("research_flags") or {}
    cn = inp.get("claude_news")
    add(_field("claude_news", "Research news check", cn and cn.get("news_view"),
               (f"{(cn.get('news_view') or '').upper()} — {cn.get('news_note') or ''}" if cn else "—"),
               "stored research discussion", cn and cn.get("created_ts"),
               f"required window {(inp.get('news_window') or {}).get('from')} → "
               f"{(inp.get('news_window') or {}).get('to')}",
               "written by the research session; not computed",
               warnings=flags.get("news") or []))
    cs = inp.get("claude_stance")
    vfields = research_quality.verdict_fields(cs) if cs else {}
    if vfields.get("format") == "v6" and vfields.get("verdict_label"):
        # prompt v6: the verdict is the headline (docs/VERDICT_SPEC.md §1)
        cs_label = "Research verdict"
        cs_display = (f"{vfields['verdict_label']} — {vfields.get('verdict_note') or ''}"
                      .rstrip(" —"))
    else:
        cs_label = "Research stance"
        cs_display = (f"{research_quality.stance_label(cs)} · {cs.get('stance_horizon') or ''} — "
                      f"{cs.get('stance_note') or ''}" if cs else "—")
    add(_field("claude_stance", cs_label, cs and cs.get("stance"), cs_display,
               "stored research discussion", cs and cs.get("created_ts"),
               "reasoned judgment, dated", "written by the research session; not computed",
               warnings=flags.get("stance") or [],
               # what would change it (prompt v5) — shown under the stance
               **research_quality.stance_conditions(cs),
               # the verdict keys (v6) / format "earlier", and the note's trust
               **vfields, trust=(cs or {}).get("trust"),
               note_status=(cs or {}).get("status")))

    # social (sample size = tagged messages)
    so = inp.get("social") or {}
    tagged = so.get("tagged")
    if tagged is None and so.get("available"):
        tagged = (so.get("bullish") or 0) + (so.get("bearish") or 0)
    soc_ok = bool(so.get("available")) and (tagged or 0) >= MIN_TAGGED_SOCIAL
    add(_field("social", "Social (StockTwits)", so.get("polarity") if soc_ok else None,
               (f"{so.get('bullish', 0)} bull / {so.get('bearish', 0)} bear "
                f"(n={tagged} tagged), polarity {so.get('polarity'):+.2f}" if soc_ok else "—"),
               "StockTwits recent stream", None, f"n = {tagged} tagged messages",
               "(bullish − bearish) ÷ tagged",
               warnings=([] if soc_ok or not so.get("available") else
                         [f"only {tagged} tagged messages — below the minimum of "
                          f"{MIN_TAGGED_SOCIAL}; not scored"]),
               sample_n=tagged,
               status="ok" if soc_ok else ("withheld" if so.get("available") else "no_data")))

    # analyst mix — only a recent monthly snapshot
    recs = inp.get("recs") or {}
    months = recs.get("months") or []
    an_ok, an_warn, m0 = False, [], months[0] if months else None
    rc = inp.get("rating_changes") or {}
    changes = rc.get("changes") or []
    keys5 = ("strong_buy", "buy", "hold", "sell", "strong_sell")
    unchanged = (len(months) >= 2 and all(months[0].get(k) == months[1].get(k) for k in keys5))
    if m0 and unchanged:
        since = str(months[1].get("period"))[:10]
        n_since = sum(1 for c in changes if c["date"] >= since)
        an_warn.append(
            f"identical to the {months[1].get('period')} snapshot"
            + (f" while Yahoo lists {n_since} rating change(s) since {since} — "
               "the snapshot is stale" if n_since else " — may be stale"))
    if m0:
        try:
            age = (today - dt.date.fromisoformat(str(m0.get("period"))[:10])).days
        except ValueError:
            age = None
        if age is None or age > ANALYST_MAX_AGE_DAYS:
            an_warn.append(f"latest analyst snapshot is for {m0.get('period')} — stale; not shown")
        elif not m0.get("total"):
            an_warn.append("analyst snapshot is empty")
        else:
            an_ok = True
    # dated rating actions (report-only; never in the Setup Score)
    recent = [c for c in changes if c["date"] >= (today - dt.timedelta(days=30)).isoformat()]
    def _dir(c):
        a = (c.get("action") or "").lower()
        return ("down" if a.startswith("down") else "up" if a.startswith("up")
                else "init" if a.startswith("init") else a)
    n_down = sum(1 for c in recent if _dir(c) == "down")
    n_up = sum(1 for c in recent if _dir(c) == "up")
    n_init = sum(1 for c in recent if _dir(c) == "init")   # new coverage, counted apart
    n_other = len(recent) - n_down - n_up - n_init
    # a feed with no rows at all for the symbol is missing coverage, not a
    # count of zero (ANT, days after its listing: 0/0/0 while firms initiated)
    no_feed = bool(rc and rc.get("no_feed"))
    aa_warn = ([f"{rc.get('source', 'Yahoo')} has no rating-action history at all for {sym}"
                + (" (listed days ago)" if is_new else "")
                + " — initiations and changes are not in this feed yet; not a count of zero"]
               if no_feed else [])

    def _action(c) -> str:
        if _dir(c) == "init":
            return f"; {c['date'][5:]} {c['firm']} initiates at {c['to'] or '?'}"
        return f"; {c['date'][5:]} {c['firm']} {c['from'] or '?'}→{c['to']}"
    add(_field("analyst_actions", "Analyst rating changes (30 days)",
               len(recent) if rc and not no_feed else None,
               (f"{n_down} downgrade(s), {n_up} upgrade(s), {n_init} initiation(s), "
                f"{n_other} other" + "".join(_action(c) for c in recent[:4])
                if rc and not no_feed else "—"),
               f"{rc.get('source', 'Yahoo')} rating actions", today.isoformat(),
               "dated firm-level actions", "count by action (initiations = new coverage; "
               "other = ratings reiterated or maintained)",
               warnings=aa_warn,
               status="ok" if rc and not no_feed else "no_data", items=recent[:10],
               counts={"down": n_down, "up": n_up, "init": n_init, "other": n_other}))
    add(_field("analyst_mix", "Analyst mix", m0.get("total") if an_ok else None,
               (f"{m0['strong_buy'] + m0['buy']} buy / {m0['hold']} hold / "
                f"{m0['sell'] + m0['strong_sell']} sell of {m0['total']}" if an_ok else "—"),
               "Finnhub monthly recommendation snapshot", m0 and m0.get("period"),
               "month-level counts; rating changes within the month may be missing",
               "count by rating", warnings=an_warn,
               status="ok" if an_ok else ("withheld" if m0 else "no_data")))

    # next earnings — confirmed vs estimated, never a past date
    e = inp.get("earnings") or {}
    ec = dict(inp.get("earnings_confirm") or {})
    if ec.get("date") and ec["date"] < today.isoformat():
        ec = {}      # Yahoo still lists the release that just happened — not "next"
    nd = e.get("next_date")
    e_warn: list[str] = []
    tag, e_src = None, "Finnhub earnings calendar"
    if nd:
        try:
            ndd = dt.date.fromisoformat(nd[:10])
        except ValueError:
            ndd = None
        if ndd and ndd < today:
            e_warn.append(f"cached next-earnings date {nd} has already passed — not shown")
            nd = None
    if not nd and ec.get("date") and ec.get("date") >= today.isoformat():
        nd, e_src = ec["date"], ec.get("source", "Yahoo")
        tag = "estimated" if ec.get("estimate", True) else "confirmed"
    elif nd:
        if ec.get("date") == nd and ec.get("estimate") is False:
            tag = "confirmed"
            e_src += " + Yahoo flag (confirmed per Yahoo — company-announced date)"
        else:
            tag = "estimated"
            if ec.get("date") and ec.get("date") != nd:
                e_warn.append(f"sources disagree: Finnhub {nd} vs Yahoo {ec['date']}"
                              f" ({'estimate' if ec.get('estimate', True) else 'confirmed'})")
    days = (dt.date.fromisoformat(nd) - today).days if nd else None
    add(_field("earnings_next", "Next earnings", nd,
               f"{nd} ({days}d) · {tag}" if nd else "—", e_src, today.isoformat(),
               "confirmed = company-announced; estimated = vendor projection",
               "next scheduled report date", warnings=e_warn, tag=tag, days_until=days))

    # ── your position (one price basis) ───────────────────────────────────────
    pos = inp.get("position") or {}
    acct = inp.get("account") or {}
    if pos.get("owned"):
        qty = _f(pos.get("qty")) or 0.0
        raw["qty"] = qty
        val = qty * price if price else None
        cb = _f(pos.get("cost_basis"))
        pl = val - cb if val is not None and cb is not None else None
        pl_pct = pl / cb * 100 if pl is not None and cb else None
        dma = b.get("day_move_abs")
        dpl = qty * dma if dma is not None else None
        held_txt = ("today" if b.get("price_kind") == "live"
                    else f"the {word} session")
        dpl_warn = [f"assumes the shares were held through {held_txt}"]
        bdpl = _f(pos.get("day_pl_broker") if "day_pl_broker" in pos else pos.get("day_pl"))
        if bdpl is not None and val:
            if abs(bdpl) > val:
                dpl_warn.append(f"broker day P/L {signed_money(bdpl)} exceeds the whole "
                                f"position value {money(val)} — broker figure rejected")
            elif dpl is not None and abs(bdpl - dpl) > max(1.0, 0.02 * abs(dpl)):
                dpl_warn.append(f"broker reports {signed_money(bdpl)} (different session "
                                "or intraday trades) — not used")
        bmv = _f(pos.get("market_value"))
        v_warn = []
        if bmv and val and abs(bmv / val - 1) > 0.005:
            v_warn.append(f"broker values it at {money(bmv)} (its own mark) — "
                          "revalued at the report price")
        tot = _f(acct.get("total_value"))
        weight = (val / (tot - (bmv or 0) + val) * 100) if tot and val else None
        raw.update({"position_value": val, "position_day_pl": dpl})
        add(_field("position_value", "Position value", val,
                   f"{qty:,.0f} sh × {money(price)} = {money(val)}" if val is not None else "—",
                   "broker share count × report price", b.get("price_label"), "report price",
                   "shares × report price", warnings=v_warn))
        add(_field("position_pl", "Unrealized P/L", pl,
                   f"{signed_money(pl)} ({pct(pl_pct, 2, True)}) vs cost {money(cb)}"
                   if pl is not None else "—",
                   "broker cost basis", b.get("price_label"), "report price",
                   "value − cost basis"))
        add(_field("position_day_pl", "Day P/L", dpl,
                   f"{signed_money(dpl)} ({word if word == 'today' else word + ' session'})"
                   if dpl is not None else "—",
                   "shares × day move", b.get("session_date"), "official closes",
                   "shares × (price − prior close)", warnings=dpl_warn))
        add(_field("position_weight", "Portfolio weight", weight, pct(weight, 1),
                   "broker account total (revalued)", acct.get("as_of"), "report price",
                   "position value ÷ (account total − broker mark + revalued position)"))
    else:
        link = pos.get("linked")
        add(_field("position_value", "Position value", None,
                   (f"not held — you hold {float(link.get('qty') or 0):,.0f} {link['symbol']} "
                    f"(other share class of the same company), {money(_f(link.get('market_value')))} "
                    f"at {link['symbol']}'s broker price" if link else "not held"),
                   "broker account", acct.get("as_of"), "", "",
                   status="ok", linked=link))

    return {"symbol": sym, "fields": fields, "raw": raw,
            "security_kind": kind, "groups": list(GROUPS),
            "generated_ny": now.isoformat(timespec="minutes")}


# ─── valuation range (docs/VERDICT_SPEC.md §6) ─────────────────────────────────

def _vr_fmt(measure: str | None, v: float | None) -> str:
    if v is None:
        return "—"
    if measure == "distribution_rate":
        return pct(v, 2)
    return ratio(v, 2 if measure == "p_tbv" else 1)


def add_valuation_fields(view: dict[str, Any], vr: dict[str, Any] | None) -> None:
    """Three report fields in group "valuation" from the payload's
    `valuation_range`: `valuation_now` (the measure now), `valuation_points`
    (the year-end points, compact; `items` = the points) and
    `valuation_range` (median / low / high and the grade hint, with the
    warnings). Nothing is added when there is no range at all."""
    if not vr or not vr.get("measure"):
        return
    fields = view.setdefault("fields", {})
    m, label = vr["measure"], vr.get("label") or vr["measure"]
    status = vr.get("status") or "no_data"
    now = vr.get("now")
    now_disp = _vr_fmt(m, now)
    if vr.get("normalized") and vr.get("now_normalized") is not None:
        now_disp += f" ({_vr_fmt(m, vr['now_normalized'])} on the average — normalized)"
    withheld = status == "withheld"
    f_now = _field("valuation_now", f"{label} — now", None if withheld else now,
                   now_disp if now is not None and not withheld else "—",
                   "report fields at the report price", None, vr.get("now_basis") or "",
                   "the report's own figure", status="withheld" if withheld else "ok",
                   measure=m, normalized=bool(vr.get("normalized")),
                   now_normalized=vr.get("now_normalized"))
    pts = vr.get("points") or []
    f_pts = _field("valuation_points", f"{label} — fiscal year-ends",
                   len(pts) if pts and not withheld else None,
                   " · ".join(f"{p['fy_end']} {_vr_fmt(m, p.get('value'))}" for p in pts)
                   if pts and not withheld else "—",
                   "long price history + filed fiscal years", pts[0]["fy_end"] if pts else None,
                   vr.get("points_basis") or "", "newest first, up to five",
                   status="withheld" if withheld else "ok", items=pts)
    rng_ok = status == "ok" and vr.get("median") is not None
    f_rng = _field("valuation_range", f"{label} — five-year range",
                   vr.get("median") if rng_ok else None,
                   (f"median {_vr_fmt(m, vr['median'])} · low {_vr_fmt(m, vr.get('low'))} · "
                    f"high {_vr_fmt(m, vr.get('high'))}"
                    + (f" · grade on the reported figure {vr['grade_hint']}"
                       if vr.get("grade_hint") else "")
                    + (f" · on the five-year average {vr['normalized_grade_hint']}"
                       if vr.get("normalized_grade_hint") else "")
                    + (f" · grade {vr['grade']} (the less favorable)"
                       if vr.get("normalized_grade_hint") and vr.get("grade") else "")
                    if rng_ok else "—"),
                   "TickerBands", None, vr.get("points_basis") or "",
                   ("cheap ≥ 15% above the median; expensive below the low or under "
                    "two-thirds of the median (a yield: higher is cheaper)"
                    if m == "distribution_rate" else
                    "cheap ≤ 85% of the median; expensive above the high or > 1.5× the median"),
                   warnings=vr.get("warnings") or [],
                   status="ok" if rng_ok else ("withheld" if withheld else "no_data"),
                   grade_hint=vr.get("grade_hint"),
                   normalized_grade_hint=vr.get("normalized_grade_hint"),
                   grade=vr.get("grade"), direction=vr.get("direction"),
                   excluded_points=vr.get("excluded_points") or [],
                   low=vr.get("low"), high=vr.get("high"),
                   median=vr.get("median"))
    for f in (f_now, f_pts, f_rng):
        f["group"] = "valuation"
        fields[f["key"]] = f


def add_rule_fields(view: dict[str, Any], ri: dict[str, Any] | None) -> None:
    """One compact report field `rule_inputs` in group "rules" from the
    payload's `rule_inputs` (docs/VERDICT_SPEC.md §6b): the computed soft
    signals that are met / not met / not computable, one line each in
    `items`. Nothing is added when there are no rules."""
    if not ri or not ri.get("rules"):
        return
    rules = ri["rules"]
    met = [r["key"] for r in rules if r.get("fires") is True]
    word = {True: "met", False: "not met", None: "not computed"}
    items = [{"key": r["key"], "label": r.get("label"), "fires": r.get("fires"),
              "summary": r.get("summary") or "", "threshold": r.get("threshold") or "",
              "warnings": r.get("warnings") or []} for r in rules]
    disp = ("met: " + ", ".join(met)) if met else "none met"
    n_null = sum(1 for r in rules if r.get("fires") is None)
    if n_null:
        disp += f" · {n_null} not computed"
    f = _field("rule_inputs", "Soft-signal arithmetic", len(met), disp,
               ri.get("rules") and (rules[0].get("basis") or "TickerBands"), ri.get("as_of"),
               "the last two filed quarters" if ri.get("kind") != "fund" else
               "distribution history",
               "the app's arithmetic for each data-driven soft signal; the research note "
               "writes soft_signals",
               warnings=list(ri.get("warnings") or [])
               + [f"{r['key']}: {word[r.get('fires')]} — {'; '.join(r.get('warnings') or [])}"
                  for r in rules if r.get("warnings")],
               status="ok" if ri.get("status") == "ok" else "no_data",
               items=items, filed=ri.get("filed"))
    f["group"] = "rules"
    view.setdefault("fields", {})[f["key"]] = f


# ─── consistency checks ────────────────────────────────────────────────────────

def _rel(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1e-12)


def apply_checks(view: dict[str, Any]) -> list[dict[str, Any]]:
    """Re-derive the core identities from raw inputs; a failure withholds the
    field(s) and names the two numbers that disagree. Mutates `view`."""
    f, r = view["fields"], view["raw"]
    out: list[dict] = []

    def val(k):
        x = f.get(k)
        return x["value"] if x and x["status"] == "ok" else None

    def check(name: str, keys: list[str], ok: bool, detail: str) -> None:
        out.append({"check": name, "fields": keys, "ok": ok, "detail": detail})
        if not ok:
            for k in keys:
                if k in f:
                    _withhold(f[k], f"check failed: {detail}")

    price = r.get("price")
    mcap = val("market_cap")
    if mcap is not None and price and r.get("shares"):
        exp = price * r["shares"]
        check("market_cap", ["market_cap"], _rel(mcap, exp) <= 0.01,
              f"market cap {big_money(mcap)} vs price × shares {big_money(exp)}")
    pe = val("pe_ttm")
    if pe is not None and price and r.get("eps"):
        exp = price / r["eps"]
        check("pe", ["pe_ttm"], _rel(pe, exp) <= 0.01,
              f"P/E {pe:.2f} vs price ÷ EPS {exp:.2f}")
    pf = val("p_fcf")
    if pf is not None and mcap and r.get("fcf"):
        exp = mcap / r["fcf"]
        check("p_fcf", ["p_fcf"], _rel(pf, exp) <= 0.01,
              f"P/FCF {pf:.2f} vs market cap ÷ FCF {exp:.2f}")
    ev = val("ev")
    if ev is not None and mcap is not None and r.get("debt") is not None and r.get("cash_st") is not None:
        exp = mcap + r["debt"] - r["cash_st"] - (r.get("equity_securities") or 0.0)
        check("ev", ["ev"], _rel(ev, exp) <= 0.001,
              f"EV {big_money(ev)} vs market cap + debt − cash & securities {big_money(exp)}")
    dm = val("day_move")
    if dm is not None and price and r.get("prior_close"):
        exp = (price / (r["prior_close"] - (r.get("ex_div_amount") or 0.0)) - 1) * 100
        check("day_move", ["day_move", "day_move_sigma"], abs(dm - exp) <= 0.01,
              f"day move {dm:+.2f}% vs price ÷ prior close {exp:+.2f}%")
    zf = f.get("day_move_sigma")
    if zf and zf["status"] == "ok" and zf["value"] is not None and dm is not None:
        sig = zf.get("daily_sigma_pct")
        z = zf["value"]
        ok = True
        detail = ""
        if sig:
            if abs(dm / sig - z) > 0.05:
                ok, detail = False, f"σ-move {z:+.2f} vs move ÷ σ {dm / sig:+.2f}"
        if ok and bool(zf.get("outside_band")) != (abs(z) >= BAND_Z - 1e-9):
            ok, detail = False, f"band status '{'outside' if zf.get('outside_band') else 'inside'}' vs |z| {abs(z):.2f}"
        check("sigma_band", ["day_move_sigma"], ok, detail or f"z {z:+.2f} consistent with {dm:+.2f}%")
    pv = val("position_value")
    if pv is not None and price and r.get("qty"):
        exp = r["qty"] * price
        check("position_value", ["position_value", "position_pl", "position_weight"],
              abs(pv - exp) <= 0.005 + 1e-9 * abs(exp),
              f"position value {money(pv)} vs shares × price {money(exp)}")
    dpl = val("position_day_pl")
    if dpl is not None and pv is not None:
        check("day_pl_bound", ["position_day_pl"], abs(dpl) <= pv,
              f"day P/L {signed_money(dpl)} vs position value {money(pv)}")
    en = f.get("earnings_next")
    if en and en["value"] is not None:
        check("earnings_tag", ["earnings_next"], en.get("tag") in ("confirmed", "estimated"),
              f"earnings date {en['value']} has no confirmed/estimated tag")
    view["checks"] = out
    return out


def data_as_of(view: dict, basis: dict, statements: dict | None, smeta: dict,
               headlines_view: dict, claude_news: dict | None,
               news_window: dict) -> dict[str, str]:
    """The three facts at the top of the report."""
    st = statements or {}
    q_end = st.get("latest_quarter_end")
    stmt = ("statements unavailable" if not st else
            f"TTM through quarter ended {q_end or '?'} ({st.get('source', '?')}, fetched "
            f"{smeta.get('fetched_date') or st.get('as_of') or '?'})"
            + (f" — {smeta['warning']}" if smeta.get("warning") else ""))
    hv = headlines_view or {}
    news = (f"headlines {hv.get('window_from') or '?'} → {hv.get('window_to') or '?'} "
            f"({hv.get('kept', 0)} relevant of {hv.get('fetched', 0)}); research news check "
            f"{(claude_news or {}).get('created_ts', 'none')[:16].replace('T', ' ')}; "
            f"required window {news_window.get('from')} → {news_window.get('to')}")
    return {"price": basis.get("price_label") or "price unavailable",
            "as_of": basis.get("as_of_label") or "",
            "statements": stmt, "news": news}
