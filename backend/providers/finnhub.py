"""
providers/finnhub.py — news sentiment, headlines, fundamentals, analyst
recommendation trends, and earnings data. Free tier throughout (60 calls/min).

Adapted from the experiment's scripts/finnhub_data.py (read-only source):
ProviderError instead of silent Nones where the *section* should say why it's
missing, plus four new endpoints the experiment never needed.

Q4 ruling: analyst data = /stock/recommendation ONLY. No price targets
(paid-tier), no yfinance. D12-consistent: month-over-month CHANGES in the
buy/hold/sell mix carry the information; levels are stale.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import requests

from providers.base import ProviderError, get_secret

name = "finnhub"
BASE = "https://finnhub.io/api/v1"


def _key() -> str:
    key = get_secret("finnhub_api_key", "FINNHUB_KEY")
    if not key:
        raise ProviderError("auth_expired", "finnhub_api_key not in Keychain/env")
    return key


def health_check() -> bool:
    try:
        _key()
        return True
    except ProviderError:
        return False


def _get(path: str, params: dict) -> Any:
    """One GET with a single polite retry on 429 (free tier is 60/min)."""
    params = {**params, "token": _key()}
    try:
        r = requests.get(f"{BASE}/{path}", params=params, timeout=8)
        if r.status_code == 429:
            import time
            time.sleep(1.2)
            r = requests.get(f"{BASE}/{path}", params=params, timeout=8)
        if r.status_code == 429:
            raise ProviderError("rate_limited", "Finnhub 429 after retry")
        if r.status_code == 403:
            # Free tier doesn't cover this endpoint/symbol — treat as no_data,
            # not auth failure (key itself is fine).
            raise ProviderError("no_data", f"{path}: 403 (not in free tier?)")
        if r.status_code != 200:
            raise ProviderError("unavailable", f"{path}: HTTP {r.status_code}")
        return r.json()
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError("unavailable", f"{path}: {e}")


# ─── symbol search (name → ticker lookup for the UI search bars) ───────────────

def fetch_symbol_search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Free-text lookup: "apple" → AAPL. Finnhub /search (free tier).

    Filtering is pragmatic, not perfect: Finnhub returns foreign listings and
    derivatives too. We keep rows whose symbol == displaySymbol (drops most
    cross-listings), drop anything with ':' or '=' (indices/FX), and cap at
    `limit`, trusting Finnhub's own relevance ordering.
    """
    q = (query or "").strip()
    if len(q) < 2:
        return []
    raw = _get("search", {"q": q})
    out: list[dict[str, Any]] = []
    for item in (raw or {}).get("result", []):
        sym = (item.get("symbol") or "").upper()
        if not sym or sym != (item.get("displaySymbol") or "").upper():
            continue
        if any(ch in sym for ch in (":", "=", " ")):
            continue
        out.append({
            "symbol": sym,
            "description": (item.get("description") or "").title(),
            "type": item.get("type") or "",
        })
        if len(out) >= limit:
            break
    return out


# ─── news sentiment (same endpoint the experiment used) ────────────────────────

def fetch_news_sentiment(symbol: str) -> dict[str, Any]:
    raw = _get("news-sentiment", {"symbol": symbol})
    if not isinstance(raw, dict) or not raw.get("symbol"):
        # Finnhub returns {} for unknown/uncovered symbols (ETFs often)
        return {"available": False}
    sentiment = raw.get("sentiment") or {}
    buzz = raw.get("buzz") or {}
    return {
        "available": True,
        "bullish_pct": float(sentiment.get("bullishPercent") or 0.0),
        "bearish_pct": float(sentiment.get("bearishPercent") or 0.0),
        "buzz": float(buzz.get("buzz") or 0.0),          # week vs weekly avg
        "articles_week": int(buzz.get("articlesInLastWeek") or 0),
        "news_score": float(raw.get("companyNewsScore") or 0.0),
        "sector_score": float(raw.get("sectorAverageNewsScore") or 0.0),
    }


# ─── headlines (Q1: a bare "62% bullish" is uninspectable — show the articles) ─

def fetch_headlines(symbol: str, limit: int = 5) -> list[dict[str, Any]]:
    today = dt.date.today()
    raw = _get("company-news", {
        "symbol": symbol,
        "from": (today - dt.timedelta(days=7)).isoformat(),
        "to": today.isoformat(),
    })
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:limit]:
        out.append({
            "headline": item.get("headline") or "",
            "source": item.get("source") or "",
            "url": item.get("url") or "",
            "ts": int(item.get("datetime") or 0),
            "summary": (item.get("summary") or "")[:280],
        })
    return out


# ─── fundamentals ──────────────────────────────────────────────────────────────

def fetch_fundamentals(symbol: str) -> dict[str, Any]:
    """Core valuation/quality metrics from /stock/metric (free tier)."""
    raw = _get("stock/metric", {"symbol": symbol, "metric": "all"})
    m = (raw or {}).get("metric") or {}
    if not m:
        return {"available": False}

    def num(key: str) -> float | None:
        v = m.get(key)
        return float(v) if isinstance(v, (int, float)) else None

    return {
        "available": True,
        "pe_ttm": num("peTTM") or num("peBasicExclExtraTTM"),
        "eps_ttm": num("epsTTM") or num("epsBasicExclExtraItemsTTM"),
        "market_cap_m": num("marketCapitalization"),   # in $M per Finnhub
        "revenue_growth_ttm_pct": num("revenueGrowthTTMYoy"),
        "gross_margin_pct": num("grossMarginTTM"),
        "operating_margin_pct": num("operatingMarginTTM"),
        "net_margin_pct": num("netProfitMarginTTM"),
        "dividend_yield_pct": num("currentDividendYieldTTM") or num("dividendYieldIndicatedAnnual"),
        "beta_reported": num("beta"),  # shown alongside our computed beta (S5)
    }


