"""
providers/mock.py — deterministic fixtures matching every real provider's shape.

Two jobs:
  1. Unit tests (composer/API tests run hermetically — no network, no Keychain).
  2. Offline dev: TICKERLENS_MOCK=1 ./run_app.sh gives a fully working UI with
     fake-but-plausible data on any machine.

Determinism: everything derives from md5(symbol), so the same symbol always
produces identical data across processes (the B1-reproducibility lesson —
LOOKAHEAD_AUDIT F5 — applied to fixtures).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
from typing import Any

name = "mock"


def _seed(symbol: str, salt: str = "") -> float:
    """Stable float in [0, 1) per (symbol, salt)."""
    h = hashlib.md5(f"{symbol.upper()}|{salt}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def health_check() -> bool:
    return True


def fetch_history(symbol: str, days: int = 130) -> list[dict[str, Any]]:
    """A plausible daily series: geometric walk with symbol-stable drift/vol.
    Ends yesterday so 'completed bars only' logic has a clean edge to test."""
    base = 20 + _seed(symbol) * 480            # $20–$500
    drift = (_seed(symbol, "drift") - 0.45) * 0.002
    vol = 0.008 + _seed(symbol, "vol") * 0.022  # 0.8%–3% daily
    out = []
    price = base
    today = dt.date.today()
    d = today - dt.timedelta(days=int(days * 1.5) + 7)
    i = 0
    while d < today:  # generate THROUGH yesterday, trim to the last `days`
        if d.weekday() < 5:  # trading days only
            # deterministic pseudo-noise: sin over a symbol-stable phase
            noise = math.sin(_seed(symbol, str(i)) * math.tau) * vol
            price = max(1.0, price * (1 + drift + noise))
            out.append({
                "date": d.isoformat(),
                "open": round(price * 0.998, 2),
                "high": round(price * 1.006, 2),
                "low": round(price * 0.994, 2),
                "close": round(price, 2),
                "volume": int(1e6 * (0.5 + _seed(symbol, f"v{i}"))),
            })
            i += 1
        d += dt.timedelta(days=1)
    # keep the most RECENT `days` bars (matching how the real provider slices
    # candles[-days:]) — filling from the start left mock series ending weeks
    # early at large `days`, which corrupted PEAD day-counts in mock mode.
    return out[-days:]


def fetch_quote(symbol: str) -> dict[str, Any]:
    hist = fetch_history(symbol)
    prev = hist[-1]["close"]
    chg = (_seed(symbol, "today") - 0.5) * 0.03
    last = round(prev * (1 + chg), 2)
    closes = [h["close"] for h in hist]
    # roughly half the mock universe is "in an extended session" so the AH
    # toggle is exercisable offline
    is_ext = _seed(symbol, "ahs") > 0.5
    ah_pct = round((_seed(symbol, "ahm") - 0.5) * 2.4, 2) if is_ext else None
    ah_price = round(last * (1 + (ah_pct or 0) / 100), 2) if is_ext else None
    return {
        "symbol": symbol.upper(),
        "name": f"{symbol.upper()} (mock)",
        "last": last,
        "regular_last": last,
        "regular_change_pct": round(chg * 100, 2),
        "ah_price": ah_price,
        "ah_change_pct": ah_pct,
        "is_extended": is_ext,
        "open": round(prev * (1 + chg / 2), 2),
        "close_prev": prev,
        "high_today": round(max(last, prev) * 1.004, 2),
        "low_today": round(min(last, prev) * 0.996, 2),
        "week52_high": round(max(closes) * 1.02, 2),
        "week52_low": round(min(closes) * 0.98, 2),
        "volume": int(2e6 * (0.5 + _seed(symbol, "qvol"))),
        "net_change_pct": round(chg * 100, 2),
        "quote_time_ms": int(dt.datetime.now().timestamp() * 1000),
        "asset_type": "ETF" if symbol.upper() in {"SPY", "QQQ", "IWM", "XLE", "TLT", "PHYS", "PSLV"} else "EQUITY",
    }


def fetch_quotes_batch(symbols: list[str]) -> dict[str, dict[str, Any]]:
    return {s.upper(): fetch_quote(s) for s in symbols}


def fetch_options_summary(symbol: str) -> dict[str, Any]:
    if symbol.upper() in {"PHYS", "PSLV"}:  # mirror real life: no listed options
        return {"available": False}
    pc = round(0.5 + _seed(symbol, "pc") * 0.9, 3)
    iv = round(15 + _seed(symbol, "iv") * 45, 1)
    return {
        "available": True,
        "put_call_ratio": pc,
        "call_volume": int(5e4 * (0.3 + _seed(symbol, "cv"))),
        "put_volume": int(5e4 * (0.3 + _seed(symbol, "pv")) * pc),
        "atm_iv_pct": iv,
        "implied_move_pct": round(iv / math.sqrt(252) * 2.2, 2),
        "implied_move_dte": 7 + int(_seed(symbol, "dte") * 20),
        "as_of": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }


def fetch_news_sentiment(symbol: str) -> dict[str, Any]:
    bull = round(0.2 + _seed(symbol, "bull") * 0.6, 2)
    return {
        "available": True,
        "bullish_pct": bull,
        "bearish_pct": round(max(0.0, 0.9 - bull - _seed(symbol, "gap") * 0.3), 2),
        "buzz": round(0.4 + _seed(symbol, "buzz") * 2.2, 2),
        "articles_week": int(_seed(symbol, "arts") * 60),
        "news_score": round(_seed(symbol, "ns"), 2),
        "sector_score": 0.52,
    }


def fetch_headlines(symbol: str, limit: int = 5) -> list[dict[str, Any]]:
    now = int(dt.datetime.now().timestamp())
    verbs = ["beats estimates", "announces buyback", "faces probe",
             "upgraded at MockBank", "launches new product"]
    return [{
        "headline": f"{symbol.upper()} {verbs[i % len(verbs)]}",
        "source": "MockWire",
        "url": "https://example.com",
        "ts": now - i * 7200,
        "summary": "Deterministic mock headline for offline development.",
    } for i in range(limit)]


def fetch_fundamentals(symbol: str) -> dict[str, Any]:
    return {
        "available": True,
        "pe_ttm": round(8 + _seed(symbol, "pe") * 55, 1),
        "eps_ttm": round(1 + _seed(symbol, "eps") * 12, 2),
        "market_cap_m": round(2_000 + _seed(symbol, "mc") * 2_800_000, 0),
        "revenue_growth_ttm_pct": round((_seed(symbol, "rg") - 0.25) * 60, 1),
        "gross_margin_pct": round(20 + _seed(symbol, "gm") * 60, 1),
        "operating_margin_pct": round(5 + _seed(symbol, "om") * 35, 1),
        "net_margin_pct": round(2 + _seed(symbol, "nm") * 28, 1),
        "dividend_yield_pct": round(_seed(symbol, "dy") * 4, 2),
        "beta_reported": round(0.5 + _seed(symbol, "beta") * 1.4, 2),
    }


def fetch_recommendations(symbol: str) -> dict[str, Any]:
    """Two months so the MoM-change component has something to chew on."""
    def month(offset: int) -> dict[str, Any]:
        sb = int(_seed(symbol, f"sb{offset}") * 15)
        b = int(_seed(symbol, f"b{offset}") * 20)
        h = int(5 + _seed(symbol, f"h{offset}") * 15)
        s = int(_seed(symbol, f"s{offset}") * 6)
        ss = int(_seed(symbol, f"ss{offset}") * 3)
        period = (dt.date.today().replace(day=1) - dt.timedelta(days=offset * 30))
        return {"period": period.strftime("%Y-%m-01"), "strong_buy": sb, "buy": b,
                "hold": h, "sell": s, "strong_sell": ss,
                "total": sb + b + h + s + ss}
    return {"available": True, "months": [month(0), month(1)]}


def fetch_earnings(symbol: str) -> dict[str, Any]:
    days = 2 + int(_seed(symbol, "edays") * 80)
    nxt = dt.date.today() + dt.timedelta(days=days)
    return {
        "available": True,
        "next_date": nxt.isoformat(),
        "days_until": days,
        "surprises": [{
            "period": f"2026-0{q}-01", "estimate": round(1 + q * 0.1, 2),
            "actual": round(1 + q * 0.1 + (_seed(symbol, f"sur{q}") - 0.4) * 0.3, 2),
            "surprise_pct": round((_seed(symbol, f"sur{q}") - 0.4) * 25, 1),
        } for q in range(1, 5)],
    }


def fetch_social(symbol: str) -> dict[str, Any]:
    bull = int(_seed(symbol, "stb") * 25)
    bear = int(_seed(symbol, "sts") * 12)
    tagged = bull + bear
    return {
        "available": True, "bullish": bull, "bearish": bear, "tagged": tagged,
        "total_msgs": tagged + int(_seed(symbol, "stu") * 10),
        "polarity": round((bull - bear) / tagged, 3) if tagged else 0.0,
        "sample": [{"body": f"mock take on {symbol.upper()}",
                    "sentiment": "bullish", "created_at": "2026-07-12T12:00:00Z"}],
    }


_MOCK_DIRECTORY = [
    ("AAPL", "Apple Inc", "Common Stock"),
    ("MSFT", "Microsoft Corp", "Common Stock"),
    ("NVDA", "Nvidia Corp", "Common Stock"),
    ("GOOGL", "Alphabet Inc Class A", "Common Stock"),
    ("AMZN", "Amazon.Com Inc", "Common Stock"),
    ("META", "Meta Platforms Inc", "Common Stock"),
    ("TSLA", "Tesla Inc", "Common Stock"),
    ("HIMS", "Hims & Hers Health Inc", "Common Stock"),
    ("COIN", "Coinbase Global Inc", "Common Stock"),
    ("SPY", "Spdr S&P 500 Etf Trust", "ETP"),
    ("QQQ", "Invesco Qqq Trust", "ETP"),
    ("PHYS", "Sprott Physical Gold Trust", "ETP"),
]


def fetch_symbol_search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Substring match over a small static directory — deterministic, offline."""
    q = (query or "").strip().lower()
    if len(q) < 2:
        return []
    hits = [
        {"symbol": s, "description": d, "type": t}
        for s, d, t in _MOCK_DIRECTORY
        if q in s.lower() or q in d.lower()
    ]
    return hits[:limit]


