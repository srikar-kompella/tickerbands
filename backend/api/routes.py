"""
api/routes.py — all HTTP handlers, deliberately thin.

Route → one call into analysis/, discussions_svc/, or db. No business logic
here: if a handler grows an `if` about *finance*, it's in the wrong file.
"""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

import config
from analysis import composer
from api.research_prompt import build_claude_prompt  # prompt v5; tests import it from here too
from cache.store import invalidate
from db import connect
from discussions_svc.parser import list_discussions
from discussions_svc.stream import broadcaster
from providers import registry, schwab
from providers.base import ProviderError

router = APIRouter(prefix="/api")


# ─── analysis ──────────────────────────────────────────────────────────────────

@router.get("/analysis/{symbol}")
def get_analysis(symbol: str, refresh: bool = Query(False)):
    """The big one — every section for one ticker. `?refresh=true` = the
    universal refresh button (forces past TTLs; still stale-falls-back)."""
    try:
        return composer.analyze(symbol, force=refresh)
    except ProviderError as e:
        if e.reason == "not_found":
            raise HTTPException(404, e.detail or f"No data for '{symbol.upper()}'")
        if e.reason == "auth_expired":
            raise HTTPException(503, f"Schwab auth: {e.detail} (run schwab-reauth)")
        raise HTTPException(502, f"{e.reason}: {e.detail}")


@router.get("/analysis/{symbol}/report")
def get_report(symbol: str):
    """Print-designed research snapshot: full analysis + this ticker's Claude
    discussions + recorded decisions, as self-contained HTML that auto-opens
    the print dialog (⌘P → Save as PDF)."""
    from fastapi.responses import HTMLResponse
    from analysis.screener import decisions_review
    from api.report import build_report_html

    from cache.store import put as cache_put

    symbol = symbol.strip().lstrip("$")           # "$nvda", as tickers are written in chat
    try:
        # fresh_core: the quote, history and account are fetched past their
        # caches — a report never quotes a cached price as current
        analysis = composer.analyze(symbol, fresh_core=True)
    except ProviderError as e:
        if e.reason == "not_found":
            raise HTTPException(404, e.detail or f"No data for '{symbol.upper()}'")
        raise HTTPException(502, f"{e.reason}: {e.detail}")
    discussions = list_discussions(symbol, limit=25)
    price = (analysis.get("price_basis") or {}).get("price")
    decisions = []
    for d in decisions_review(_pv()["quote"]):
        if d["ticker"] != symbol.upper():
            continue
        if price and d.get("decision_price"):   # one price basis on the page
            d = {**d, "current_price": price,
                 "pct_since": round((price / d["decision_price"] - 1) * 100, 2)}
        decisions.append(d)
    # the next report's news window starts here at the latest
    cache_put(f"report_last:{symbol.upper()}",
              {"date": dt.date.today().isoformat(),
               "ts": dt.datetime.now().isoformat(timespec="seconds")})
    return HTMLResponse(build_report_html(analysis, discussions, decisions))


@router.get("/analysis/{symbol}/prompt")
def get_prompt(symbol: str):
    """The copy-into-Claude-CLI prompt (AskClaudeModal). Server-built so the
    context_hash matches the exact snapshot served (DESIGN_QUESTIONS: backend-
    computed hash) and Q15's position line stays a one-flag decision."""
    analysis = composer.analyze(symbol)
    return {"prompt": build_claude_prompt(analysis),
            "context_hash": analysis["context_hash"]}


def _pv() -> dict:
    """Provider fn map for screener/income/decisions via the registry."""
    r = registry.resolve
    return {"quote": r("quotes"), "history": r("daily_history"),
            "options": r("option_chain"), "earnings": r("earnings"),
            "fundamentals": r("fundamentals")}


