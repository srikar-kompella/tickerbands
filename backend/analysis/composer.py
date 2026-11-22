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
import math
import json
import os
from typing import Any, Callable  # noqa: F401

import config
from analysis import (earnings_moves, fundamentals_ttm, market_clock, news_lex,
                      news_relevance, options_iv, price_basis, report_view,
                      research_quality,
                      setup_score, statements, value_lens, vol_bands)
from cache.store import _get_row, get_or_fetch
from db import connect
from providers import registry
from providers import mock
from providers.base import ProviderError


def use_mock() -> bool:
    """Delegates to the registry (TICKERBANDS_MOCK=1 → mock everywhere)."""
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
    wrapped = {**wrapped, "data": _sanitize_day_pl(wrapped["data"], force)}
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


def _sanitize_day_pl(acct: dict, force: bool, now: dt.datetime | None = None) -> dict:
    """Schwab's currentDayProfitLoss is sometimes nonsense — figures larger
    than the whole position were observed on several tickers (Oct–Nov 2026).
    Recompute each position's day P/L as qty × the last session's per-share
    change (from the batch quote) and use the broker figure only when it
    agrees. Totals are rebuilt from the corrected rows."""
    if not acct or not acct.get("positions"):
        return acct
    fn = registry.resolve("quotes_batch")
    quotes = {}
    if fn is not None:
        syms = sorted({p["symbol"] for p in acct["positions"]})
        try:
            quotes = get_or_fetch("quotes_batch:ACCOUNT:" + ",".join(syms),
                                  config.TTL["quote"], lambda: fn(syms), force=force)["data"] or {}
        except (ProviderError, TypeError):
            quotes = {}
    # per-share change from the SAME price basis the report uses (official
    # closes from daily bars) — Schwab's regularMarketPercentChange for ANT
    # differed from its bars (−9.06% vs −8.50%), so the Portfolio tab and the
    # report disagreed on the same position (11/21)
    hist_fn = registry.resolve("daily_history")

    def per_share_for(sym: str) -> float | None:
        q = quotes.get(sym) or {}
        if hist_fn is not None:
            try:
                h = get_or_fetch(f"history:{sym}", config.TTL["history"],
                                 lambda: hist_fn(sym), force=False)["data"]
                b, _ = price_basis.build(q or None, h, now=now)
                if b.get("day_move_abs") is not None:
                    return b["day_move_abs"]
            except (ProviderError, TypeError, ValueError, KeyError):
                pass
        rl, pct = q.get("regular_last"), q.get("regular_change_pct")
        if rl and pct is not None and pct > -100:
            return rl - rl / (1 + pct / 100)
        return None

    from concurrent.futures import ThreadPoolExecutor
    syms_all = [p["symbol"] for p in acct["positions"]]
    with ThreadPoolExecutor(max_workers=min(6, len(syms_all) or 1)) as pool:
        changes = dict(zip(syms_all, pool.map(per_share_for, syms_all)))
    acct = {**acct, "positions": [dict(p) for p in acct["positions"]]}
    for p in acct["positions"]:
        q = quotes.get(p["symbol"]) or {}
        pct = q.get("regular_change_pct")
        per_share = changes.get(p["symbol"])
        broker = p.get("day_pl")
        p["day_pl_broker"] = broker
        if per_share is None:
            bad = broker is not None and p.get("market_value") and abs(broker) > abs(p["market_value"])
            if bad:
                p["day_pl"], p["day_pl_source"] = None, "withheld (broker figure exceeds position)"
            continue
        calc = round(p["qty"] * per_share, 2)
        agrees = (broker is not None and abs(broker - calc) <= max(1.0, 0.05 * abs(calc)))
        if not agrees:
            p["day_pl"] = calc
            mv = p.get("market_value") or 0
            p["day_pl_pct"] = (round(calc / (mv - calc) * 100, 2) if mv and mv != calc
                               else (round(pct, 2) if pct is not None else None))
            p["day_pl_source"] = "computed: qty × last session's change (broker figure disagreed)"
        else:
            p["day_pl_source"] = "broker (agrees with qty × change)"
    vals = [p["day_pl"] for p in acct["positions"] if p.get("day_pl") is not None]
    day = round(sum(vals), 2)
    prev_total = (acct.get("total_value") or 0) - day
    acct["day_pl_broker"] = acct.get("day_pl")
    acct["day_pl"] = day
    acct["day_pl_pct"] = round(day / prev_total * 100, 2) if prev_total else 0.0
    return acct


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
    if not hit:
        # GOOG report, GOOGL position: same company, other share class
        sib = report_view.SHARE_CLASSES.get(symbol.upper())
        link = next((p for p in acct["positions"] if p["symbol"] == sib), None) if sib else None
        if link:
            data["linked"] = dict(link)
    data["account_total"] = acct.get("total_value")
    return {
        "status": "stale" if wrapped["source"] == "stale" else "ok",
        "data": data,
        "fetched_ts": wrapped["fetched_ts"],
        "age_seconds": wrapped["age_seconds"],
        **({"error_reason": wrapped["error_reason"]}
           if wrapped["source"] == "stale" else {}),
    }