def fetch_position(symbol: str) -> dict[str, Any]:
    if _seed(symbol, "own") < 0.5:
        return {"owned": False, "as_of": "2026-07-12T09:30:00"}
    qty = round(10 + _seed(symbol, "qty") * 200, 0)
    price = fetch_quote(symbol)["last"]
    cost = round(price * (0.75 + _seed(symbol, "cost") * 0.4), 2)
    return {
        "owned": True, "qty": qty, "avg_cost": cost,
        "market_value": round(qty * price, 2), "cost_basis": round(qty * cost, 2),
        "gain": round(qty * (price - cost), 2),
        "gain_pct": round((price - cost) / cost * 100, 2),
        "weight_pct": round(2 + _seed(symbol, "w") * 15, 2),
        "as_of": "2026-07-12T09:30:00",
    }


def fetch_account_positions() -> dict[str, Any]:
    """A plausible ~8-holding account, deterministic, consistent with
    fetch_quote prices so the Portfolio tab and Analysis pages agree."""
    holdings = ["MSFT", "GOOGL", "AVUV", "NFLX", "VXUS", "QQQ", "HIMS", "COIN"]
    positions = []
    for sym in holdings:
        q = fetch_quote(sym)
        qty = round(5 + _seed(sym, "aqty") * 60, 0)
        avg = round(q["last"] * (0.7 + _seed(sym, "acost") * 0.5), 2)
        mv = round(qty * q["last"], 2)
        cb = round(qty * avg, 2)
        day_pl = round(mv * q["net_change_pct"] / 100, 2)
        positions.append({
            "symbol": sym, "description": q["name"],
            "asset_type": q["asset_type"], "qty": qty, "avg_cost": avg,
            "market_value": mv, "cost_basis": cb,
            "gain": round(mv - cb, 2),
            "gain_pct": round((mv - cb) / cb * 100, 2) if cb else None,
            "day_pl": day_pl,
            "day_pl_pct": q["net_change_pct"],
        })
    positions.sort(key=lambda x: -x["market_value"])
    cash = 4060.0
    total = sum(p["market_value"] for p in positions) + cash
    day_pl = sum(p["day_pl"] for p in positions)
    for p in positions:
        p["weight_pct"] = round(p["market_value"] / total * 100, 2)
    return {
        "as_of": dt.datetime.now().isoformat(timespec="seconds"),
        "total_value": round(total, 2), "cash": cash,
        "day_pl": round(day_pl, 2),
        "day_pl_pct": round(day_pl / (total - day_pl) * 100, 2),
        "positions": positions,
    }