@router.get("/watchlist/quotes")
def watchlist_quotes():
    """Fast pass for the screener: ONE batched Schwab call for every watched
    symbol → price + day change. Each quote is written into the shared cache,
    so the subsequent per-symbol enrichment (and any analysis page) reuses
    them instead of re-fetching."""
    from cache.store import put
    conn = connect()
    try:
        symbols = [r["symbol"] for r in conn.execute("SELECT symbol FROM watchlist")]
    finally:
        conn.close()
    if not symbols:
        return {}
    fn = registry.resolve("quotes_batch")
    if fn is None:
        return {}
    try:
        quotes = fn(symbols)
    except ProviderError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")
    for sym, q in quotes.items():
        put(f"quote:{sym}", q)   # warm the cache — one call feeds everything
    # regular-session price and change — net_change_pct is last-vs-close and
    # turns into the AFTER-HOURS move on evenings/weekends (SPY "+1.11%" on a
    # Friday that fell sharply, 11/21). AH stays a separate field.
    return {sym: {"price": q.get("regular_last") or q["last"],
                  "day_pct": (q["regular_change_pct"] if q.get("regular_change_pct") is not None
                              else q["net_change_pct"]),
                  "ah_price": q.get("ah_price"),
                  "ah_change_pct": q.get("ah_change_pct"),
                  "is_extended": bool(q.get("is_extended"))}
            for sym, q in quotes.items()}


@router.get("/watchlist/screener")
def watchlist_screener(refresh: bool = Query(False),
                       symbols: str | None = Query(None)):
    """P1+P4 enrichment: band width vs implied move, days to earnings,
    yesterday's σ (band-break flag), last persisted score.

    `?symbols=A,B,C` limits to a chunk — the UI streams enrichment in small
    batches with a progress bar instead of blocking on the whole list."""
    from analysis.screener import screen_watchlist
    subset = ([s.strip().upper() for s in symbols.split(",") if s.strip()]
              if symbols else None)
    return screen_watchlist(_pv(), force=refresh, only=subset)


@router.get("/portfolio/income")
def portfolio_income_view(refresh: bool = Query(False)):
    """P2: real dividend yields per holding → projected annual income."""
    from analysis.screener import portfolio_income
    try:
        account = composer.portfolio(force=False)["data"]
    except ProviderError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")
    return portfolio_income(account, _pv()["fundamentals"], force=refresh)


@router.get("/stances/scorecard")
def stances_scorecard():
    """Forward outcome audit of every recorded research stance vs SPY.
    The honest answer to "is the algorithm accurate": there is no algorithm —
    stances are dated judgments, and this tracks how each aged."""
    from analysis.scorecard import score_stances
    from cache.store import get_or_fetch
    from db import connect

    conn = connect()
    try:
        stances = [dict(r) for r in conn.execute(
            "SELECT ticker, stance, stance_horizon, stance_note, created_ts "
            "FROM claude_discussions WHERE stance IS NOT NULL "
            "ORDER BY created_ts")]
    finally:
        conn.close()
    if not stances:
        return {"rows": [], "summary": {}, "n_scored": 0,
                "caveat": "No stances recorded yet — run research sessions."}

    hist = _pv()["history"]
    bars_by_symbol: dict = {}
    for sym in sorted({s["ticker"].upper() for s in stances}):
        try:
            bars_by_symbol[sym] = get_or_fetch(
                f"history:{sym}", config.TTL["history"],
                lambda sym=sym: hist(sym))["data"]
        except ProviderError:
            bars_by_symbol[sym] = []
    try:
        spy = get_or_fetch(f"history:SPY", config.TTL["history"],
                           lambda: hist("SPY"))["data"]
    except ProviderError:
        spy = []
    return score_stances(stances, bars_by_symbol, spy)


@router.get("/decisions")
def decisions():
    """P3: every recorded decision + price then vs now."""
    from analysis.screener import decisions_review
    return decisions_review(_pv()["quote"])


# ─── P5: action plan (ported from the old Portfolio app) ───────────────────────

class ActionAdd(BaseModel):
    symbol: str | None = None
    action: str = Field(min_length=1, max_length=200)
    rationale: str = ""
    priority: int = 0


class ActionPatch(BaseModel):
    status: str | None = None      # open / done / dismissed
    rationale: str | None = None


@router.get("/actions")
def list_actions():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM actions ORDER BY (status != 'open'), priority DESC, id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/actions", status_code=201)
def add_action(body: ActionAdd):
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO actions (symbol, action, rationale, priority, created_ts) "
            "VALUES (?, ?, ?, ?, ?)",
            ((body.symbol or "").upper() or None, body.action, body.rationale,
             body.priority, dt.datetime.now().isoformat(timespec="seconds")))
        conn.commit()
        return {"ok": True, "id": cur.lastrowid}
    finally:
        conn.close()


