"""
providers/yfinance.py — Tier-0 price data (quotes + daily history), no keys.

Purpose: the ZERO-SIGNUP first-run experience — clone, run, see bands and the
screener before creating any account. Uses the community `yfinance` package
(unofficial Yahoo API): fine for personal research, labeled as such, and one
`pip install yfinance` away (kept out of core requirements on purpose).
Brokers/paid feeds are strictly better once configured.
"""
from __future__ import annotations

from typing import Any

from providers.base import ProviderError

name = "yfinance"


def _yf():
    try:
        import yfinance  # lazy: tier-0 only, not a core dependency
        return yfinance
    except ImportError:
        raise ProviderError(
            "unavailable",
            "yfinance not installed — run: pip install yfinance "
            "(or configure a broker/data provider in config.toml)")


def health_check() -> bool:
    try:
        _yf()
        return True
    except ProviderError:
        return False


def fetch_history(symbol: str, days: int = 130) -> list[dict[str, Any]]:
    yf = _yf()
    try:
        df = yf.Ticker(symbol).history(period=f"{max(days + 30, 60)}d",
                                       auto_adjust=False)
    except Exception as e:
        raise ProviderError("unavailable", f"yfinance history: {e}")
    if df is None or df.empty:
        raise ProviderError("no_data", f"no history for {symbol}")
    out = []
    for ts, row in df.tail(days).iterrows():
        out.append({"date": ts.date().isoformat(),
                    "open": float(row["Open"]), "high": float(row["High"]),
                    "low": float(row["Low"]), "close": float(row["Close"]),
                    "volume": int(row.get("Volume") or 0)})
    return out


def fetch_quote(symbol: str) -> dict[str, Any]:
    """Quote synthesized from recent daily bars (yfinance fast_info where
    available). No extended-hours fields — is_extended is always False here."""
    yf = _yf()
    try:
        t = yf.Ticker(symbol)
        fi = getattr(t, "fast_info", None) or {}
        last = float(fi.get("last_price") or 0) or None
    except Exception:
        last = None
    # Previous close comes from the DAILY BARS, not fast_info — Yahoo's
    # fast_info.previous_close is intermittently wrong (observed +29% fake
    # day-changes). Bars are the same source the chart trusts.
    bars = fetch_history(symbol, days=7)
    if not bars and last is None:
        raise ProviderError("not_found", f"no quote for {symbol}")
    if last is None:
        last = bars[-1]["close"]
    import datetime as _dt
    today = _dt.date.today().isoformat()
    if len(bars) >= 2:
        if bars[-1]["date"] == today:
            prev = bars[-2]["close"]          # last belongs to today's session
        elif abs(last - bars[-1]["close"]) / bars[-1]["close"] < 0.001:
            prev = bars[-2]["close"]          # closed market: show last session's move
        else:
            prev = bars[-1]["close"]          # pre-market: vs latest completed close
    else:
        prev = last
    return {
        "symbol": symbol.upper(), "name": symbol.upper(), "last": last,
        "regular_last": last, "regular_change_pct": round((last / prev - 1) * 100, 2),
        "ah_price": None, "ah_change_pct": None, "is_extended": False,
        "open": last, "close_prev": prev, "high_today": last, "low_today": last,
        "week52_high": None, "week52_low": None, "volume": 0,
        "net_change_pct": round((last / prev - 1) * 100, 2),
        "quote_time_ms": 0, "asset_type": "EQUITY",
    }


def fetch_quotes_batch(symbols: list[str]) -> dict[str, dict[str, Any]]:
    out = {}
    for s in symbols:
        try:
            out[s.upper()] = fetch_quote(s)
        except ProviderError:
            continue
    return out


