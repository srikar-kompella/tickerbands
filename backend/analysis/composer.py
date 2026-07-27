"""
analysis/composer.py — assembles one ticker's full analysis from providers.

The only module that knows both provider shapes and section semantics.
Responsibilities:
  * route each section through the TTL cache (Q6) with per-section
    status/freshness (ok | stale | unavailable — Q12's degradation contract)
  * compute derived analytics from COMPLETED bars only (F1 guard):
    signals, 80% band, today's-move z-score, beta/corr vs SPY, 52w position
  * compute the Setup Score with the user's weights, persist it to
    setup_score_history (Q8d organic accumulation)
  * stamp a context_hash (sha256 of the canonical payload) for discussion
    staleness detection

Adding an analysis section = add a provider call in _fetch_sections() and a
key in the returned dict. Nothing else changes (DESIGN_QUESTIONS §6).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from typing import Any, Callable  # noqa: F401

import config
from analysis import earnings_moves, news_lex, setup_score, vol_bands
from cache.store import get_or_fetch
from db import connect
from providers import registry
from providers.base import ProviderError


def use_mock() -> bool:
    """Delegates to the registry (TICKERLENS_MOCK=1 → mock everywhere)."""
    return registry.use_mock()


def _providers() -> dict[str, Callable | None]:
    """Section → fetch callable via the capability registry (None = no
    provider configured for that capability — section degrades)."""
    r = registry.resolve
    return {
        "quote": r("quotes"), "history": r("daily_history"),
        "options": r("option_chain"),
        "news_sentiment": r("news_sentiment"), "headlines": r("news_headlines"),
        "fundamentals": r("fundamentals"), "recommendations": r("analyst_recs"),
        "earnings": r("earnings"), "social": r("social"),
        "insiders": r("insiders"), "short_interest": r("short_interest"),
    }


# section → (cache TTL key, feature flag key or None=always on)
_SECTION_META = {
    "quote": ("quote", None),
    "history": ("history", None),
    "options": ("options", "options"),
    "news_sentiment": ("news_sentiment", "news"),
    "headlines": ("headlines", "news"),
    "fundamentals": ("fundamentals", "fundamentals"),
    "recommendations": ("analyst", "analyst"),
    "earnings": ("earnings", "earnings"),
    "social": ("social", "social"),
    "insiders": ("insiders", "insiders"),
    "short_interest": ("short_interest", "short_interest"),
}


# ─── live account portfolio (the Portfolio tab; also feeds the ownership chip) ─

def portfolio(force: bool = False) -> dict[str, Any]:
    """Account positions through the cache (one key for the whole account).
    A LIVE fetch appends to portfolio_snapshots — the organic value-history
    chart, same philosophy as the score history (no backfill fiction)."""
    fn = registry.resolve("account")
    if fn is None:
        raise ProviderError("unavailable", "no account provider configured (capabilities.account)")
    wrapped = get_or_fetch("portfolio:ACCOUNT", config.TTL["portfolio"], fn, force=force)
    if wrapped["source"] == "live":
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO portfolio_snapshots (ts, total_value, cash) VALUES (?, ?, ?)",
                (wrapped["data"]["as_of"], wrapped["data"]["total_value"],
                 wrapped["data"]["cash"]),
            )
            conn.commit()
        finally:
            conn.close()
    return wrapped


def portfolio_history(limit: int = 500) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT ts, total_value, cash FROM portfolio_snapshots "
            "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


def _position_section(symbol: str, force: bool) -> dict[str, dict]:
    """Ownership chip data, derived from the live account (the old Portfolio
    app's portfolio.db is retired as a source — one live truth now)."""
    if not config.FEATURES.get("ownership", True):
        return {"status": "disabled"}
    try:
        wrapped = portfolio(force)
    except ProviderError as e:
        return {"status": "unavailable", "error_reason": e.reason,
                "error_detail": e.detail}
    acct = wrapped["data"]
    hit = next((p for p in acct["positions"] if p["symbol"] == symbol.upper()), None)
    data = ({**hit, "owned": True, "as_of": acct["as_of"]} if hit
            else {"owned": False, "as_of": acct["as_of"]})
    return {
        "status": "stale" if wrapped["source"] == "stale" else "ok",
        "data": data,
        "fetched_ts": wrapped["fetched_ts"],
        "age_seconds": wrapped["age_seconds"],
        **({"error_reason": wrapped["error_reason"]}
           if wrapped["source"] == "stale" else {}),
    }


def _fetch_one(section: str, fn, ttl_key: str, symbol: str,
               force: bool) -> tuple[str, dict]:
    """One section's fetch+envelope — runs on a pool worker."""
    try:
        wrapped = get_or_fetch(
            key=f"{section}:{symbol.upper()}",
            ttl_seconds=config.TTL[ttl_key],
            fetch_fn=lambda: fn(symbol),
            force=force,
        )
        return section, {
            "status": "stale" if wrapped["source"] == "stale" else "ok",
            "data": wrapped["data"],
            "fetched_ts": wrapped["fetched_ts"],
            "age_seconds": wrapped["age_seconds"],
            **({"error_reason": wrapped["error_reason"],
                "error_detail": wrapped.get("error_detail", "")}
               if wrapped["source"] == "stale" else {}),
        }
    except ProviderError as e:
        return section, {"status": "unavailable",
                         "error_reason": e.reason, "error_detail": e.detail}


def _fetch_sections(symbol: str, force: bool) -> dict[str, dict]:
    """Every section independently AND CONCURRENTLY (step-1 speedup: cold-load
    time = the slowest single provider, not the sum of all of them). The
    cache's per-key in-flight lock prevents duplicate provider calls when
    requests overlap. Per-section degradation contract unchanged.
    """
    from concurrent.futures import ThreadPoolExecutor

    fns = _providers()
    out: dict[str, dict] = {}
    jobs = []
    for section, fn in fns.items():
        ttl_key, flag = _SECTION_META[section]
        if flag is not None and not config.FEATURES.get(flag, True):
            out[section] = {"status": "disabled"}
            continue
        if fn is None:  # capability not configured — honest gap
            out[section] = {"status": "unavailable",
                            "error_reason": "no_provider",
                            "error_detail": "no provider configured for this capability"}
            continue
        jobs.append((section, fn, ttl_key))

    if jobs:
        with ThreadPoolExecutor(max_workers=min(6, len(jobs))) as pool:
            futures = [pool.submit(_fetch_one, sec, fn, ttl, symbol, force)
                       for sec, fn, ttl in jobs]
            for fut in futures:
                section, envelope = fut.result()
                out[section] = envelope
    return out


def _spy_closes(force: bool) -> list[float] | None:
    """SPY history for beta/correlation — cached like any other section."""
    fns = _providers()
    try:
        wrapped = get_or_fetch("history:SPY", config.TTL["history"],
                               lambda: fns["history"]("SPY"), force=force)
        return [b["close"] for b in vol_bands.completed_bars(wrapped["data"])]
    except ProviderError:
        return None  # beta simply won't render; not worth failing anything


def _load_band_engine() -> str:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key='band_engine'").fetchone()
        engine = json.loads(row["value"]) if row else "flat20"
        return engine if engine in config.BAND_ENGINES else "flat20"
    finally:
        conn.close()


def _bars_2y(symbol: str, force: bool) -> list[dict] | None:
    """~520 completed daily bars (earnings-move history + conformal window)."""
    fns = _providers()
    try:
        wrapped = get_or_fetch(
            f"history2y:{symbol}", config.TTL["history2y"],
            lambda: fns["history"](symbol, 520), force=force)
        return vol_bands.completed_bars(wrapped["data"])
    except (ProviderError, TypeError):
        return None


def _closes_2y(symbol: str, force: bool) -> list[float] | None:
    bars = _bars_2y(symbol, force)
    return [b["close"] for b in bars] if bars else None


def _load_weights() -> dict[str, float]:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key='score_weights'").fetchone()
        if row:
            saved = json.loads(row["value"])
            # tolerate missing keys after upgrades: overlay onto defaults
            return {**config.DEFAULT_WEIGHTS, **{k: float(v) for k, v in saved.items()}}
    finally:
        conn.close()
    return dict(config.DEFAULT_WEIGHTS)


def _persist_score(symbol: str, result: dict) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO setup_score_history (symbol, ts, score, lean, components) "
            "VALUES (?, ?, ?, ?, ?)",
            (symbol.upper(), dt.datetime.now().isoformat(timespec="seconds"),
             result["score"], result["lean"], json.dumps(result["components"])),
        )
        conn.commit()
    finally:
        conn.close()