@router.patch("/actions/{action_id}")
def patch_action(action_id: int, body: ActionPatch):
    if body.status is not None and body.status not in {"open", "done", "dismissed"}:
        raise HTTPException(422, "status must be open|done|dismissed")
    conn = connect()
    try:
        row = conn.execute("SELECT id FROM actions WHERE id = ?", (action_id,)).fetchone()
        if not row:
            raise HTTPException(404, "action not found")
        if body.status is not None:
            conn.execute("UPDATE actions SET status = ? WHERE id = ?", (body.status, action_id))
        if body.rationale is not None:
            conn.execute("UPDATE actions SET rationale = ? WHERE id = ?", (body.rationale, action_id))
        conn.commit()
        return dict(conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone())
    finally:
        conn.close()


@router.post("/actions/import-portfolio")
def import_actions():
    """One-time port of the old app's action plan (read-only source)."""
    import sqlite3 as _sq
    from config import PORTFOLIO_DB_PATH
    import os as _os
    if not PORTFOLIO_DB_PATH:
        raise HTTPException(501, "no legacy portfolio DB configured (paths.portfolio_db)")
    if not _os.path.exists(PORTFOLIO_DB_PATH):
        raise HTTPException(503, "old Portfolio app DB not found")
    try:
        src = _sq.connect(f"file:{PORTFOLIO_DB_PATH}?mode=ro", uri=True, timeout=2.0)
        src.row_factory = _sq.Row
        rows = src.execute("SELECT symbol, action, rationale, status, priority, "
                           "created_ts FROM actions").fetchall()
        src.close()
    except _sq.Error as e:
        raise HTTPException(503, f"old app DB unreadable: {e}")
    conn = connect()
    imported = 0
    try:
        for r in rows:
            dup = conn.execute(
                "SELECT 1 FROM actions WHERE symbol IS ? AND action = ? AND created_ts = ?",
                (r["symbol"], r["action"], r["created_ts"])).fetchone()
            if dup:
                continue
            conn.execute(
                "INSERT INTO actions (symbol, action, rationale, status, priority, created_ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (r["symbol"], r["action"], r["rationale"] or "", r["status"] or "open",
                 r["priority"] or 0, r["created_ts"] or dt.datetime.now().isoformat(timespec="seconds")))
            imported += 1
        conn.commit()
        return {"ok": True, "imported": imported, "seen": len(rows)}
    finally:
        conn.close()


# ─── watchlist imports (Schwab attempt + bulk paste) ───────────────────────────

class BulkAdd(BaseModel):
    symbols: str = Field(min_length=1)   # free text: "AAPL, NVDA TSLA\nHIMS"


def _add_watch_symbols(symbols: list[str], tag: str) -> int:
    conn = connect()
    added = 0
    try:
        for sym in symbols:
            sym = sym.upper().strip()
            if not sym or not all(c.isalnum() or c in ".-" for c in sym) or len(sym) > 10:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO watchlist (symbol, note, tags, added_ts) "
                "VALUES (?, '', ?, ?)",
                (sym, json.dumps([tag]), dt.datetime.now().isoformat(timespec="seconds")))
            added += cur.rowcount
        conn.commit()
        return added
    finally:
        conn.close()


@router.post("/watchlist/bulk")
def bulk_add(body: BulkAdd):
    import re
    symbols = re.split(r"[,\s;]+", body.symbols)
    added = _add_watch_symbols(symbols, "bulk-import")
    return {"ok": True, "added": added}


@router.post("/watchlist/import-schwab")
def import_schwab_watchlist():
    """One-time pull of Schwab watchlists — attempted honestly (the retail API
    may not expose them; the error says so and points at the fallbacks)."""
    from providers import mock as m
    fn = (m.fetch_schwab_watchlists if composer.use_mock()
          else (schwab.fetch_schwab_watchlists
                if registry.provider_name("account") == "schwab" else None))
    if fn is None:
        raise HTTPException(501, "Schwab is not the configured account provider")
    try:
        lists = fn()
    except ProviderError as e:
        raise HTTPException(502 if e.reason != "no_data" else 501, e.detail or e.reason)
    added = 0
    for wl in lists:
        added += _add_watch_symbols(wl["symbols"], f"schwab:{wl['name'][:20]}")
    return {"ok": True, "added": added,
            "lists": [{"name": w["name"], "count": len(w["symbols"])} for w in lists]}


@router.get("/discovery/report")
def discovery_report():
    """Printable Discover digest (same open-in-tab → auto-print flow as the
    analysis report)."""
    from analysis.discovery import list_candidates
    from api.report import build_discover_html
    return HTMLResponse(build_discover_html(list_candidates()))