def fetch_dividends(symbol: str) -> dict[str, Any]:
    """Trailing distribution history (ex-dates + amounts), newest last.
    Ground truth for TTM yield and for dividend-adjusting moves/bands —
    vendor 'yield' metrics annualize one payment and break on variable payers.
    """
    yf = _yf()
    import datetime as _dt
    try:
        series = yf.Ticker(symbol).dividends  # pandas Series, index = ex-date
    except Exception as e:
        raise ProviderError("unavailable", f"yfinance dividends: {e}")
    events = []
    try:
        cutoff = _dt.date.today() - _dt.timedelta(days=3 * 365)
        for idx, val in series.items():
            d = idx.date() if hasattr(idx, "date") else idx
            if d >= cutoff and float(val) > 0:
                events.append({"date": d.isoformat(), "amount": round(float(val), 6)})
    except Exception as e:
        raise ProviderError("unavailable", f"yfinance dividends parse: {e}")
    # Yahoo publishes the UPCOMING ex-DATE (quoteSummary) before the amount
    # lands in the history feed — INSW's 9/10 special was invisible in
    # .dividends on the day itself. Date-only knowledge still lets the UI
    # warn instead of rendering a fake sell-off.
    next_ex = None
    try:
        epoch = (yf.Ticker(symbol).info or {}).get("exDividendDate")
        if epoch:
            next_ex = _dt.datetime.fromtimestamp(
                float(epoch), _dt.timezone.utc).date().isoformat()
    except Exception:
        pass
    return {"available": True, "events": events, "source": "yfinance",
            "next_ex_date": next_ex, "as_of": _dt.date.today().isoformat()}


def fetch_eps_ttm(symbol: str) -> dict[str, Any]:
    """Statement-derived trailing-12-month EPS (Yahoo quoteSummary
    trailingEps) — the cross-check that catches stale vendor FY EPS."""
    yf = _yf()
    import datetime as _dt
    try:
        info = yf.Ticker(symbol).info or {}
        eps = info.get("trailingEps")
    except Exception as e:
        raise ProviderError("unavailable", f"yfinance eps: {e}")
    if eps is None:
        raise ProviderError("no_data", f"no trailing EPS for {symbol}")
    return {"eps_ttm": round(float(eps), 4), "source": "yfinance trailingEps",
            "as_of": _dt.date.today().isoformat()}


def _row(df, *names):
    """First matching row from a yfinance statement DataFrame (labels vary
    by issuer/version); values newest-first. None-safe."""
    try:
        for n in names:
            if n in df.index:
                return [None if v != v else float(v) for v in df.loc[n].tolist()]
    except Exception:
        pass
    return None


def _first_complete(df, row_names: list[tuple]) -> int | None:
    """Index of the newest column where every required row has a value. A
    freshly-added quarter column is often PARTIAL (EPS or revenue only); TTM
    sums must start at the newest COMPLETE quarter, not the newest column."""
    rows = [_row(df, *names) for names in row_names]
    if any(r is None for r in rows):
        return None
    n = min(len(r) for r in rows)
    for i in range(n):
        if all(r[i] is not None for r in rows):
            return i
    return None


def _col_date(df, i: int | None) -> str | None:
    try:
        return df.columns[i].date().isoformat() if i is not None else None
    except Exception:
        return None