def _dividends(symbol: str, force: bool) -> dict | None:
    """Distribution history (ex-dates + amounts) via the cache. Powers the TTM
    yield AND the dividend-adjusted move/σ/band (BUG A + B). None = degrade."""
    fn = mock.fetch_dividends if use_mock() else registry.resolve("dividend_history")
    if fn is None:
        return None
    try:
        return get_or_fetch(f"dividends:{symbol.upper()}", config.TTL["dividends"],
                            lambda: fn(symbol), force=force)["data"]
    except ProviderError:
        return None


def _statements(symbol: str, force: bool) -> dict | None:
    """Financial-statement aggregates for the value lens (24h cache)."""
    return _statements_checked(symbol, force, None)[0]


def _fetched_date(ts: float | None) -> str | None:
    return dt.date.fromtimestamp(ts).isoformat() if ts else None


def _one_statement_source(key: str, fn, symbol: str, force: bool,
                          last_earnings: str | None) -> tuple[dict | None, dict]:
    """One statement source through the cache, refetched when an earnings
    release postdates the cached copy (or the copy predates the per-statement
    format). GOOGL 10/29 showed pre-Q3 figures the morning after Q3; NVDA
    11/21 showed TTM FCF through Q2 after the 11/17 release."""
    meta: dict[str, Any] = {"refreshed": False}
    w = get_or_fetch(key, config.TTL["statements"], lambda: fn(symbol), force=force)
    cov = report_view.statement_coverage(w["data"], last_earnings)
    fetched = _fetched_date(w.get("fetched_ts"))
    needs = w["source"] != "live" and (
        "periods" not in (w["data"] or {})
        or (cov["covers_latest"] is False
            and ((last_earnings and fetched and fetched <= last_earnings[:10])
                 or (w.get("age_seconds") or 0) > 6 * 3600)))
    if needs:
        w = get_or_fetch(key, config.TTL["statements"], lambda: fn(symbol), force=True)
        meta["refreshed"] = w["source"] == "live"
    meta.update({"fetched_date": _fetched_date(w.get("fetched_ts")),
                 "source_kind": w["source"]})
    return w["data"], meta


def _statements_checked(symbol: str, force: bool,
                        last_earnings: str | None,
                        release: dict | None = None) -> tuple[dict | None, dict]:
    """Statements merged per statement from the primary source (SEC filings
    by default) and the fallback (Yahoo), freshest COMPLETE period wins —
    then coverage-checked against the last earnings release."""
    if use_mock():
        fns = [("statements", mock.fetch_statements)]
    else:
        fns = []
        prim = registry.resolve("financial_statements")
        fb = registry.resolve("statements_fallback")
        if prim:
            fns.append(("statements", prim))
        if fb and fb is not prim:
            fns.append(("statements_fb", fb))
    meta: dict[str, Any] = {"last_earnings": last_earnings, "refreshed": False,
                            "sources": []}
    datas = []
    for prefix, fn in fns:
        try:
            d, m = _one_statement_source(f"{prefix}:{symbol.upper()}", fn, symbol,
                                         force, last_earnings)
        except ProviderError as e:
            meta["sources"].append({"key": prefix, "error": f"{e.reason}: {e.detail}"[:160]})
            continue
        datas.append(d)
        meta["refreshed"] = meta["refreshed"] or m["refreshed"]
        meta["sources"].append({"key": prefix, "source": (d or {}).get("source"),
                                "periods": (d or {}).get("periods"),
                                "fetched_date": m["fetched_date"]})
        meta.setdefault("fetched_date", m["fetched_date"])
    merged = statements.merge(datas) if datas else None
    cov = report_view.statement_coverage(merged, last_earnings, release)
    meta.update({"covers_latest": cov["covers_latest"], "warning": cov["warning"],
                 "lag": cov["lag"]})
    return merged, meta