@router.get("/discovery")
def discovery_list(include_dismissed: bool = Query(False)):
    from analysis.discovery import list_candidates
    return list_candidates(include_dismissed)


@router.post("/discovery/scan")
def discovery_scan():
    """Run all evidence screens on demand (each capped + defensive)."""
    from analysis import discovery as d
    m = registry.MODULES
    mockm = registry.use_mock()
    counts = {
        "pead": d.screen_pead(
            m["mock"].fetch_earnings_calendar_market if mockm else m["finnhub"].fetch_earnings_calendar_market,
            (m["mock"].fetch_fundamentals if mockm else m["finnhub"].fetch_fundamentals)),
        "insider": d.screen_insider_feed(
            m["mock"].fetch_form4_feed_counts if mockm else m["edgar"].fetch_form4_feed_counts),
        "short_interest": d.screen_short_interest(
            m["mock"].fetch_si_top_changes if mockm else m["finra"].fetch_si_top_changes),
    }
    return {"ok": True, "added": counts, "total_added": sum(counts.values())}


class DiscoveryPatch(BaseModel):
    status: str  # reviewed / dismissed / new


@router.patch("/discovery/{symbol}")
def discovery_patch(symbol: str, body: DiscoveryPatch):
    if body.status not in {"new", "reviewed", "dismissed"}:
        raise HTTPException(422, "status must be new|reviewed|dismissed")
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE discovery_candidates SET status=?, returned=0 WHERE symbol=?",
            (body.status, symbol.upper()))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "unknown candidate")
        return {"ok": True}
    finally:
        conn.close()


@router.post("/discovery/{symbol}/promote")
def discovery_promote(symbol: str):
    """Candidate → watchlist (tagged 'discovery'), status=promoted."""
    sym = symbol.upper()
    conn = connect()
    try:
        row = conn.execute("SELECT reasons FROM discovery_candidates WHERE symbol=?",
                           (sym,)).fetchone()
        if not row:
            raise HTTPException(404, "unknown candidate")
        reasons = json.loads(row["reasons"])
        note = (reasons[-1]["reason"][:120] if reasons else "")
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (symbol, note, tags, added_ts) VALUES (?,?,?,?)",
            (sym, note, json.dumps(["discovery"]),
             dt.datetime.now().isoformat(timespec="seconds")))
        conn.execute("UPDATE discovery_candidates SET status='promoted' WHERE symbol=?", (sym,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.get("/discovery/prompt")
def discovery_prompt():
    """The web-sweep prompt: paste into any agent; it writes discoveries/*.md
    which the watcher ingests (same file contract philosophy as discussions)."""
    day = dt.date.today().isoformat()
    return {"prompt": f"""# TickerBands discovery sweep — {day}
Research CURRENT sources (news, filings, sector coverage) for 8-15 NON-OBVIOUS
US stock/ETF candidates with early potential. EXCLUDE: Mag-7, S&P 500
mainstays, mega-cap household names, flagship index ETFs. For each: a concrete
thesis (what changed recently), and the main risk. No price predictions —
theses about businesses/flows/events only.

Write the result to `~/.tickerbands/discoveries/{day}.md` EXACTLY as:
```markdown
---
date: {day}
candidates:
  - ticker: XYZ
    thesis: <1-2 lines, concrete and recent>
    risk: <1 line>
  - ticker: ...
---
# Sweep notes {day}
<brief method/source notes>
```
The app ingests it automatically; duplicates merge by symbol."""}


@router.get("/lens/{symbol}")
def premium_lens(symbol: str):
    """Premium-seller's lens: implied vs empirical breach probability per
    short-strike candidate (validated DTE window only)."""
    from analysis.premium_lens import compose_lens
    from cache.store import get_or_fetch as _gof
    sym = symbol.upper()
    chain_fn = registry.resolve("option_chain_lens")
    if chain_fn is None:
        return {"available": False, "reason": "no options provider configured"}
    try:
        chain = _gof(f"lens_chain:{sym}", config.TTL["options"],
                     lambda: chain_fn(sym))["data"]
    except ProviderError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")
    bars = composer._bars_2y(sym, False) or []
    closes = [b["close"] for b in bars]
    earnings_days = None
    try:
        efn = registry.resolve("earnings")
        edata = _gof(f"earnings:{sym}", config.TTL["earnings"],
                     lambda: efn(sym))["data"] if efn else {}
        if edata.get("available"):
            earnings_days = edata.get("days_until")
    except ProviderError:
        pass
    return compose_lens(chain, closes, earnings_days)


@router.get("/vrp/{symbol}")
def vrp_report(symbol: str):
    """Accrued implied-move captures vs what realized — per-ticker variance
    risk premium. Thin until the app has been used a while; says so."""
    from cache.store import get_or_fetch as _gof
    sym = symbol.upper()
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT day, spot, dte, implied_pct FROM implied_move_history "
            "WHERE symbol = ? ORDER BY day", (sym,))]
    finally:
        conn.close()
    if not rows:
        return {"samples": [], "n_resolved": 0, "avg_ratio": None}
    try:
        bars = _gof(f"history2y:{sym}", config.TTL["history2y"],
                    lambda: _pv()["history"](sym, 520))["data"]
    except ProviderError:
        bars = []
    by_date = {b["date"]: b["close"] for b in bars}
    dates = sorted(by_date)
    out, ratios = [], []
    for r in rows:
        later = [d for d in dates if d > r["day"]]
        horizon = min(max(int(r["dte"] * 252 / 365), 1), len(later))
        realized = None
        if later and horizon >= 1 and len(later) >= horizon:
            realized = round(abs(by_date[later[horizon - 1]] / r["spot"] - 1) * 100, 2)
            if r["implied_pct"]:
                ratios.append(r["implied_pct"] / realized if realized > 0.05 else None)
        out.append({**r, "realized_pct": realized})
    valid = [x for x in ratios if x]
    return {"samples": out[-60:], "n_resolved": len(valid),
            "avg_ratio": round(sum(valid) / len(valid), 2) if valid else None,
            "note": "ratio >1 = options overpaid realized moves (seller-favorable), <1 = underpaid"}