def fetch_statements(symbol: str) -> dict[str, Any]:
    """Compact statement aggregates: TTM (4 quarters) for the income and
    cash-flow statements + up to 5 reported FYs + the latest balance sheet.

    Each statement is aligned SEPARATELY to its newest complete quarter and
    reports its own period end (`periods`) — Yahoo updates them on different
    days (NVDA 11/21: Q3 income statement present, Q3 cash flow not)."""
    yf = _yf()
    import datetime as _dt
    t = yf.Ticker(symbol)
    try:
        qcf, acf = t.quarterly_cashflow, t.cashflow
        qis, ais = t.quarterly_income_stmt, t.income_stmt
        qbs = getattr(t, "quarterly_balance_sheet", None)
        bs = t.balance_sheet
    except Exception as e:
        raise ProviderError("unavailable", f"yfinance statements: {e}")
    if qcf is None or getattr(qcf, "empty", True):
        raise ProviderError("no_data", f"no statements for {symbol} (funds/ETFs don't file)")

    REV = ("Total Revenue", "Operating Revenue")
    NI = ("Net Income", "Net Income Common Stockholders")
    OCF = ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
    CAPEX = ("Capital Expenditure",)
    i_inc = _first_complete(qis, [REV, NI])
    i_cf = _first_complete(qcf, [OCF, CAPEX])

    def rows_from(df, i, *names):
        r = _row(df, *names)
        return r[i:] if r is not None and i is not None else None

    def _sum4(rows):
        """TTM = exactly four reported quarters; fewer → None."""
        vals = [v for v in (rows or [])[:4] if v is not None]
        return sum(vals) if len(vals) == 4 else None

    def _at(rows, i):
        return rows[i] if rows and i < len(rows) else None

    q_rev = rows_from(qis, i_inc, *REV)
    q_gp = rows_from(qis, i_inc, "Gross Profit")
    q_ni = rows_from(qis, i_inc, *NI)
    q_ebit = rows_from(qis, i_inc, "Operating Income", "EBIT")
    q_tax = rows_from(qis, i_inc, "Tax Provision")
    q_pre = rows_from(qis, i_inc, "Pretax Income")
    q_eps = rows_from(qis, i_inc, "Diluted EPS", "Basic EPS")
    q_ocf = rows_from(qcf, i_cf, *OCF)
    q_capex = rows_from(qcf, i_cf, *CAPEX)
    q_div = rows_from(qcf, i_cf, "Cash Dividends Paid", "Common Stock Dividend Paid")
    inc_end, cf_end = _col_date(qis, i_inc), _col_date(qcf, i_cf)

    ocf4, capex4 = _sum4(q_ocf), _sum4(q_capex)
    pre4, tax4 = _sum4(q_pre), _sum4(q_tax)
    ttm = {
        "ocf": ocf4, "capex": capex4,
        "fcf": (ocf4 + capex4) if ocf4 is not None and capex4 is not None else None,
        "dividends_paid": abs(_sum4(q_div)) if _sum4(q_div) is not None else None,
        "revenue": _sum4(q_rev), "gross_profit": _sum4(q_gp),
        "net_income": _sum4(q_ni), "operating_income": _sum4(q_ebit),
        "ebit": _sum4(q_ebit), "pretax_income": pre4,
        "eff_tax_rate": (tax4 / pre4 if pre4 not in (None, 0) and tax4 is not None else None),
        "eps_diluted": _sum4(q_eps),
    }

    quarters = []
    inc_dates = [_col_date(qis, j) for j in range(i_inc or 0, len(qis.columns))] if i_inc is not None else []
    cf_dates = ([_col_date(qcf, j) for j in range(i_cf, len(qcf.columns))]
                if i_cf is not None else [])
    for k in range(min(5, len(inc_dates))):
        end = inc_dates[k]
        fcf = None
        if end in cf_dates:
            j = cf_dates.index(end)
            o, c = _at(q_ocf, j), _at(q_capex, j)
            fcf = (o + c) if o is not None and c is not None else None
        quarters.append({
            "end": end, "revenue": _at(q_rev, k), "gross_profit": _at(q_gp, k),
            "operating_income": _at(q_ebit, k), "pretax_income": _at(q_pre, k),
            "net_income": _at(q_ni, k), "eps_diluted": _at(q_eps, k), "fcf": fcf,
        })

    a_rev = _row(ais, "Total Revenue") or []
    a_ni = _row(ais, "Net Income", "Net Income Common Stockholders") or []
    a_ebit = _row(ais, "Operating Income", "EBIT") or []
    a_tax = _row(ais, "Tax Provision") or []
    a_pre = _row(ais, "Pretax Income") or []
    a_ocf = _row(acf, *OCF) or []
    a_capex = _row(acf, *CAPEX) or []
    a_acq = _row(acf, "Purchase Of Business", "Net Business Purchase And Sale") or []
    annual = []
    for i in range(min(5, max(len(a_rev), len(a_ni)))):
        ocf_i, cap_i = _at(a_ocf, i), _at(a_capex, i)
        pre_i, tax_i = _at(a_pre, i), _at(a_tax, i)
        annual.append({
            "revenue": _at(a_rev, i), "net_income": _at(a_ni, i), "ebit": _at(a_ebit, i),
            "eff_tax_rate": (tax_i / pre_i if pre_i not in (None, 0) and tax_i is not None else None),
            "fcf": (ocf_i + cap_i) if ocf_i is not None and cap_i is not None else None,
            "acquisitions": _at(a_acq, i),
        })

    balance, bal_end = {}, None
    try:
        src_bs = qbs if qbs is not None and not getattr(qbs, "empty", True) else bs
        CASH_ST = ("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents")
        i_bs = _first_complete(src_bs, [CASH_ST])
        bal_end = _col_date(src_bs, i_bs)

        def b(*names):
            r = _row(src_bs, *names)
            return r[i_bs] if r is not None and i_bs is not None and i_bs < len(r) else None
        # Yahoo does not separate marketable EQUITY securities reliably (VST's
        # nuclear decommissioning trust landed in its investments rows), so
        # they are not reported from this source at all.
        balance = {"total_debt": b("Total Debt"), "cash": b(*CASH_ST),
                   "cash_st": b(*CASH_ST), "equity_securities": None,
                   "equity": b("Stockholders Equity", "Common Stock Equity",
                               "Total Equity Gross Minority Interest"),
                   "as_of_date": bal_end,
                   "debt_note": "Yahoo 'Total Debt' (may include lease liabilities)"}
    except Exception:
        pass

    periods = {"income": inc_end, "cashflow": cf_end, "balance": bal_end}
    ends = [p for p in periods.values() if p]
    return {"available": True, "ttm": ttm, "annual": annual, "balance": balance,
            "quarters": quarters, "periods": periods,
            "latest_quarter_end": min(ends) if ends else None,
            "source": "Yahoo statements", "as_of": _dt.date.today().isoformat()}