def _last_earnings_date(symbol: str, now_ny: dt.datetime, state: str) -> str | None:
    """Most recent earnings release on or before now (AMC releases dated
    today don't count until the session has closed)."""
    fn = registry.resolve("earnings_dates")
    if fn is None:
        return None
    try:
        events = get_or_fetch(f"earnings_dates:{symbol}", config.TTL["earnings_dates"],
                              lambda: fn(symbol))["data"] or []
    except (ProviderError, TypeError):
        return None
    today = now_ny.date().isoformat()
    past = []
    for e in events:
        d = str(e.get("date") or "")[:10]
        if not d or d > today:
            continue
        if d == today and (e.get("hour") or "").lower() == "amc" and state in ("pre", "open"):
            continue
        past.append(d)
    return max(past) if past else None


def _earnings_confirm(symbol: str, force: bool) -> dict | None:
    fn = registry.resolve("earnings_confirm")
    if fn is None:
        return None
    try:
        return get_or_fetch(f"earnings_confirm:{symbol}", config.TTL["earnings"],
                            lambda: fn(symbol), force=force)["data"]
    except ProviderError:
        return None


def _shares_out(symbol: str) -> tuple[float | None, str | None]:
    fn = registry.resolve("shares_out")
    if fn is None:
        return None, None
    try:
        v = get_or_fetch(f"shares_out:{symbol}", config.TTL["shares_out"],
                         lambda: fn(symbol))["data"]
        return (float(v), f"{registry.provider_name('shares_out')} profile") if v else (None, None)
    except (ProviderError, TypeError, ValueError):
        return None, None


def _recent_filings(symbol: str, since: str, force: bool) -> dict | None:
    fn = registry.resolve("sec_filings")
    if fn is None:
        return None
    try:
        return get_or_fetch(f"sec_filings:{symbol}:{since[:10]}", 6 * 3600,
                            lambda: fn(symbol, since), force=force)["data"]
    except (ProviderError, TypeError):
        return None


def _rating_changes(symbol: str, force: bool) -> dict | None:
    fn = registry.resolve("rating_changes")
    if fn is None:
        return None
    try:
        return get_or_fetch(f"rating_changes:{symbol}", config.TTL["analyst"],
                            lambda: fn(symbol), force=force)["data"]
    except (ProviderError, TypeError):
        return None


def _last_report_date(symbol: str) -> str | None:
    row = _get_row(f"report_last:{symbol.upper()}")
    return (row[0] or {}).get("date") if row else None


def _eps_check(symbol: str, force: bool, last_earnings: str | None = None) -> dict | None:
    """Statement-derived TTM EPS cross-check for the fundamentals panel —
    refetched when an earnings release postdates the cached copy."""
    fn = mock.fetch_eps_ttm if use_mock() else registry.resolve("eps_check")
    if fn is None:
        return None
    key = f"eps_check:{symbol.upper()}"
    try:
        w = get_or_fetch(key, config.TTL["eps_check"], lambda: fn(symbol), force=force)
        fetched = _fetched_date(w.get("fetched_ts"))
        if (w["source"] == "cache" and last_earnings and fetched
                and fetched <= last_earnings[:10]):
            w = get_or_fetch(key, config.TTL["eps_check"], lambda: fn(symbol), force=True)
        return w["data"]
    except ProviderError:
        return None


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


# A stale fallback is useful for an hour or two, not for weeks: the live NVDA
# page (11/21) showed a 53-day-old news sentiment ("43% bullish") and FINRA
# short interest from a feed that had been failing since early October.
STALE_MAX_SECONDS = 72 * 3600