class WhatIfBody(BaseModel):
    changes: dict[str, float]   # symbol → delta market value in $ (± ; cash auto-adjusts)


@router.post("/portfolio/whatif")
def portfolio_whatif(body: WhatIfBody):
    """Rebalance sandbox: hypothetical position changes → before/after risk +
    income. Pure preview; nothing is ever executed from this app."""
    from analysis.portfolio_risk import portfolio_risk
    from analysis.screener import portfolio_income
    from analysis import vol_bands as vb
    from cache.store import get_or_fetch as _gof
    try:
        account = composer.portfolio(force=False)["data"]
    except ProviderError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")

    def closes_for(symbols: list[str]) -> dict[str, list[float]]:
        pv = _pv()
        out: dict[str, list[float]] = {}
        for s in symbols + ["SPY"]:
            try:
                bars = _gof(f"history:{s}", config.TTL["history"],
                            lambda s=s: pv["history"](s))["data"]
                out[s] = [b["close"] for b in vb.completed_bars(bars)]
            except ProviderError:
                continue
        return out

    def views(positions, cash):
        cmap = closes_for([p["symbol"] for p in positions])
        risk = portfolio_risk(positions, cmap, cash, cmap.get("SPY"))
        income = portfolio_income({"positions": positions,
                                   "total_value": sum(p["market_value"] for p in positions) + cash},
                                  _pv()["fundamentals"])
        return {"risk": risk, "income": {k: income[k] for k in
                ("projected_annual", "blended_yield_pct")}}

    before_pos = [dict(p) for p in account["positions"]]
    after_pos = {p["symbol"]: dict(p) for p in before_pos}
    cash_after = account.get("cash") or 0.0
    for sym, delta in body.changes.items():
        sym = sym.upper()
        cur = after_pos.get(sym) or {"symbol": sym, "market_value": 0.0}
        new_mv = max(0.0, cur["market_value"] + delta)
        cash_after -= (new_mv - cur["market_value"])   # buys consume cash, sells add
        cur["market_value"] = new_mv
        after_pos[sym] = cur
    if cash_after < 0:
        raise HTTPException(422, f"not enough cash: needs ${-cash_after:,.0f} more")
    after_list = [p for p in after_pos.values() if p["market_value"] > 0]
    return {"before": views(before_pos, account.get("cash") or 0.0),
            "after": views(after_list, cash_after),
            "cash_before": account.get("cash") or 0.0, "cash_after": round(cash_after, 2)}


