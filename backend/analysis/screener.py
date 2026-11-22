"""
analysis/screener.py — P1/P2/P3 aggregation logic (kept out of routes).

Screener philosophy: LIGHTWEIGHT on purpose. Per watchlist symbol it touches
only quote/history/options/earnings (all TTL-cached; news/social/analyst are
skipped — they're the slow, rate-limited calls). The Setup Score column shows
the LAST PERSISTED score, not a fresh one: recomputing would silently burn
Finnhub quota per screener refresh, and a stale score with its timestamp is
more honest than a half-computed fresh one.

The Claude column carries the same stance the analysis header shows — its
label (AVOID for a `sell` on a name not owned) and its warnings — judged from
what the row already has: the stored discussions (one query), the bars
fetched for the band, and the account and the dividend history as last
cached. No extra provider call.

note_statuses() does the same for the discussion timeline: OK / STALE /
INVALID / SUPERSEDED per stored note, from the daily history and dividends
as last cached — it never fetches.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable

import config
from analysis import market_clock, news_relevance, price_basis, research_quality, vol_bands
from analysis.report_view import SHARE_CLASSES
from cache.store import _get_row, get_or_fetch
from db import connect
from providers.base import ProviderError


def _cached(section: str, symbol: str, fn: Callable | None, ttl_key: str, force: bool):
    if fn is None:          # capability not configured → that column is "—"
        return None
    try:
        return get_or_fetch(f"{section}:{symbol}", config.TTL[ttl_key],
                            lambda: fn(symbol), force=force)["data"]
    except ProviderError:
        return None


HELD_MAX_AGE_SECONDS = 24 * 3600
_NO_STANCE = {"stance": None, "stance_ts": None, "stance_label": None,
              "stance_owned": None, "stance_warnings": []}


def held_symbols() -> set[str] | None:
    """Symbols in the brokerage account AS LAST CACHED (the Portfolio tab and
    every analysis refresh that cache). Read straight from the cache table —
    never fetched here — and ignored once it is a day old. None = unknown."""
    try:
        row = _get_row("portfolio:ACCOUNT")
    except Exception:  # noqa: BLE001 — the label falls back to the note
        return None
    if not row or time.time() - row[1] > HELD_MAX_AGE_SECONDS:
        return None
    return {str(p.get("symbol") or "").upper()
            for p in (row[0] or {}).get("positions") or []}


def cached_dividend_events(symbol: str) -> list[dict]:
    """Ex-dates and amounts from the dividend history AS LAST CACHED by an
    analysis of this ticker (cache key `dividends:{SYMBOL}`), whatever its
    age — past ex-dates do not change. Never fetched here; [] when the
    ticker was never analyzed, and then closes are not dividend-adjusted."""
    try:
        row = _get_row(f"dividends:{symbol.upper()}")
    except Exception:  # noqa: BLE001 — the adjustment is skipped, nothing breaks
        return []
    events = ((row[0] or {}).get("events") if row and isinstance(row[0], dict) else None) or []
    return [e for e in events if isinstance(e, dict)]


def _note_rows(conn, symbol: str) -> list[dict]:
    """One ticker's stored notes, newest first, `research_meta` parsed — the
    same 50 the analysis header judges (composer._discussion_rows)."""
    rows = [dict(r) for r in conn.execute(
        "SELECT id, stance, created_ts, research_meta, news_view, news_note "
        "FROM claude_discussions WHERE ticker = ? "
        "ORDER BY created_ts DESC LIMIT 50", (symbol,))]
    for r in rows:
        try:
            r["research_meta"] = json.loads(r.get("research_meta") or "{}")
        except (TypeError, ValueError):
            r["research_meta"] = {}
        if not isinstance(r["research_meta"], dict):
            r["research_meta"] = {}
    return rows


NO_HISTORY = ("not judged against ≥2σ sessions — no price history is cached for this "
              "ticker; open its analysis")


def note_statuses(tickers, now=None) -> dict[int, dict[str, Any]]:
    """Discussion id → {"status", "reason", "as_of"} for every stored note of
    `tickers`: research_quality.prior_note_status, the same judgment the
    research prompt and the report give each note.

    Built from what is already stored — the notes, and the daily history and
    dividends as last cached by an analysis or the watchlist. No provider
    call. `as_of` is the last daily bar the note was judged against. With no
    cached history a note cannot be held against any ≥2σ session, so it is
    not called OK: its status is None with NO_HISTORY as the reason (verdicts
    that need no bars — SUPERSEDED, incomplete research — still stand)."""
    out: dict[int, dict[str, Any]] = {}
    conn = connect()
    try:
        notes = {t: _note_rows(conn, t) for t in sorted({str(t).upper() for t in tickers if t})}
    finally:
        conn.close()
    for sym, rows in notes.items():
        if not rows:
            continue
        try:
            cached = _get_row(f"history:{sym}")
        except Exception:  # noqa: BLE001 — judged without bars, and said so
            cached = None
        history = cached[0] if cached and isinstance(cached[0], list) else []
        divs = cached_dividend_events(sym)
        basis, bars = price_basis.build(None, history, divs, now=now)
        sessions = research_quality.big_move_sessions(bars, divs)
        as_of = bars[-1]["date"] if bars else None
        for r, st in zip(rows, research_quality.prior_note_status(rows, None, basis, sessions)):
            status, reason = st["status"], st["reason"]
            if not bars and status == research_quality.OK:
                status, reason = None, NO_HISTORY
            out[r["id"]] = {"status": status, "reason": reason, "as_of": as_of}
    return out


def _stance_columns(conn, symbol: str, basis: dict, bars: list[dict], now,
                    held: set[str] | None) -> dict[str, Any]:
    """The Claude column: stance, its date, its label, whether it was written
    for an owned name, and the warnings the analysis header would show
    (research_quality.pick_stance / stance_flags, news_relevance.research_flags).

    ≥2σ sessions are found on dividend-adjusted closes, as the composer does,
    using the dividend history as last cached (cached_dividend_events): an
    ex-dividend drop is not a 2σ session here either. A ticker never analyzed
    has no cached dividends and is judged on raw closes."""
    rows = _note_rows(conn, symbol)
    st, orphan = research_quality.pick_stance(rows)
    if not st:
        return {**_NO_STANCE, "stance_warnings": orphan}
    if held is not None:
        st["account_owned"] = symbol in held or SHARE_CLASSES.get(symbol) in held
    big = news_relevance.big_sessions(
        None, basis, research_quality.big_move_sessions(bars, cached_dividend_events(symbol)))
    explained = research_quality.explained_sessions(rows, None, basis, big)
    today = market_clock.session_state(now)["today"]
    warnings = (news_relevance.research_flags(None, st, None, basis, sessions=big,
                                              explained=explained)["stance"]
                + research_quality.stance_flags(st, bars, today))
    return {"stance": st["stance"], "stance_ts": st["created_ts"],
            "stance_label": research_quality.stance_label(st),
            "stance_owned": research_quality.stance_owned(st),
            "stance_warnings": warnings}


def screen_symbol(symbol: str, providers: dict, force: bool = False,
                  now=None, held: set[str] | None = None) -> dict[str, Any]:
    """One watchlist row. Never raises — a symbol that fails everything still
    returns a row (the UI shows dashes, not a hole in the table). `held` is
    the account's symbols when known (held_symbols)."""
    symbol = symbol.upper()
    quote = _cached("quote", symbol, providers["quote"], "quote", force)
    history = _cached("history", symbol, providers["history"], "history", force) or []
    options = _cached("options", symbol, providers["options"], "options", force)
    earnings = _cached("earnings", symbol, providers["earnings"], "earnings", force)

    # one price basis, same as the report (analysis/price_basis.py)
    basis, bars = price_basis.build(quote, history, now=now)
    row: dict[str, Any] = {
        "symbol": symbol,
        "price": basis.get("price") if basis.get("price") is not None
                 else ((quote or {}).get("regular_last") or (quote or {}).get("last")),
        "day_pct": basis.get("day_move_pct"),
        "day_label": basis.get("move_word"),
        "band_half_pct": None,
        "implied_move_pct": None,
        "implied_vs_band": None,      # >1 ⇒ options pricing more than recent vol
        "days_to_earnings": (earnings or {}).get("days_until"),
        "yesterday_z": None,
        "outside_band_yesterday": False,   # P4: yesterday broke the 80% band
        "last_score": None,
        "last_score_ts": None,
    }

    # bars end with the last COMPLETED session: when open that's yesterday;
    # otherwise it's the session the day move refers to
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
        try:
            row.update(_stance_columns(conn, symbol, basis, bars, now, held))
        except Exception:  # noqa: BLE001 — the column degrades, the row stays
            row.update(_NO_STANCE)
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

    held = held_symbols()         # one cache read for the whole list

    def one(symbol: str) -> dict[str, Any]:
        try:
            row = screen_symbol(symbol, providers, force, held=held)
        except Exception:  # absolute backstop — a hole in the table is worse
            row = {"symbol": symbol, "price": None, "day_pct": None,
                   "band_half_pct": None, "implied_move_pct": None,
                   "implied_vs_band": None, "days_to_earnings": None,
                   "yesterday_z": None, "outside_band_yesterday": False,
                   "last_score": None, "last_score_ts": None, **_NO_STANCE}
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