def _drop_ancient(section: str, env: dict) -> dict:
    if env.get("status") != "stale":
        return env
    ttl = config.TTL.get(_SECTION_META.get(section, (section,))[0], 3600)
    limit = max(STALE_MAX_SECONDS, 3 * ttl)
    if (env.get("age_seconds") or 0) <= limit:
        return env
    days = round((env.get("age_seconds") or 0) / 86400, 1)
    return {"status": "unavailable", "error_reason": env.get("error_reason") or "stale",
            "error_detail": (f"provider failing; last good data is {days} days old — "
                             "withheld rather than shown as current")}


def _fetch_sections(symbol: str, force: bool,
                    force_sections: set[str] | None = None) -> dict[str, dict]:
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
            futures = [pool.submit(_fetch_one, sec, fn, ttl, symbol,
                                   force or sec in (force_sections or set()))
                       for sec, fn, ttl in jobs]
            for fut in futures:
                section, envelope = fut.result()
                out[section] = _drop_ancient(section, envelope)
    return out


def _spy_bars(force: bool) -> list[dict] | None:
    """SPY completed daily bars — cached like any other section."""
    fns = _providers()
    try:
        wrapped = get_or_fetch("history:SPY", config.TTL["history"],
                               lambda: fns["history"]("SPY"), force=force)
        return vol_bands.completed_bars(wrapped["data"])
    except ProviderError:
        return None  # beta simply won't render; not worth failing anything


def _spy_closes(force: bool) -> list[float] | None:
    """SPY history for beta/correlation."""
    bars = _spy_bars(force)
    return [b["close"] for b in bars] if bars else None


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
            if k not in {"generated_ts", "sections", "score_history",
                         # derived presentation layer + clock-dependent bits
                         "report_view", "news_window", "research_flags",
                         "big_move_sessions", "statements_meta",
                         "recent_sessions"}}
    if isinstance(slim.get("price_basis"), dict):
        slim["price_basis"] = {k: v for k, v in slim["price_basis"].items()
                               if k != "generated_ny"}
    canon = json.dumps(slim, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canon.encode()).hexdigest()[:16]


