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


def fetch_statements(symbol: str) -> dict[str, Any]:
    """Compact statement aggregates for the value lens: TTM (4 quarters) FCF /
    net income / dividends paid / EBIT + up to 5 reported FYs + latest
    balance-sheet debt/cash/equity. All plain numbers, JSON-safe."""
    yf = _yf()
    import datetime as _dt
    t = yf.Ticker(symbol)
    try:
        qcf, acf = t.quarterly_cashflow, t.cashflow
        qis, ais = t.quarterly_income_stmt, t.income_stmt
        bs = t.balance_sheet
    except Exception as e:
        raise ProviderError("unavailable", f"yfinance statements: {e}")
    if qcf is None or getattr(qcf, "empty", True):
        raise ProviderError("no_data", f"no statements for {symbol} (funds/ETFs don't file)")

    def _sum4(rows):
        vals = [v for v in (rows or [])[:4] if v is not None]
        return sum(vals) if vals else None

    q_ocf = _row(qcf, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
    q_capex = _row(qcf, "Capital Expenditure")
    q_div = _row(qcf, "Cash Dividends Paid", "Common Stock Dividend Paid")
    q_ni = _row(qis, "Net Income", "Net Income Common Stockholders")
    q_ebit = _row(qis, "Operating Income", "EBIT")
    q_tax = _row(qis, "Tax Provision")
    q_pre = _row(qis, "Pretax Income")
    ocf4, capex4 = _sum4(q_ocf), _sum4(q_capex)
    ttm = {
        "ocf": ocf4,
        "capex": capex4,
        "fcf": (ocf4 + capex4) if ocf4 is not None and capex4 is not None else None,
        "dividends_paid": abs(_sum4(q_div)) if _sum4(q_div) is not None else None,
        "net_income": _sum4(q_ni),
        "ebit": _sum4(q_ebit),
        "eff_tax_rate": ((_sum4(q_tax) or 0) / _sum4(q_pre)
                         if _sum4(q_pre) not in (None, 0) else None),
    }

    a_rev = _row(ais, "Total Revenue") or []
    a_ni = _row(ais, "Net Income", "Net Income Common Stockholders") or []
    a_ebit = _row(ais, "Operating Income", "EBIT") or []
    a_tax = _row(ais, "Tax Provision") or []
    a_pre = _row(ais, "Pretax Income") or []
    a_ocf = _row(acf, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities") or []
    a_capex = _row(acf, "Capital Expenditure") or []
    a_acq = _row(acf, "Purchase Of Business", "Net Business Purchase And Sale") or []

    def _at(rows, i):
        return rows[i] if i < len(rows) else None

    annual = []
    for i in range(min(5, max(len(a_rev), len(a_ni)))):
        ocf_i, cap_i = _at(a_ocf, i), _at(a_capex, i)
        pre_i, tax_i = _at(a_pre, i), _at(a_tax, i)
        annual.append({
            "revenue": _at(a_rev, i), "net_income": _at(a_ni, i),
            "ebit": _at(a_ebit, i),
            "eff_tax_rate": (tax_i / pre_i if pre_i not in (None, 0)
                             and tax_i is not None else None),
            "fcf": (ocf_i + cap_i) if ocf_i is not None and cap_i is not None else None,
            "acquisitions": _at(a_acq, i),
        })

    balance = {}
    try:
        balance = {
            "total_debt": (_row(bs, "Total Debt") or [None])[0],
            "cash": (_row(bs, "Cash Cash Equivalents And Short Term Investments",
                          "Cash And Cash Equivalents") or [None])[0],
            "equity": (_row(bs, "Stockholders Equity", "Common Stock Equity",
                            "Total Equity Gross Minority Interest") or [None])[0],
        }
    except Exception:
        pass

    return {"available": True, "ttm": ttm, "annual": annual, "balance": balance,
            "source": "yfinance statements", "as_of": _dt.date.today().isoformat()}
