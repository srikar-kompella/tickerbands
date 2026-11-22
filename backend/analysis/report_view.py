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
from typing import Any

from analysis import market_clock as mc
from analysis.fundamentals_ttm import DIVERGENCE_LIMIT, ttm_sum
from analysis.news_relevance import MIN_HEADLINES_FOR_SENTIMENT, MIN_TAGGED_SOCIAL

BAND_Z = 1.28
ANALYST_MAX_AGE_DAYS = 62     # Finnhub monthly snapshot older than ~2 months = stale
NON_OP_FLAG_SHARE = 0.05      # non-operating items ≥ 5% of pretax income → flag
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

    def add(f: dict) -> None:
        fields[f["key"]] = f

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
        sig_txt = (f"{mv['z']:+.2f}σ · {'outside' if mv.get('outside_band') else 'inside'}"
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
    add(_field("market_cap", "Market cap", mcap, big_money(mcap),
               f"shares outstanding {shares_m:,.0f}M ({inp.get('shares_source') or 'vendor profile'})"
               if shares_m else "shares outstanding unavailable",
               b.get("price_label"), "report price", "price × shares outstanding",
               warnings=mc_warn))

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
            eps_warn.append(f"{s} reports {round(v, 2)}"
                            + ("" if "filed statements" in s else " (may predate the latest quarter)"))
    eps_status = "withheld" if any(">50%" in w for w in eps_warn) else "ok"
    raw["eps"] = eps if eps_status == "ok" else None
    add(_field("eps_ttm", "EPS (TTM)", eps, money(eps), eps_src,
               periods.get("income") if eps_src.startswith("sum") else (inp.get("eps_check") or {}).get("as_of"),
               "trailing twelve months", "sum of the last 4 quarters' diluted EPS",
               warnings=eps_warn + (s_warn("income") if eps_src.startswith("sum") else []),
               status=eps_status))

    # non-operating items inflating net income
    pre, op = _f(ttm.get("pretax_income")), _f(ttm.get("operating_income"))
    non_op = (pre - op) if pre is not None and op is not None else None
    non_op_warn = None
    if non_op is not None and pre and pre > 0 and non_op / pre >= NON_OP_FLAG_SHARE:
        non_op_warn = (f"TTM pretax income includes {big_money(non_op)} of non-operating "
                       f"items ({non_op / pre * 100:.0f}% — e.g. gains on equity "
                       "securities); net income, net margin and P/E are flattered")

    pe = price / eps if price and eps and eps > 0 and eps_status == "ok" else None
    pe_warn = list(eps_warn) + ([non_op_warn] if non_op_warn else [])
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
               warnings=s_warn("cashflow")))
    p_fcf = mcap / fcf if mcap and fcf and fcf > 0 else None
    add(_field("p_fcf", "P/FCF (TTM)", p_fcf, ratio(p_fcf), "market cap ÷ TTM FCF",
               b.get("price_label"), f"report price; {s_basis('cashflow')}",
               "market cap ÷ TTM free cash flow",
               warnings=s_warn("cashflow") + (["FCF ≤ 0 — P/FCF undefined"] if fcf is not None and fcf <= 0 else [])))

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
    add(_field("ev", "Enterprise value", ev, big_money(ev),
               f"market cap + {s_src('balance')}",
               b.get("price_label"), f"report price; {s_basis('balance')}",
               "market cap + total debt − cash & short-term investments − marketable securities"
               + debt_note, warnings=s_warn("balance") + debt_missing))
    add(_field("net_cash", "Net cash", net_cash,
               (f"{big_money(net_cash)} ({breakdown})" if net_cash is not None else "—"),
               s_src("balance"), bal.get("as_of_date") or periods.get("balance"),
               s_basis("balance"),
               "cash & short-term investments + marketable securities − total debt" + debt_note,
               warnings=s_warn("balance")))

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
        add(_field("gross_margin", "Gross margin (TTM)", gm, pct(gm, 1), inc_src, inc_p,
                   inc_b, "TTM gross profit ÷ TTM revenue", warnings=inc_w))
        add(_field("operating_margin", "Operating margin (TTM)", om, pct(om, 1), inc_src,
                   inc_p, inc_b, "TTM operating income ÷ TTM revenue", warnings=inc_w))
        add(_field("net_margin", "Net margin (TTM)", nm, pct(nm, 1), inc_src, inc_p,
                   inc_b, "TTM net income ÷ TTM revenue",
                   warnings=inc_w + ([non_op_warn] if non_op_warn else [])))
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
    add(_field("revenue_growth", "Revenue growth (TTM YoY)", rg, pct(rg, 1),
               "Finnhub vendor metric", vmeta.get("fetched_date"),
               "vendor TTM vs prior TTM", "vendor-computed (not recomputed here)",
               warnings=([f"vendor data fetched {vmeta.get('fetched_date')}, before the "
                          f"{smeta.get('last_earnings')} earnings release — may not include "
                          "the latest quarter"] if vmeta.get("predates_earnings") else [])))

    # dividends: forward and trailing, each at the report price
    dv = inp.get("dividends") or {}
    events = dv.get("events") or []
    freq = dividend_frequency(events) if events else None
    last_amt = None
    if events:
        last_amt = float(sorted(events, key=lambda e: str(e.get("date")))[-1].get("amount") or 0) or None
    fwd = (last_amt * freq / price * 100) if last_amt and freq and price else None
    trailing_paid = ttm_sum(events, today) if dv.get("available") else None
    trl = trailing_paid / price * 100 if trailing_paid is not None and price else None
    if dv.get("available"):
        if not events:
            fwd = 0.0          # a non-payer's yield is a real zero, not missing
        add(_field("div_yield_forward", "Dividend yield (forward)", fwd,
                   pct(fwd) if fwd is not None else ("0.00%" if not events else "—"),
                   f"{dv.get('source', '?')} distribution history", dv.get("as_of"),
                   f"latest declared ${last_amt} × {freq}/yr at the report price" if fwd else
                   "report price",
                   "latest declared distribution × payments per year ÷ price",
                   warnings=([] if fwd is not None or not events else
                             ["payment schedule irregular — forward yield not computed"])))
        add(_field("div_yield_trailing", "Dividend yield (trailing 12 mo)", trl, pct(trl),
                   f"{dv.get('source', '?')} distribution history", dv.get("as_of"),
                   f"${trailing_paid:.2f} paid in the last 12 months at the report price"
                   if trailing_paid is not None else "report price",
                   "sum of distributions with ex-date in the last 365 days ÷ price"))
    else:
        v = vendor.get("dividend_yield_pct")
        add(_field("div_yield_forward", "Dividend yield (forward)", None, "—",
                   "no distribution history", None, "", "",
                   warnings=([f"vendor yield {v}% cannot be verified at the report price — "
                              "not shown"] if v is not None else [])))
        add(_field("div_yield_trailing", "Dividend yield (trailing 12 mo)", None, "—",
                   "no distribution history", None, "", ""))

    # ── context ─────────────────────────────────────────────────────────────
    beta = inp.get("beta") or {}
    add(_field("beta", "Beta vs SPY", beta.get("beta"),
               ratio(beta.get("beta"), 2, ""), "TickerBands, computed from daily bars",
               b.get("prior_date") if b.get("price_kind") == "live" else b.get("session_date"),
               f"{beta.get('window', 60)} daily returns vs SPY",
               "OLS slope of daily returns vs SPY daily returns"))
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
        add(_field("atm_iv", "ATM implied vol", o.get("atm_iv_pct"), pct(o.get("atm_iv_pct"), 1),
                   "broker option chain", o_asof, "nearest expiry ≤45d", "avg of ATM call/put IV"))
        im = o.get("implied_move_pct")
        add(_field("implied_move", f"Implied move ({o.get('implied_move_dte', '—')}d)", im,
                   f"±{im:.2f}%" if im is not None else "—", "broker option chain", o_asof,
                   "ATM straddle ÷ underlying", "(ATM call + ATM put) ÷ spot"))
        ivb = (im / band["half_width_pct"]) if im is not None and band.get("half_width_pct") else None
        add(_field("implied_vs_band", "Implied move vs our band", ivb, ratio(ivb, 2, "×"),
                   "derived", o_asof, "", "implied move ÷ band half-width"))

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
        reg_parts.append("SEC filings: " + "; ".join(
            f"{f['date']} {f['form']} ({f.get('desc')})" for f in fil[:5]))
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
    add(_field("claude_stance", "Research stance", cs and cs.get("stance"),
               (f"{(cs.get('stance') or '').upper()} · {cs.get('stance_horizon') or ''} — "
                f"{cs.get('stance_note') or ''}" if cs else "—"),
               "stored research discussion", cs and cs.get("created_ts"),
               "reasoned judgment, dated", "written by the research session; not computed",
               warnings=flags.get("stance") or []))

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
        return "down" if a.startswith("down") else "up" if a.startswith("up") else a
    n_down = sum(1 for c in recent if _dir(c) == "down")
    n_up = sum(1 for c in recent if _dir(c) == "up")
    add(_field("analyst_actions", "Analyst rating changes (30 days)",
               len(recent) if rc else None,
               (f"{n_down} downgrade(s), {n_up} upgrade(s), {len(recent) - n_down - n_up} other"
                + ("".join(f"; {c['date'][5:]} {c['firm']} {c['from'] or '?'}→{c['to']}"
                           for c in recent[:4])) if rc else "—"),
               f"{rc.get('source', 'Yahoo')} rating actions", today.isoformat(),
               "dated firm-level actions", "count by action",
               status="ok" if rc else "no_data", items=recent[:10]))
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
            "generated_ny": now.isoformat(timespec="minutes")}


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