def analyze(symbol: str, force: bool = False, now: dt.datetime | None = None,
            fresh_core: bool = False) -> dict[str, Any]:
    """The GET /api/analysis/{symbol} payload. Never raises for a single
    provider failure; raises ProviderError('not_found') only when the symbol
    has neither quote nor history (nothing to build a page from).

    `now` is injectable (replay tests). `fresh_core=True` (the report) forces
    the quote, history and account past their caches — a report must never
    quote a cached price as current."""
    symbol = symbol.strip().lstrip("$").upper().strip()   # "$nvda" → "NVDA"
    clk = market_clock.session_state(now)
    now_ny = clk["now"]
    sections = _fetch_sections(symbol, force,
                               {"quote", "history"} if fresh_core else None)
    sections["position"] = _position_section(symbol, force or fresh_core)

    quote = sections["quote"].get("data") if sections["quote"].get("data") else None
    history = sections["history"].get("data") or []
    if quote is None and not history:
        reasons = "; ".join(
            f"{sec}: {sections[sec].get('error_reason', '?')}"
            f" ({sections[sec].get('error_detail', '')[:120]})"
            for sec in ("quote", "history")
            if sections[sec].get("status") in ("unavailable", "disabled"))
        raise ProviderError("not_found",
                            f"no price data for '{symbol}' — {reasons or 'unknown'}")

    # ── headlines: keep only items about this company (NVDA 11/21 listed
    # Intel / Cramer / annuities / Anthropic) — before sentiment or display ─
    headlines_view = news_relevance.filter_headlines(
        sections["headlines"].get("data") or [], symbol, (quote or {}).get("name"))

    # ── news sentiment fallback chain (ask #3) ────────────────────────────────
    news_data = sections["news_sentiment"].get("data")
    if news_data and news_data.get("available"):
        news_data.setdefault("method", "finnhub")
    else:
        lex = news_lex.score_headlines(headlines_view["all_relevant"])
        if lex.get("available"):
            news_data = lex          # scored on RELEVANT headlines only
            sections["news_sentiment"] = {
                "status": "ok", "derived_from": "headlines",
                "fetched_ts": sections["headlines"].get("fetched_ts"),
                "age_seconds": sections["headlines"].get("age_seconds"),
            }

    # ── ONE price basis for the whole report (price_basis.py) ────────────────
    div_payload = _dividends(symbol, force)
    div_events = (div_payload or {}).get("events") or []
    qenv = sections["quote"]
    basis, bars = price_basis.build(
        quote, history, div_events, (div_payload or {}).get("next_ex_date"), now,
        quote_status="ok" if qenv.get("status") == "ok" else "stale",
        quote_age_s=qenv.get("age_seconds"))
    price = basis["price"]
    closes = [b["close"] for b in bars]
    # implied vol read over a closed market drifts up as expiry nears on
    # frozen marks — restate it to the close those marks are from
    if sections["options"].get("data"):
        sections["options"] = {**sections["options"], "data": options_iv.restate_to_close(
            sections["options"]["data"], basis)}
    volumes = [b.get("volume") or 0 for b in bars]
    ex_amt = basis.get("ex_div_amount") or 0.0

    signals = band = move = None
    if len(closes) >= 21:
        signals = vol_bands.compute_signals(closes, volumes)  # raw price levels
        adj_closes = vol_bands.adjust_closes_for_dividends(bars, div_events)
        if div_events:
            signals["rv_20d"] = round(vol_bands.realized_vol(adj_closes, 20), 2)
            if len(adj_closes) >= 61:
                signals["rv_60d"] = round(vol_bands.realized_vol(adj_closes, 60), 2)
        engine = _load_band_engine()
        band_closes = adj_closes
        if engine == "conformal":
            bars2y = _closes_2y(symbol, force)
            if bars2y:
                band_closes = bars2y
        live = basis["price_kind"] == "live"
        # band: when open, today's session (anchored on the prior close, less
        # any distribution going ex today); otherwise the NEXT session,
        # anchored on the official close just quoted
        anchor = round(closes[-1] - (ex_amt if live else 0.0), 4)
        band = vol_bands.band_with_engine(anchor, band_closes, engine)
        band.update({"anchor_date": bars[-1]["date"],
                     "session": "today" if live else "next", "warnings": []})
        # σ for the move = the σ of the band that applied to that session
        sig_closes = adj_closes if live else adj_closes[:-1]
        if price is not None and len(sig_closes) >= 21 and basis["prior_close"]:
            rv_move = vol_bands.realized_vol(sig_closes, 20)
            move = vol_bands.move_z_score(basis["prior_close"] - ex_amt, price, rv_move)
            move.update({"session_date": basis["session_date"],
                         "label": basis["move_word"],
                         "daily_sigma_pct": round(rv_move / math.sqrt(config.TRADING_DAYS), 4),
                         "basis": ("live vs prior official close" if live
                                   else "official close vs prior official close")})
        ah = basis.get("after_hours")
        if ah and band.get("half_width_pct") and abs(ah["change_pct"]) > band["half_width_pct"]:
            band["warnings"].append(
                f"{ah['kind']} price {ah['change_pct']:+.2f}% from the official close is "
                f"already beyond this band (±{band['half_width_pct']:.2f}%) — likely an "
                "earnings/news gap; the band is centered on the official close")

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
    if (opt.get("available") and opt.get("implied_move_pct") is not None and quote
            and basis["price_kind"] == "live"):
        conn = connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO implied_move_history "
                "(symbol, day, spot, dte, implied_pct) VALUES (?, ?, ?, ?, ?)",
                (symbol, dt.date.today().isoformat(), price or quote["last"],
                 opt.get("implied_move_dte") or 0, opt["implied_move_pct"]))
            conn.commit()
        finally:
            conn.close()

    # ── short interest as % of shares outstanding (labeled as such) ──────────
    shares_m, shares_src = _shares_out(symbol) if registry.resolve("shares_out") else (None, None)
    si_data = sections["short_interest"].get("data")
    if si_data and si_data.get("available") and si_data.get("short_interest") and shares_m:
        si_data["pct_of_shares_out"] = round(
            si_data["short_interest"] / (shares_m * 1e6) * 100, 2)

    beta = None
    spy_bars = None
    if closes and symbol != "SPY":
        spy_bars = _spy_bars(force)
        if spy_bars:
            beta = vol_bands.beta_and_correlation(closes, [b["close"] for b in spy_bars])

    # the last 10 completed sessions with SPY's move beside each, so a
    # research session need not look up closes or the market's day
    recent = []
    if len(bars) >= 22:
        recent = vol_bands.recent_sessions(
            bars, vol_bands.adjust_closes_for_dividends(bars, div_events),
            spy_bars, (beta or {}).get("beta"))

    week52 = None
    if quote and quote.get("week52_high") and quote.get("week52_low") and price:
        lo, hi = quote["week52_low"], quote["week52_high"]
        if hi > lo:
            week52 = {"low": lo, "high": hi,
                      "position_pct": round((price - lo) / (hi - lo) * 100, 1)}

    # ── Setup Score (Q8) — empty feeds contribute nothing; thin samples are
    # withheld (setup_score.py) ───────────────────────────────────────────────
    score = None
    if config.FEATURES.get("setup_score", True) and signals:
        score = setup_score.compute_from_sections(
            signals=signals,
            news=news_data,
            options=sections["options"].get("data"),
            recs=sections["recommendations"].get("data"),
            social=sections["social"].get("data"),
            weights=_load_weights(),
            today=now_ny.date(),
        )
        _persist_score(symbol, score)

    # ── bottom context: descriptive proximity facts (his #5, honest version) ─
    bottom_context = None
    bars2y_bc = _bars_2y(symbol, False)
    if bars2y_bc and len(bars2y_bc) >= 220 and price:
        c2 = [b["close"] for b in bars2y_bc][-252:]
        last = price
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

    # ── statement + vendor freshness around the last earnings release ────────
    is_equity = (quote or {}).get("asset_type", "EQUITY") == "EQUITY"
    last_earn = _last_earnings_date(symbol, now_ny, basis["state"]) if is_equity else None
    nwin = news_relevance.news_window(_last_report_date(symbol), basis.get("session_date"),
                                      now_ny.date())
    # SEC filings since the earlier of the news window and the last earnings
    # release: the regulatory pass, the cause of a big move, and the earnings
    # 8-K that carries results before the 10-Q exists
    since = min([d for d in (nwin["from"], (dt.date.fromisoformat(last_earn)
                 - dt.timedelta(days=2)).isoformat() if last_earn else None) if d])
    filings_payload = _recent_filings(symbol, since, force) if is_equity else None
    filings = []
    for f in (filings_payload or {}).get("filings") or []:
        desc, red = news_relevance.describe_8k(f.get("items", "")) if f["form"].startswith("8-K") else (f["form"], False)
        filings.append({**f, "desc": desc, "red_flag": red})
    release = next((f for f in filings if f["form"].startswith("8-K") and "2.02" in f.get("items", "")
                    and last_earn and f["date"] >= (dt.date.fromisoformat(last_earn)
                                                    - dt.timedelta(days=1)).isoformat()), None)
    stmts, smeta = _statements_checked(symbol, force, last_earn, release)
    smeta["release"] = release
    eps_chk = _eps_check(symbol, force, last_earn)
    fenv = sections["fundamentals"]
    f_fetched = _fetched_date(fenv.get("fetched_ts"))
    if (last_earn and f_fetched and f_fetched <= last_earn
            and now_ny.date().isoformat() > last_earn and fenv.get("status") == "ok"):
        fn = _providers()["fundamentals"]
        if fn:
            sections["fundamentals"] = _fetch_one("fundamentals", fn, "fundamentals",
                                                  symbol, True)[1]
            fenv = sections["fundamentals"]
            f_fetched = _fetched_date(fenv.get("fetched_ts")) or f_fetched
    vendor = fenv.get("data")
    vendor_meta = {"fetched_date": f_fetched,
                   "predates_earnings": bool(last_earn and f_fetched and f_fetched <= last_earn)}
    earn_confirm = (_earnings_confirm(symbol, force)
                    if is_equity and (sections["earnings"].get("data") or {}).get("available")
                    else None)

    pos_data = sections["position"].get("data") or {}
    notes = _discussion_rows(symbol)
    news_row, news_pick_flags = research_quality.pick_news(notes)
    stance_row, orphan_stance_flags = research_quality.pick_stance(notes)
    claude_stance = _stance_with_drift(stance_row, bars, band)
    if claude_stance:
        claude_stance = dict(claude_stance)
        if "owned" in pos_data:
            # the account's view of ownership: decides AVOID vs SELL when the
            # note does not say, and is checked against a note that says "no"
            claude_stance["account_owned"] = bool(pos_data.get("owned")
                                                  or pos_data.get("linked"))
        claude_stance["label"] = research_quality.stance_label(claude_stance)
    # every completed ≥2σ session in the bars (plus the report's own last
    # session): notes are judged against all of them, so a failed news check
    # does not read as fine again after one ordinary day
    big_sessions = news_relevance.big_sessions(
        move, basis, research_quality.big_move_sessions(bars, div_events))
    explained = research_quality.explained_sessions(notes, move, basis, big_sessions)
    flags = news_relevance.research_flags(news_row, claude_stance, move, basis, now_ny,
                                          big_sessions, explained)
    flags["news"] = news_pick_flags + flags["news"]
    # v5 stance checks (incomplete research, no conditions, expired, own
    # trigger met…) join the same warnings the header badge and report read
    flags["stance"] = (orphan_stance_flags + flags["stance"]
                       + research_quality.stance_flags(claude_stance, bars, now_ny.date()))
    if claude_stance:
        # the stance's own news fields were only needed for the check above
        claude_stance = {k: v for k, v in claude_stance.items()
                         if k not in ("news_view", "news_note")}
    claude_news = _public_news(news_row)
    reg_hits = news_relevance.regulatory_scan(headlines_view["all_relevant"], nwin["from"])
    cause = news_relevance.move_cause(headlines_view["all_relevant"], filings,
                                      (move or {}).get("session_date"), (move or {}).get("z"))
    rating_changes = _rating_changes(symbol, force) if is_equity else None

    rv_view = report_view.build({
        "symbol": symbol, "basis": basis, "move": move, "band": band,
        "signals": signals, "quote": quote, "vendor": vendor,
        "vendor_meta": vendor_meta, "shares_out_m": shares_m,
        "shares_source": shares_src, "statements": stmts,
        "statements_meta": smeta, "eps_check": eps_chk,
        "dividends": div_payload, "options": sections["options"].get("data"),
        "news": news_data, "headlines_view": headlines_view,
        "social": sections["social"].get("data"),
        "recs": sections["recommendations"].get("data"),
        "earnings": sections["earnings"].get("data"),
        "earnings_confirm": earn_confirm, "beta": beta,
        "position": pos_data, "account": {"total_value": pos_data.get("account_total"),
                                          "as_of": pos_data.get("as_of")},
        "claude_news": claude_news, "claude_stance": claude_stance,
        "research_flags": flags, "news_window": nwin, "now": now_ny,
        "filings": filings, "regulatory_hits": reg_hits, "move_cause": cause,
        "rating_changes": rating_changes,
    })
    report_view.apply_checks(rv_view)
    rv_view["data_as_of"] = report_view.data_as_of(
        rv_view, basis, stmts, smeta, headlines_view, claude_news, nwin)

    mcap_dollars = (rv_view["fields"].get("market_cap") or {}).get("value")
    payload: dict[str, Any] = {
        "symbol": symbol,
        "generated_ts": dt.datetime.now().isoformat(timespec="seconds"),
        "quote": quote,
        "price_basis": basis,
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
        "claude_news": claude_news,
        "claude_stance": claude_stance,
        "research_flags": flags,
        "big_move_sessions": big_sessions,
        "recent_sessions": recent,
        "news_window": nwin,
        "headlines": headlines_view["items"],
        "headlines_view": {k: v for k, v in headlines_view.items()
                           if k not in ("items", "all_relevant")},
        "sec_filings": filings,
        "regulatory_hits": reg_hits,
        "move_cause": cause,
        "rating_changes": rating_changes,
        "earnings_move": earnings_move,
        "pead": pead,
        "insiders": sections["insiders"].get("data"),
        "short_interest": si_data,
        "fundamentals": vendor,
        "fundamentals_ttm": fundamentals_ttm.build_ttm_view(
            price, vendor, div_payload, eps_chk),
        "value_lens": _value_lens_with_freshness(mcap_dollars, stmts, smeta),
        "statements_meta": smeta,
        "ex_div": {"today": bool(basis.get("ex_div_amount")) or basis.get("ex_div_pending", False),
                   "amount": basis.get("ex_div_amount"),
                   "session_date": basis.get("session_date"),
                   # date known but amount unpublished → warn, never fabricate
                   "pending": basis.get("ex_div_pending", False)},
        "recommendations": sections["recommendations"].get("data"),
        "earnings": sections["earnings"].get("data"),
        "earnings_confirm": earn_confirm,
        "social": sections["social"].get("data"),
        "position": pos_data,
        "report_view": rv_view,
    }
    payload["context_hash"] = _context_hash(payload)

    _touch_watchlist(symbol)
    return payload


