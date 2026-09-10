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