@router.get("/portfolio/risk")
def get_portfolio_risk():
    """Roadmap #6: the account's own 80% range for tomorrow + diversification
    stats. Uses cached per-holding history (fetched on demand, TTL'd)."""
    from analysis.portfolio_risk import portfolio_risk
    from cache.store import get_or_fetch as _gof
    try:
        account = composer.portfolio(force=False)["data"]
    except ProviderError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")
    pv = _pv()
    closes_map: dict[str, list[float]] = {}
    for pos in account["positions"] + [{"symbol": "SPY"}]:
        sym = pos["symbol"]
        try:
            bars = _gof(f"history:{sym}", config.TTL["history"],
                        lambda s=sym: pv["history"](s), force=False)["data"]
            from analysis import vol_bands as vb
            closes_map[sym] = [b["close"] for b in vb.completed_bars(bars)]
        except ProviderError:
            continue
    return portfolio_risk(account["positions"], closes_map,
                          account.get("cash") or 0.0, closes_map.get("SPY"))


@router.get("/score-history/export")
def score_history_export():
    """Approved sharing path (LOGGED): a portable bundle of this instance's
    score history — for another machine/instance, never a silent cloud."""
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT symbol, ts, score, lean, components FROM setup_score_history")]
        conn.execute("INSERT INTO score_share_log (ts, direction, rows, detail) "
                     "VALUES (?, 'export', ?, 'api export')",
                     (dt.datetime.now().isoformat(timespec="seconds"), len(rows)))
        conn.commit()
    finally:
        conn.close()
    return {"format": "tickerbands-score-history-v1",
            "exported_at": dt.datetime.now().isoformat(timespec="seconds"),
            "rows": rows}


class ScoreImportBody(BaseModel):
    rows: list[dict]


@router.post("/score-history/import")
def score_history_import(body: ScoreImportBody):
    """Import a bundle (deduped by symbol+ts). Logged, like export."""
    conn = connect()
    added = 0
    try:
        for r in body.rows:
            sym, ts = str(r.get("symbol", "")).upper(), str(r.get("ts", ""))
            if not sym or not ts or r.get("score") is None:
                continue
            dup = conn.execute("SELECT 1 FROM setup_score_history WHERE symbol=? AND ts=?",
                               (sym, ts)).fetchone()
            if dup:
                continue
            conn.execute("INSERT INTO setup_score_history (symbol, ts, score, lean, components) "
                         "VALUES (?,?,?,?,?)",
                         (sym, ts, float(r["score"]), str(r.get("lean", "NEUTRAL")),
                          str(r.get("components", "[]"))))
            added += 1
        conn.execute("INSERT INTO score_share_log (ts, direction, rows, detail) "
                     "VALUES (?, 'import', ?, ?)",
                     (dt.datetime.now().isoformat(timespec="seconds"), added,
                      f"received {len(body.rows)} rows"))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "imported": added, "received": len(body.rows)}


@router.get("/score-history/share-log")
def score_share_log():
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM score_share_log ORDER BY id DESC LIMIT 50")]
    finally:
        conn.close()


@router.get("/score-audit")
def get_score_audit():
    """Roadmap #10: your accumulated Setup Scores vs subsequent returns."""
    from analysis.score_audit import score_audit
    return score_audit()


@router.get("/settings/band-validation")
def get_band_validation():
    """The engine study shipped with the app (flat20 vs EWMA vs conformal)."""
    with open(config.BAND_VALIDATION_JSON) as f:
        return json.load(f)


class BandEngineBody(BaseModel):
    engine: str


@router.put("/settings/band-engine")
def put_band_engine(body: BandEngineBody):
    if body.engine not in config.BAND_ENGINES:
        raise HTTPException(422, f"engine must be one of {config.BAND_ENGINES}")
    conn = connect()
    try:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) "
                     "VALUES ('band_engine', ?)", (json.dumps(body.engine),))
        conn.commit()
    finally:
        conn.close()
    invalidate("")  # bands recompute with the new engine on next analysis
    return {"ok": True, "engine": body.engine}


@router.get("/morning-sheet")
def morning_sheet():
    """Roadmap #8: one printable daily digest — screener, band breaks,
    earnings this week, portfolio day summary."""
    from fastapi.responses import HTMLResponse
    from analysis.screener import screen_watchlist
    from api.report import build_morning_html
    rows = screen_watchlist(_pv(), force=False)
    try:
        account = composer.portfolio(force=False)["data"]
    except ProviderError:
        account = None
    return HTMLResponse(build_morning_html(rows, account))