def fetch_earnings_confirmation(symbol: str) -> dict[str, Any]:
    """Yahoo's next-earnings timestamp and its `isEarningsDateEstimate` flag.
    Finnhub's calendar carries no confirmed/estimated marker, and estimated
    dates were being shown as if confirmed (ISTR, MCD, GOOGL, VST — all off
    by days). Yahoo flips the flag to False once the company announces the
    date, so it is the closest keyless proxy for 'company-confirmed'."""
    yf = _yf()
    import datetime as _dt
    try:
        info = yf.Ticker(symbol).info or {}
    except Exception as e:
        raise ProviderError("unavailable", f"yfinance earnings info: {e}")
    ts = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
    if not ts:
        raise ProviderError("no_data", f"no earnings timestamp for {symbol}")
    try:
        d = _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc)
    except Exception as e:
        raise ProviderError("no_data", f"bad earnings timestamp: {e}")
    try:
        from zoneinfo import ZoneInfo
        d = d.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        pass
    est = info.get("isEarningsDateEstimate")
    return {"date": d.date().isoformat(),
            "estimate": (bool(est) if est is not None else True),
            "source": "Yahoo (isEarningsDateEstimate)",
            "as_of": _dt.date.today().isoformat()}


def fetch_rating_changes(symbol: str, days: int = 90) -> dict[str, Any]:
    """Dated analyst rating actions (firm, from → to) from Yahoo. Finnhub's
    free feed is a monthly COUNT snapshot that can sit unchanged while
    downgrades happen (NVDA Nov = Oct, while Hold downgrades were reported).
    Report-only: never feeds the Setup Score."""
    yf = _yf()
    import datetime as _dt
    try:
        df = yf.Ticker(symbol).upgrades_downgrades
    except Exception as e:
        raise ProviderError("unavailable", f"yfinance rating changes: {e}")
    if df is None or getattr(df, "empty", True):
        return {"available": True, "source": "Yahoo", "changes": []}
    cutoff = _dt.date.today() - _dt.timedelta(days=days)
    out = []
    for idx, row in df.iterrows():
        try:
            d = idx.date() if hasattr(idx, "date") else _dt.date.fromisoformat(str(idx)[:10])
        except Exception:
            continue
        if d < cutoff:
            continue
        out.append({"date": d.isoformat(), "firm": str(row.get("Firm") or ""),
                    "action": str(row.get("Action") or ""),
                    "from": str(row.get("FromGrade") or ""),
                    "to": str(row.get("ToGrade") or "")})
    out.sort(key=lambda r: r["date"], reverse=True)
    return {"available": True, "source": "Yahoo", "changes": out[:40]}