def _value_lens_with_freshness(mcap: float | None, stmts: dict | None,
                               smeta: dict) -> dict | None:
    vl = value_lens.build_value_lens(mcap, stmts)
    if vl and smeta.get("warning"):
        vl["warnings"] = [f"statements {smeta['warning']}"] + list(vl.get("warnings") or [])
    return vl


def _stance_with_drift(stance: dict | None, bars: list[dict],
                       band: dict | None) -> dict | None:
    """Materiality beats checksums: a hash-equality 'stale' flag fires on ANY
    daily drift, so it is permanently on and therefore meaningless. Instead we
    report how far price actually moved since the stance was recorded, and
    flag it only when the move exceeds the band's own vol-scaled expectation
    (halfwidth × √t trading days) — the same math the band is built on."""
    if not stance or not bars:
        return stance
    try:
        then_date = str(stance.get("created_ts") or "")[:10]
        prior = [b for b in bars if b["date"] <= then_date]
        if not prior or not then_date:
            return stance
        then_close, now_close = prior[-1]["close"], bars[-1]["close"]
        if not then_close:
            return stance
        drift = (now_close / then_close - 1) * 100
        cal_days = (dt.date.fromisoformat(bars[-1]["date"])
                    - dt.date.fromisoformat(then_date)).days
        tdays = max(1, round(cal_days * 5 / 7))
        halfwidth_pct = (band or {}).get("half_width_pct")
        stance = dict(stance)
        stance["drift_pct"] = round(drift, 1)
        stance["drift_material"] = (
            abs(drift) > halfwidth_pct * math.sqrt(tdays)
            if halfwidth_pct else abs(drift) > 5.0)
    except Exception:
        return stance
    return stance