# ─── analyst recommendation trends (Q4 — the ONLY analyst data source) ─────────

def fetch_recommendations(symbol: str) -> dict[str, Any]:
    """Monthly buy/hold/sell counts, newest first.

    We keep the two most recent months so setup_score can use the MoM *change*
    (D12: rating changes carry residual information; levels are priced in).
    """
    raw = _get("stock/recommendation", {"symbol": symbol})
    if not isinstance(raw, list) or not raw:
        return {"available": False}
    months = []
    for row in raw[:3]:
        total = sum(int(row.get(k) or 0) for k in
                    ("strongBuy", "buy", "hold", "sell", "strongSell"))
        months.append({
            "period": row.get("period"),
            "strong_buy": int(row.get("strongBuy") or 0),
            "buy": int(row.get("buy") or 0),
            "hold": int(row.get("hold") or 0),
            "sell": int(row.get("sell") or 0),
            "strong_sell": int(row.get("strongSell") or 0),
            "total": total,
        })
    return {"available": True, "months": months}


# ─── earnings (S1 — protects the vol band from its known failure mode) ─────────

def fetch_earnings(symbol: str) -> dict[str, Any]:
    """Next earnings date + last-4-quarters surprise history.

    The band is calibrated *unconditionally*; earnings days are exactly when
    ±1.28σ breaks. "Earnings in 3d" beside the band converts the band's worst
    failure mode into a feature (DESIGN_QUESTIONS S1).
    """
    out: dict[str, Any] = {"available": False, "next_date": None,
                           "days_until": None, "surprises": []}
    today = dt.date.today()

    # Upcoming date via the earnings calendar (free tier)
    try:
        cal = _get("calendar/earnings", {
            "symbol": symbol,
            "from": today.isoformat(),
            "to": (today + dt.timedelta(days=120)).isoformat(),
        })
        events = (cal or {}).get("earningsCalendar") or []
        dates = sorted(e.get("date") for e in events if e.get("date"))
        if dates:
            nxt = dt.date.fromisoformat(dates[0])
            out["next_date"] = nxt.isoformat()
            out["days_until"] = (nxt - today).days
            out["available"] = True
    except ProviderError:
        pass  # calendar gone from free tier ⇒ degrade to surprises only

    # Past surprises
    try:
        hist = _get("stock/earnings", {"symbol": symbol})
        if isinstance(hist, list):
            for q in hist[:4]:
                est, act = q.get("estimate"), q.get("actual")
                out["surprises"].append({
                    "period": q.get("period"),
                    "estimate": float(est) if est is not None else None,
                    "actual": float(act) if act is not None else None,
                    "surprise_pct": (
                        round((act - est) / abs(est) * 100, 1)
                        if isinstance(est, (int, float)) and isinstance(act, (int, float)) and est
                        else None
                    ),
                })
            if out["surprises"]:
                out["available"] = True
    except ProviderError:
        pass

    if not out["available"]:
        raise ProviderError("no_data", f"no earnings data for {symbol}")
    return out


def fetch_past_earnings_dates(symbol: str, years: int = 2) -> list[dict[str, Any]]:
    """Past announcement DATES + timing (bmo/amc) — the input the historical
    earnings-move calculation needs (quarter labels alone don't say when the
    market reacted)."""
    today = dt.date.today()
    raw = _get("calendar/earnings", {
        "symbol": symbol,
        "from": (today - dt.timedelta(days=365 * years)).isoformat(),
        "to": today.isoformat(),
    })
    events = (raw or {}).get("earningsCalendar") or []
    out = [{"date": e.get("date"), "hour": e.get("hour") or ""}
           for e in events if e.get("date")]
    out.sort(key=lambda x: x["date"])
    return out


def fetch_shares_outstanding(symbol: str) -> float | None:
    """Shares outstanding (millions) from /stock/profile2 (free tier) — used
    to express FINRA short interest as % of shares outstanding."""
    raw = _get("stock/profile2", {"symbol": symbol})
    v = (raw or {}).get("shareOutstanding")
    return float(v) if isinstance(v, (int, float)) and v > 0 else None


def fetch_earnings_calendar_market(frm: str, to: str) -> list[dict]:
    """Market-wide earnings events (no symbol filter) — the PEAD screen's feed."""
    raw = _get("calendar/earnings", {"from": frm, "to": to})
    return (raw or {}).get("earningsCalendar") or []


def fetch(ticker: str) -> dict[str, Any]:
    return {
        "news_sentiment": fetch_news_sentiment(ticker),
        "headlines": fetch_headlines(ticker),
        "fundamentals": fetch_fundamentals(ticker),
        "recommendations": fetch_recommendations(ticker),
        "earnings": fetch_earnings(ticker),
    }
