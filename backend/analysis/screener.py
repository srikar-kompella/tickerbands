"""
analysis/screener.py — P1/P2/P3 aggregation logic (kept out of routes).

Screener philosophy: LIGHTWEIGHT on purpose. Per watchlist symbol it touches
only quote/history/options/earnings (all TTL-cached; news/social/analyst are
skipped — they're the slow, rate-limited calls). The Setup Score column shows
the LAST PERSISTED score, not a fresh one: recomputing would silently burn
Finnhub quota per screener refresh, and a stale score with its timestamp is
more honest than a half-computed fresh one.
"""
from __future__ import annotations

import json
from typing import Any, Callable

import config
from analysis import vol_bands
from cache.store import get_or_fetch
from db import connect
from providers.base import ProviderError


def _cached(section: str, symbol: str, fn: Callable, ttl_key: str, force: bool):
    try:
        return get_or_fetch(f"{section}:{symbol}", config.TTL[ttl_key],
                            lambda: fn(symbol), force=force)["data"]
    except ProviderError:
        return None


def screen_symbol(symbol: str, providers: dict, force: bool = False) -> dict[str, Any]:
    """One watchlist row. Never raises — a symbol that fails everything still
    returns a row (the UI shows dashes, not a hole in the table)."""
    symbol = symbol.upper()
    quote = _cached("quote", symbol, providers["quote"], "quote", force)
    history = _cached("history", symbol, providers["history"], "history", force) or []
    options = _cached("options", symbol, providers["options"], "options", force)
    earnings = _cached("earnings", symbol, providers["earnings"], "earnings", force)

    row: dict[str, Any] = {
        "symbol": symbol,
        "price": quote.get("last") if quote else None,
        "day_pct": quote.get("net_change_pct") if quote else None,
        "band_half_pct": None,
        "implied_move_pct": None,
        "implied_vs_band": None,      # >1 ⇒ options pricing more than recent vol
        "days_to_earnings": (earnings or {}).get("days_until"),
        "yesterday_z": None,
        "outside_band_yesterday": False,   # P4: yesterday broke the 80% band
        "last_score": None,
        "last_score_ts": None,
    }

    bars = vol_bands.completed_bars(history)
    if len(bars) >= 22:
        closes = [b["close"] for b in bars]
        volumes = [b["volume"] for b in bars]
        sig = vol_bands.compute_signals(closes, volumes)
        band = vol_bands.band(sig["prev_close"], sig["rv_20d"])
        row["band_half_pct"] = band["half_width_pct"]
        # Yesterday's move in σ: return_1d is close[-1]/close[-2] — exactly the
        # move the band construction targets, so |z| ≥ 1.28 = band break.
        if band["daily_sigma_pct"] > 0:
            z = sig["return_1d"] / band["daily_sigma_pct"]
            row["yesterday_z"] = round(z, 2)
            row["outside_band_yesterday"] = abs(z) >= config.BAND_Z - 1e-9

    if options and options.get("available") and options.get("implied_move_pct") is not None:
        row["implied_move_pct"] = options["implied_move_pct"]
        if row["band_half_pct"]:
            row["implied_vs_band"] = round(options["implied_move_pct"] / row["band_half_pct"], 2)

    conn = connect()
    try:
        last = conn.execute(
            "SELECT score, ts FROM setup_score_history WHERE symbol = ? "
            "ORDER BY ts DESC LIMIT 1", (symbol,)).fetchone()
        if last:
            row["last_score"], row["last_score_ts"] = last["score"], last["ts"]
        st = conn.execute(
            "SELECT stance, created_ts FROM claude_discussions "
            "WHERE ticker = ? AND stance IS NOT NULL "
            "ORDER BY created_ts DESC LIMIT 1", (symbol,)).fetchone()
        row["stance"] = st["stance"] if st else None
        row["stance_ts"] = st["created_ts"] if st else None
    finally:
        conn.close()
    return row