def _score_history(symbol: str, limit: int = 60) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT ts, score, lean FROM setup_score_history "
            "WHERE symbol = ? ORDER BY ts DESC LIMIT ?", (symbol.upper(), limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


def _band_coverage(symbol: str) -> dict | None:
    """Static S2 evidence. Core-20 tickers get their own number; everything
    else gets the overall figure explicitly labeled as basket-level."""
    try:
        with open(config.BAND_COVERAGE_JSON) as f:
            cov = json.load(f)
    except OSError:
        return None
    per = cov.get("per_ticker", {}).get(symbol.upper())
    if per:
        return {**per, "scope": "this_ticker", "overall": cov["overall"]}
    return {"scope": "basket_overall", **cov["overall"],
            "note": "ticker not in the backtest basket; overall figure shown"}


def _context_hash(payload: dict) -> str:
    """Stable fingerprint of the analysis a discussion refers to. Volatile
    bookkeeping excluded so the hash tracks *content*: generated_ts (clock),
    sections (fetch ages), score_history (grows on every analyze — including
    it would make two back-to-back identical analyses hash differently)."""
    slim = {k: v for k, v in payload.items()
            if k not in {"generated_ts", "sections", "score_history"}}
    canon = json.dumps(slim, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canon.encode()).hexdigest()[:16]


def analyze(symbol: str, force: bool = False) -> dict[str, Any]:
    """The GET /api/analysis/{symbol} payload. Never raises for a single
    provider failure; raises ProviderError('not_found') only when the symbol
    has neither quote nor history (nothing to build a page from)."""
    symbol = symbol.upper().strip()
    sections = _fetch_sections(symbol, force)
    sections["position"] = _position_section(symbol, force)

    quote = sections["quote"].get("data") if sections["quote"].get("data") else None
    history = sections["history"].get("data") or []
    if quote is None and not history:
        raise ProviderError("not_found", f"no data for '{symbol}'")

    # ── news sentiment fallback chain (ask #3) ────────────────────────────────
    # Finnhub's aggregate endpoint (paid on some tiers) → else score the free
    # headlines with the transparent lexicon. Method is always labeled.
    news_data = sections["news_sentiment"].get("data")
    if news_data and news_data.get("available"):
        news_data.setdefault("method", "finnhub")
    else:
        headlines_data = sections["headlines"].get("data") or []
        lex = news_lex.score_headlines(headlines_data)
        if lex.get("available"):
            news_data = lex
            sections["news_sentiment"] = {
                "status": "ok", "derived_from": "headlines",
                "fetched_ts": sections["headlines"].get("fetched_ts"),
                "age_seconds": sections["headlines"].get("age_seconds"),
            }

    # ── derived analytics from completed bars only (F1 guard) ────────────────
    bars = vol_bands.completed_bars(history)
    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]

    signals = band = move = None
    if len(closes) >= 21:
        signals = vol_bands.compute_signals(closes, volumes)
        # engine per user setting (flat20 default — it WON the validation);
        # conformal needs 251 bars → history section serves 130 → falls back
        # unless the 2y bars (fetched below for earnings-move) are available.
        engine = _load_band_engine()
        band_closes = closes
        if engine == "conformal":
            bars2y = _closes_2y(symbol, force)
            if bars2y:
                band_closes = bars2y
        band = vol_bands.band_with_engine(signals["prev_close"], band_closes, engine)
        current = quote["last"] if quote else closes[-1]
        move = vol_bands.move_z_score(signals["prev_close"], current, signals["rv_20d"])

    # ── earnings expected-move + PEAD (roadmap #3, #7) — stocks only ─────────
    earnings_move = pead = None
    if (config.FEATURES.get("earnings_move", True)
            and registry.resolve("earnings_dates") is not None
            and (sections["earnings"].get("data") or {}).get("available")
            and (quote or {}).get("asset_type") == "EQUITY"):
        try:
            events = get_or_fetch(
                f"earnings_dates:{symbol}", config.TTL["earnings_dates"],
                lambda: registry.resolve("earnings_dates")(symbol),
                force=force)["data"]
            bars2y_full = _bars_2y(symbol, force)
            if events and bars2y_full:
                hist = earnings_moves.earnings_day_moves(bars2y_full, events)
                implied = ((sections["options"].get("data") or {})
                           .get("implied_move_pct"))
                earnings_move = earnings_moves.expected_move_summary(implied, hist)
                pead = earnings_moves.pead_flag(
                    events, (sections["earnings"]["data"].get("surprises") or []),
                    bars2y_full)
        except ProviderError:
            pass  # context modules degrade silently; core page unaffected

    # ── VRP accrual: capture today's implied move once per symbol/day ────────
    opt = sections["options"].get("data") or {}
    if opt.get("available") and opt.get("implied_move_pct") is not None and quote:
        conn = connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO implied_move_history "
                "(symbol, day, spot, dte, implied_pct) VALUES (?, ?, ?, ?, ?)",
                (symbol, dt.date.today().isoformat(), quote["last"],
                 opt.get("implied_move_dte") or 0, opt["implied_move_pct"]))
            conn.commit()
        finally:
            conn.close()

    # ── short interest as % of shares outstanding (labeled as such) ──────────
    si_data = sections["short_interest"].get("data")
    if (si_data and si_data.get("available") and si_data.get("short_interest")
            and registry.resolve("shares_out") is not None):
        try:
            sho = get_or_fetch(
                f"shares_out:{symbol}", config.TTL["shares_out"],
                lambda: registry.resolve("shares_out")(symbol),
                force=False)["data"]
            if sho:
                si_data["pct_of_shares_out"] = round(
                    si_data["short_interest"] / (sho * 1e6) * 100, 2)
        except ProviderError:
            pass

    beta = None
    if closes and symbol != "SPY":
        spy = _spy_closes(force)
        if spy:
            beta = vol_bands.beta_and_correlation(closes, spy)

    week52 = None
    if quote and quote.get("week52_high") and quote.get("week52_low"):
        lo, hi = quote["week52_low"], quote["week52_high"]
        if hi > lo:
            week52 = {"low": lo, "high": hi,
                      "position_pct": round((quote["last"] - lo) / (hi - lo) * 100, 1)}

    # ── Setup Score (Q8) — news component consumes whichever sentiment source
    # survived the fallback chain (finnhub or headline-lexicon) ───────────────
    score = None
    if config.FEATURES.get("setup_score", True) and signals:
        score = setup_score.compute_from_sections(
            signals=signals,
            news=news_data,
            options=sections["options"].get("data"),
            recs=sections["recommendations"].get("data"),
            social=sections["social"].get("data"),
            weights=_load_weights(),
        )
        _persist_score(symbol, score)

    # ── bottom context: descriptive proximity facts (his #5, honest version) ─
    bottom_context = None
    bars2y_bc = _bars_2y(symbol, False)
    if bars2y_bc and len(bars2y_bc) >= 220 and quote:
        c2 = [b["close"] for b in bars2y_bc][-252:]
        last = quote["last"]
        hi52, lo52 = max(c2), min(c2)
        sma200_src = [b["close"] for b in bars2y_bc][-200:]
        sma200 = sum(sma200_src) / len(sma200_src)
        below = sum(1 for x in c2 if x < last) / len(c2)
        ins = sections["insiders"].get("data") or {}
        bottom_context = {
            "drawdown_pct": round((last / hi52 - 1) * 100, 1),
            "above_52w_low_pct": round((last / lo52 - 1) * 100, 1),
            "range_percentile": round(below * 100, 0),   # 0 = at the yearly low
            "vs_sma200_pct": round((last / sma200 - 1) * 100, 1),
            "bottom_decile": below <= 0.10,
            "insider_buys_into_drawdown": bool(
                ins.get("cluster_buy") and (last / hi52 - 1) < -0.15),
            "note": ("Where price sits — NOT whether this is the bottom "
                     "(nothing can know that; ~9,600 predictions proved it)."),
        }

    payload: dict[str, Any] = {
        "symbol": symbol,
        "generated_ts": dt.datetime.now().isoformat(timespec="seconds"),
        "quote": quote,
        "signals": signals,
        "band": band,
        "move": move,
        "beta": beta,
        "week52": week52,
        "band_coverage": _band_coverage(symbol),
        "bottom_context": bottom_context,
        "setup_score": score,
        "score_history": _score_history(symbol),
        "chart": [{"date": b["date"], "close": b["close"]} for b in bars[-30:]],
        "sections": {  # per-section status envelope for the UI's badges (Q12)
            name: {k: v for k, v in sec.items() if k != "data"}
            for name, sec in sections.items()
        },
        # section payloads the UI renders directly:
        "options": sections["options"].get("data"),
        "news_sentiment": news_data,
        "claude_news": _latest_claude_news(symbol),
        "claude_stance": _latest_claude_stance(symbol),
        "headlines": sections["headlines"].get("data"),
        "earnings_move": earnings_move,
        "pead": pead,
        "insiders": sections["insiders"].get("data"),
        "short_interest": si_data,
        "fundamentals": sections["fundamentals"].get("data"),
        "recommendations": sections["recommendations"].get("data"),
        "earnings": sections["earnings"].get("data"),
        "social": sections["social"].get("data"),
        "position": sections["position"].get("data"),
    }
    payload["context_hash"] = _context_hash(payload)

    _touch_watchlist(symbol)
    return payload