def _discussion_rows(symbol: str, limit: int = 50) -> list[dict]:
    """One ticker's stored discussions, newest first, `research_meta` parsed —
    the rows the stance, the news check and their flags are picked from
    (research_quality.pick_stance / pick_news).

    The app SURFACES the session's judgment (dated, reasoned, aging in plain
    sight) — it never computes a buy/sell/hold itself: the computed signals
    demonstrably can't call direction, and a reasoned multi-month judgment is
    a different animal. The news check is shown with its timestamp; the
    reader judges staleness, and the flags say what is wrong with it."""
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, stance, stance_horizon, stance_note, context_hash, "
            "created_ts, research_meta, news_view, news_note "
            "FROM claude_discussions WHERE ticker = ? "
            "ORDER BY created_ts DESC LIMIT ?", (symbol.upper(), limit))]
    finally:
        conn.close()
    for r in rows:
        try:
            r["research_meta"] = json.loads(r.get("research_meta") or "{}")
        except (TypeError, ValueError):
            r["research_meta"] = {}
        if not isinstance(r["research_meta"], dict):
            r["research_meta"] = {}
    return rows


def _public_news(row: dict | None) -> dict | None:
    """The news check as the payload carries it: the view, the note, its
    date, and `move_cause` (prompt v5) — the one research_meta key the UI
    reads. The rest of the row was only needed to judge the check."""
    if not row:
        return None
    out = {k: row.get(k) for k in ("news_view", "news_note", "created_ts")}
    cause = (row.get("research_meta") or {}).get("move_cause")
    if cause:
        out["move_cause"] = cause
    return out


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