@router.get("/portfolio")
def get_portfolio(refresh: bool = Query(False)):
    """Live Schwab account positions (read-only, cached 5m) + organic value
    history. This tab supersedes the old standalone Portfolio app."""
    try:
        wrapped = composer.portfolio(force=refresh)
    except ProviderError as e:
        if e.reason == "auth_expired":
            raise HTTPException(503, f"Schwab auth: {e.detail} (run schwab-reauth)")
        raise HTTPException(502, f"{e.reason}: {e.detail}")
    return {
        **wrapped["data"],
        "source": wrapped["source"],
        "age_seconds": wrapped["age_seconds"],
        "history": composer.portfolio_history(),
    }


@router.get("/search")
def symbol_search(q: str = Query(min_length=2, max_length=40)):
    """Name → ticker lookup ("apple" → AAPL) for the search bar + ⌘K palette."""
    from cache.store import get_or_fetch

    fn = registry.resolve("symbol_search")
    if fn is None:
        return []
    try:
        wrapped = get_or_fetch(
            key=f"search:{q.strip().lower()}",
            ttl_seconds=config.TTL["search"],
            fetch_fn=lambda: fn(q),
        )
        return wrapped["data"]
    except ProviderError as e:
        # Search is a convenience — degrade to "no results" rather than a
        # scary error; direct ticker entry always still works.
        if e.reason == "rate_limited":
            raise HTTPException(429, "Search rate-limited — try again shortly")
        return []


@router.post("/sync")
def sync_all():
    """Nuke the provider cache; next analysis fetches everything live."""
    return {"ok": True, "invalidated": invalidate("")}


# ─── watchlist (TickerBands's own — independent of the Portfolio app's, Q2) ─────

class WatchAdd(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    note: str = ""
    tags: list[str] = []


class WatchPatch(BaseModel):
    note: str | None = None
    tags: list[str] | None = None


@router.get("/watchlist")
def get_watchlist():
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM watchlist ORDER BY added_ts DESC").fetchall()
        return [{**dict(r), "tags": json.loads(r["tags"] or "[]")} for r in rows]
    finally:
        conn.close()


@router.post("/watchlist", status_code=201)
def add_watch(body: WatchAdd):
    sym = body.symbol.upper().strip()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO watchlist (symbol, note, tags, added_ts) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET note=excluded.note, tags=excluded.tags",
            (sym, body.note, json.dumps(body.tags),
             dt.datetime.now().isoformat(timespec="seconds")))
        conn.commit()
        return {"ok": True, "symbol": sym}
    finally:
        conn.close()


@router.patch("/watchlist/{symbol}")
def patch_watch(symbol: str, body: WatchPatch):
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM watchlist WHERE symbol = ?",
                           (symbol.upper(),)).fetchone()
        if not row:
            raise HTTPException(404, "not on watchlist")
        note = body.note if body.note is not None else row["note"]
        tags = json.dumps(body.tags) if body.tags is not None else row["tags"]
        conn.execute("UPDATE watchlist SET note = ?, tags = ? WHERE symbol = ?",
                     (note, tags, symbol.upper()))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/watchlist/{symbol}")
def del_watch(symbol: str):
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "not on watchlist")
        return {"ok": True}
    finally:
        conn.close()


@router.post("/watchlist/import-portfolio")
def import_portfolio_watchlist():
    """One-time convenience (Q2): copy symbols from the Portfolio app's
    watchlist (read-only). Existing TickerBands entries are left alone."""
    if not config.PORTFOLIO_DB_PATH:
        raise HTTPException(501, "no legacy portfolio DB configured (paths.portfolio_db in config.toml)")
    from providers import portfolio_db
    try:
        items = portfolio_db.fetch_watchlist_symbols()
    except ProviderError as e:
        raise HTTPException(503, f"Portfolio DB unavailable: {e.detail}")
    conn = connect()
    added = 0
    try:
        for item in items:
            cur = conn.execute(
                "INSERT OR IGNORE INTO watchlist (symbol, note, tags, added_ts) "
                "VALUES (?, ?, ?, ?)",
                (item["symbol"].upper(), item["note"], json.dumps(["from-portfolio"]),
                 dt.datetime.now().isoformat(timespec="seconds")))
            added += cur.rowcount
        conn.commit()
        return {"ok": True, "imported": added, "seen": len(items)}
    finally:
        conn.close()


