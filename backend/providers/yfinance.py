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
        prev = float(fi.get("previous_close") or 0) or None
    except Exception:
        last = prev = None
    if last is None or prev is None:
        bars = fetch_history(symbol, days=3)
        if len(bars) < 2:
            raise ProviderError("not_found", f"no quote for {symbol}")
        last, prev = bars[-1]["close"], bars[-2]["close"]
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