def fetch_chain_for_lens(symbol: str, dte_max: int = 35) -> dict[str, Any]:
    """Deterministic chain: strikes ±2..12% around spot at 3 expiries, deltas
    from a rough normal approx so implied-vs-empirical edges are non-trivial."""
    if symbol.upper() in {"PHYS", "PSLV"}:
        return {"available": False}
    spot = fetch_quote(symbol)["last"]
    vol_daily = (0.008 + _seed(symbol, "vol") * 0.022)
    calls, puts = [], []
    for dte in (7, 21, 33):
        sd = vol_daily * math.sqrt(dte * 252 / 365)
        for off in (-0.12, -0.08, -0.05, -0.03, -0.02, 0.02, 0.03, 0.05, 0.08, 0.12):
            strike = round(spot * (1 + off), 1)
            z = off / sd if sd > 0 else 99
            p_above = max(0.01, min(0.99, 0.5 * (1 - math.erf(z / math.sqrt(2)))))
            # market slightly OVERPRICES tails (the premium the lens hunts)
            skewed = min(0.99, p_above * (1.18 if abs(off) >= 0.05 else 1.02))
            mid = max(0.05, spot * sd * 0.55 * math.exp(-abs(z)))
            row = {"strike": strike, "expiry": f"2026-08-{10 + dte % 18:02d}",
                   "dte": dte, "bid": round(mid * 0.96, 2), "ask": round(mid * 1.04, 2),
                   "mark": round(mid, 2), "iv_pct": round(vol_daily * math.sqrt(252) * 118, 1),
                   "oi": int(200 + _seed(symbol, f"oi{off}{dte}") * 5000),
                   "volume": int(_seed(symbol, f"v{off}{dte}") * 800)}
            if off > 0:
                calls.append({**row, "side": "call", "delta": round(skewed, 3)})
            else:
                puts.append({**row, "side": "put", "delta": round(-min(0.99, (1 - p_above) * 1.18), 3)})
    return {"available": True, "spot": spot, "calls": calls, "puts": puts}