# ─── discussions ───────────────────────────────────────────────────────────────

@router.get("/discussions")
def get_discussions(ticker: str | None = None, limit: int = Query(100, le=500)):
    """Stored discussions, newest activity first. Each carries `status`
    (OK / STALE / INVALID / SUPERSEDED, or null when it could not be judged),
    `status_reason` and `status_as_of` — whether the note can still be relied
    on, judged from the notes and the cached daily history (no provider call)."""
    from analysis.screener import note_statuses
    rows = list_discussions(ticker, limit)
    try:
        statuses = note_statuses({r["ticker"] for r in rows})
    except Exception:  # noqa: BLE001 — the timeline loads without the chips
        statuses = {}
    for r in rows:
        st = statuses.get(r["id"]) or {}
        r["status"] = st.get("status")
        r["status_reason"] = st.get("reason") or None
        r["status_as_of"] = st.get("as_of")
    return rows


@router.get("/discussions/stream")
async def discussions_stream():
    """SSE: created/deleted/error events from the file watcher (Q5, deviation 1)."""
    return StreamingResponse(
        broadcaster.sse_events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ─── glossary + settings + health ──────────────────────────────────────────────

@router.get("/glossary")
def get_glossary():
    with open(config.GLOSSARY_JSON) as f:
        data = json.load(f)
    return {"entries": data["entries"], "features": config.FEATURES}


class WeightsBody(BaseModel):
    weights: dict[str, float]


@router.get("/settings")
def get_settings():
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key='score_weights'").fetchone()
        weights = {**config.DEFAULT_WEIGHTS,
                   **(json.loads(row["value"]) if row else {})}
        return {"weights": weights, "defaults": config.DEFAULT_WEIGHTS,
                "score_bands": config.SCORE_BANDS,
                "disclaimer": config.SCORE_DISCLAIMER}
    finally:
        conn.close()


@router.put("/settings/weights")
def put_weights(body: WeightsBody):
    unknown = set(body.weights) - set(config.DEFAULT_WEIGHTS)
    if unknown:
        raise HTTPException(422, f"unknown components: {sorted(unknown)}")
    if any(v < 0 for v in body.weights.values()):
        raise HTTPException(422, "weights must be ≥ 0")
    if sum(body.weights.values()) <= 0:
        raise HTTPException(422, "at least one weight must be > 0")
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('score_weights', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(body.weights),))
        conn.commit()
        return {"ok": True, "weights": {**config.DEFAULT_WEIGHTS, **body.weights}}
    finally:
        conn.close()


@router.get("/capabilities")
def capabilities():
    """What's wired to what — the frontend gates features on this."""
    return registry.snapshot()


@router.get("/health")
def health():
    """Per-provider status for the header dot + degradation banners (Q12)."""
    providers = {}
    for pname, module in registry.MODULES.items():
        if pname == "mock":
            continue
        try:
            providers[pname] = bool(module.health_check())
        except Exception:
            providers[pname] = False
    conn = connect()
    try:
        n = conn.execute("SELECT COUNT(*) AS c FROM claude_discussions").fetchone()["c"]
    finally:
        conn.close()
    return {"ok": True, "providers": providers, "discussions": n,
            "mock_mode": composer.use_mock()}


@router.get("/audit/sec-tags/{symbol}")
def audit_sec_tags(symbol: str, q: str = Query("", max_length=60)):
    """Audit helper: the company's us-gaap XBRL tags (optionally filtered by a
    substring) with each tag's latest period end and value — to check which
    tag a company actually files a line under (tags change over time)."""
    import requests as _rq

    from providers import edgar
    try:
        cik = edgar._cik_for(symbol)
        r = _rq.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                    headers=edgar._ua(), timeout=30)
        us = (r.json().get("facts") or {}).get("us-gaap") or {}
    except Exception as e:
        raise HTTPException(502, f"SEC: {e}")
    out = []
    for tag, body in us.items():
        if q and q.lower() not in tag.lower():
            continue
        rows = [x for u in (body.get("units") or {}).values() for x in u]
        if not rows:
            continue
        last = max(rows, key=lambda x: (x["end"], x.get("filed", "")))
        out.append({"tag": tag, "end": last["end"], "val": last["val"],
                    "form": last.get("form"), "filed": last.get("filed")})
    return sorted(out, key=lambda x: x["end"], reverse=True)[:80]