def _latest_claude_stance(symbol: str) -> dict | None:
    """Most recent research stance from a discussion session. The app SURFACES
    this judgment (dated, reasoned, aging in plain sight) — it never computes
    a buy/sell/hold itself: the computed signals demonstrably can't call
    direction, and a reasoned multi-month judgment is a different animal."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT id, stance, stance_horizon, stance_note, context_hash, "
            "created_ts FROM claude_discussions "
            "WHERE ticker = ? AND stance IS NOT NULL "
            "ORDER BY created_ts DESC LIMIT 1", (symbol.upper(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _latest_claude_news(symbol: str) -> dict | None:
    """Most recent Claude-supplied news view for this ticker (ask #3 fallback):
    discussions can carry `news_view` / `news_note` frontmatter after a live
    news check in the CLI session. Shown with its timestamp — the reader
    judges staleness; we don't silently expire someone's judgment."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT news_view, news_note, created_ts FROM claude_discussions "
            "WHERE ticker = ? AND news_view IS NOT NULL "
            "ORDER BY created_ts DESC LIMIT 1", (symbol.upper(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _touch_watchlist(symbol: str) -> None:
    """Record 'last analyzed' on the watchlist row, if this symbol is on it."""
    conn = connect()
    try:
        conn.execute(
            "UPDATE watchlist SET last_analyzed_ts = ? WHERE symbol = ?",
            (dt.datetime.now().isoformat(timespec="seconds"), symbol.upper()),
        )
        conn.commit()
    finally:
        conn.close()