def screen_watchlist(providers: dict, force: bool = False,
                     only: list[str] | None = None) -> list[dict[str, Any]]:
    """All watchlist rows, attention-first: band breaks, then imminent
    earnings, then the rest alphabetically. The UI can re-sort client-side.

    Fetches run on a small thread pool: a 36-symbol cold load is ~140 provider
    calls — serial, that's minutes (reads as 'watchlist is broken'); at 4
    workers it's tens of seconds cold and instant warm (every call is
    TTL-cached). Pool stays small on purpose — Schwab ~120 req/min, Finnhub
    60/min. Each thread opens its own SQLite connection (sqlite3 requirement);
    a symbol that fails still yields a row of dashes, never an exception.
    """
    from concurrent.futures import ThreadPoolExecutor

    conn = connect()
    try:
        items = conn.execute(
            "SELECT symbol, note, tags FROM watchlist ORDER BY symbol").fetchall()
    finally:
        conn.close()
    meta = {i["symbol"]: (i["note"], json.loads(i["tags"] or "[]")) for i in items}
    if only is not None:  # chunked enrichment: only watched symbols, only asked
        meta = {s: meta[s] for s in only if s in meta}

    def one(symbol: str) -> dict[str, Any]:
        try:
            row = screen_symbol(symbol, providers, force)
        except Exception:  # absolute backstop — a hole in the table is worse
            row = {"symbol": symbol, "price": None, "day_pct": None,
                   "band_half_pct": None, "implied_move_pct": None,
                   "implied_vs_band": None, "days_to_earnings": None,
                   "yesterday_z": None, "outside_band_yesterday": False,
                   "last_score": None, "last_score_ts": None}
        row["note"], row["tags"] = meta[symbol]
        return row

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(one, meta.keys()))

    rows.sort(key=lambda r: (
        not r["outside_band_yesterday"],
        r["days_to_earnings"] if r["days_to_earnings"] is not None else 9999,
        r["symbol"],
    ))
    return rows


# ─── P2: income lens over the live account ─────────────────────────────────────

def portfolio_income(account: dict, fundamentals_fn: Callable,
                     force: bool = False) -> dict[str, Any]:
    """Per-holding dividend yield (Finnhub, 24h-cached) → projected annual
    income. This replaces the old app's hand-maintained yield estimates with
    live fundamentals — the whole reason it wasn't blind-ported."""
    holdings = []
    projected = 0.0
    covered_mv = 0.0
    for pos in account.get("positions", []):
        f = _cached("fundamentals", pos["symbol"], fundamentals_fn, "fundamentals", force)
        yld = (f or {}).get("dividend_yield_pct") if (f or {}).get("available") else None
        income = round(pos["market_value"] * yld / 100, 2) if yld else 0.0
        projected += income
        if yld is not None:
            covered_mv += pos["market_value"]
        holdings.append({"symbol": pos["symbol"], "yield_pct": yld,
                         "income_annual": income})
    total = account.get("total_value") or 0.0
    return {
        "holdings": holdings,
        "projected_annual": round(projected, 2),
        "blended_yield_pct": round(projected / total * 100, 2) if total else None,
        # honesty: how much of the portfolio actually had yield data
        "coverage_pct": round(covered_mv / total * 100, 1) if total else None,
    }


# ─── P3: decision journal review ───────────────────────────────────────────────

def decisions_review(quote_fn: Callable) -> list[dict[str, Any]]:
    """Every recorded decision + what the price did since. Uses cached quotes
    (30s TTL) so reviewing 20 decisions doesn't hammer anything."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, ticker, decision, decision_price, summary, created_ts "
            "FROM claude_discussions WHERE decision IS NOT NULL "
            "ORDER BY created_ts DESC").fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        current = None
        q = _cached("quote", r["ticker"], quote_fn, "quote", False)
        if q:
            current = q.get("last")
        since = None
        if current and r["decision_price"]:
            since = round((current - r["decision_price"]) / r["decision_price"] * 100, 2)
        out.append({
            "id": r["id"], "ticker": r["ticker"], "decision": r["decision"],
            "decision_price": r["decision_price"], "current_price": current,
            "pct_since": since, "summary": r["summary"], "created_ts": r["created_ts"],
        })
    return out