def fetch_past_earnings_dates(symbol: str, years: int = 2) -> list[dict[str, Any]]:
    """~8 quarterly announcement dates, deterministic, alternating bmo/amc."""
    out = []
    base = dt.date.today() - dt.timedelta(days=10 + int(_seed(symbol, "eoff") * 50))
    for q in range(8):
        d = base - dt.timedelta(days=91 * q)
        out.append({"date": d.isoformat(), "hour": "amc" if q % 2 else "bmo"})
    return sorted(out, key=lambda x: x["date"])


def fetch_shares_outstanding(symbol: str) -> float | None:
    return round(500 + _seed(symbol, "sho") * 5000, 1)  # millions


def fetch_insiders(symbol: str) -> dict[str, Any]:
    clustered = _seed(symbol, "clus") > 0.6
    today = dt.date.today()
    txs = [
        {"owner": "Jane Founder", "title": "CEO", "code": "P",
         "date": (today - dt.timedelta(days=4)).isoformat(),
         "shares": 10000, "price": 42.5, "value": 425000.0},
        {"owner": "Sam Numbers", "title": "CFO",
         "code": "P" if clustered else "S",
         "date": (today - dt.timedelta(days=9)).isoformat(),
         "shares": 3000, "price": 41.0, "value": 123000.0},
        {"owner": "Dee Rector", "title": "Director", "code": "S",
         "date": (today - dt.timedelta(days=40)).isoformat(),
         "shares": 2000, "price": 44.0, "value": 88000.0},
    ]
    net = sum(t["shares"] if t["code"] == "P" else -t["shares"] for t in txs)
    return {"available": True, "transactions": txs, "cluster_buy": clustered,
            "cluster_buyers": ["Jane Founder", "Sam Numbers"] if clustered else [],
            "net_shares_90d": net, "note": "mock — open-market trades only"}


def fetch_short_interest(symbol: str) -> dict[str, Any]:
    si = 1e6 * (2 + _seed(symbol, "si") * 40)
    prev = si / (1 + (_seed(symbol, "sid") - 0.5) * 0.4)
    return {"available": True,
            "settlement_date": (dt.date.today() - dt.timedelta(days=9)).isoformat(),
            "short_interest": round(si), "prev_short_interest": round(prev),
            "change_pct": round((si - prev) / prev * 100, 1),
            "days_to_cover": round(0.5 + _seed(symbol, "dtc") * 8, 1),
            "pct_of_shares_out": None}


def fetch_schwab_watchlists() -> list[dict[str, Any]]:
    """Deterministic pretend-Schwab watchlists for offline validation."""
    return [
        {"name": "Income candidates", "symbols": ["JEPQ", "SPYI", "ARCC", "SCHD"]},
        {"name": "Research", "symbols": ["LLY", "AVGO", "PLTR"]},
    ]


def fetch(ticker: str) -> dict[str, Any]:
    return {
        "quote": fetch_quote(ticker),
        "history": fetch_history(ticker),
        "options": fetch_options_summary(ticker),
        "news_sentiment": fetch_news_sentiment(ticker),
        "headlines": fetch_headlines(ticker),
        "fundamentals": fetch_fundamentals(ticker),
        "recommendations": fetch_recommendations(ticker),
        "earnings": fetch_earnings(ticker),
        "social": fetch_social(ticker),
        "position": fetch_position(ticker),
    }
