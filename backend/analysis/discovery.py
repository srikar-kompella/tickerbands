"""
analysis/discovery.py — the Discover pipeline: evidence screens + web-sweep
ingestion → deduped, persistent candidate leads.

Honesty rules: candidates are LEADS, not recommendations — every row carries
its evidence type, reasoning, and date; screens use documented-signal sources
(insider clusters, PEAD, SI deltas); "obvious" mainstream names (Mag-7 /
index mainstays / mega ETFs / >$75B) are excluded by design; dismissals are
remembered — a dismissed name only resurfaces flagged `returned` when NEW
evidence arrives.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

from db import connect
from providers.base import ProviderError

# "Not the typical ones": famous megacaps + index/theme mainstream ETFs.
OBVIOUS = {
    "AAPL","MSFT","GOOGL","GOOG","AMZN","META","NVDA","TSLA","AVGO","BRK.B",
    "BRK.A","LLY","JPM","V","MA","UNH","XOM","CVX","WMT","JNJ","PG","HD","KO",
    "PEP","COST","ABBV","MRK","BAC","ORCL","CRM","AMD","NFLX","ADBE","DIS",
    "INTC","CSCO","QCOM","IBM","TXN","GE","CAT","MCD","NKE","PFE","T","VZ",
    "SPY","VOO","IVV","QQQ","VTI","IWM","DIA","VT","VXUS","VEA","VWO","AGG",
    "BND","TLT","GLD","SLV","VUG","VTV","SCHD","JEPI","JEPQ","SMH","XLK",
    "XLE","XLF","XLV","ARKK","EFA","EEM","LQD","HYG","VIG","VYM","RSP",
}
OBVIOUS_CAP_M = 75_000  # >$75B counts as a "big bet" even if not listed


def is_obvious(symbol: str, market_cap_m: float | None = None) -> bool:
    return (symbol.upper() in OBVIOUS
            or (market_cap_m is not None and market_cap_m > OBVIOUS_CAP_M))


def upsert_candidate(symbol: str, source: str, reason: str,
                     market_cap_m: float | None = None,
                     date: str | None = None) -> bool:
    """Dedupe-merge. Returns True if this added NEW evidence. Dismissed rows
    stay dismissed but get `returned=1` when a NEW source type shows up."""
    symbol = symbol.upper().strip()
    if not symbol or is_obvious(symbol, market_cap_m):
        return False
    day = date or dt.date.today().isoformat()
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM discovery_candidates WHERE symbol=?",
                           (symbol,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO discovery_candidates (symbol, first_seen, last_seen,"
                " sources, reasons, market_cap_m) VALUES (?,?,?,?,?,?)",
                (symbol, day, day, json.dumps([source]),
                 json.dumps([{"source": source, "reason": reason, "date": day}]),
                 market_cap_m))
            conn.commit()
            return True
        sources = json.loads(row["sources"])
        reasons = json.loads(row["reasons"])
        if any(r["source"] == source and r["reason"] == reason for r in reasons):
            return False  # exact duplicate evidence
        new_source_type = source not in sources
        if new_source_type:
            sources.append(source)
        reasons.append({"source": source, "reason": reason, "date": day})
        conn.execute(
            "UPDATE discovery_candidates SET last_seen=?, sources=?, reasons=?, "
            "market_cap_m=COALESCE(?, market_cap_m), "
            "returned=CASE WHEN status='dismissed' AND ? THEN 1 ELSE returned END "
            "WHERE symbol=?",
            (day, json.dumps(sources), json.dumps(reasons[-12:]),
             market_cap_m, int(new_source_type), symbol))
        conn.commit()
        return True
    finally:
        conn.close()


def list_candidates(include_dismissed: bool = False) -> list[dict[str, Any]]:
    conn = connect()
    try:
        q = "SELECT * FROM discovery_candidates"
        if not include_dismissed:
            q += " WHERE status != 'dismissed'"
        rows = [dict(r) for r in conn.execute(q).fetchall()]
    finally:
        conn.close()
    for r in rows:
        r["sources"] = json.loads(r["sources"])
        r["reasons"] = json.loads(r["reasons"])
    # convergence first (multiple independent evidence types), then recency
    rows.sort(key=lambda r: (-len(r["sources"]), r["last_seen"]), reverse=False)
    rows.sort(key=lambda r: (len(r["sources"]), r["last_seen"]), reverse=True)
    return rows


# ─── screens (each capped + defensive; a dead source contributes 0, not 500) ──

def screen_pead(calendar_fn, cap_fn=None, max_new: int = 10) -> int:
    """Strong recent beats (≥10% EPS surprise, last 14d) — PEAD window leads."""
    today = dt.date.today()
    try:
        events = calendar_fn((today - dt.timedelta(days=14)).isoformat(),
                             (today - dt.timedelta(days=1)).isoformat())
    except ProviderError:
        return 0
    scored = []
    for e in events:
        est, act, sym = e.get("epsEstimate"), e.get("epsActual"), (e.get("symbol") or "").upper()
        if not sym or est in (None, 0) or act is None:
            continue
        surprise = (act - est) / abs(est) * 100
        if surprise >= 10:
            scored.append((surprise, sym, e.get("date")))
    scored.sort(reverse=True)
    added = 0
    for surprise, sym, day in scored:
        if added >= max_new:
            break
        cap = None
        if cap_fn:
            try:
                f = cap_fn(sym)
                cap = f.get("market_cap_m") if f.get("available") else None
            except ProviderError:
                pass
        if upsert_candidate(sym, "pead",
                            f"beat EPS estimates by {surprise:.0f}% on {day} — "
                            f"inside the ~60-trading-day drift window", cap, day):
            added += 1
    return added


def screen_insider_feed(feed_fn, max_new: int = 8) -> int:
    """Issuers with MULTIPLE Form 4s in EDGAR's latest-filings feed — a cheap
    market-wide cluster proxy. The Analysis page's Insiders panel is the
    verification step (open-market? distinct owners?) — the reason says so."""
    try:
        counts = feed_fn()   # {TICKER: n_filings_in_recent_feed}
    except ProviderError:
        return 0
    added = 0
    for sym, n in sorted(counts.items(), key=lambda x: -x[1]):
        if n < 2 or added >= max_new:
            continue
        if upsert_candidate(sym, "insider-cluster",
                            f"{n} Form 4 filings in the latest EDGAR feed — "
                            "possible cluster; verify open-market buys in Analysis"):
            added += 1
    return added


def screen_short_interest(si_top_fn, max_new: int = 6) -> int:
    """Largest SI% builds from FINRA's file — crowded-bet / squeeze context."""
    try:
        rows = si_top_fn()   # [{symbol, change_pct, days_to_cover}]
    except ProviderError:
        return 0
    added = 0
    for r in rows:
        if added >= max_new:
            break
        if abs(r.get("change_pct") or 0) < 20:
            continue
        if upsert_candidate(r["symbol"], "short-interest",
                            f"short interest {r['change_pct']:+.0f}% vs prior period, "
                            f"{r.get('days_to_cover', '?')} days to cover"):
            added += 1
    return added


def ingest_sweep_frontmatter(meta: dict, day: str | None = None) -> int:
    """Web-sweep file contract: frontmatter `candidates:` list of
    {ticker, thesis, source?, risk?} → upserts tagged 'web-sweep'."""
    added = 0
    for c in (meta.get("candidates") or []):
        sym = str(c.get("ticker") or "").upper().strip()
        thesis = str(c.get("thesis") or "").strip()
        if not sym or not thesis:
            continue
        risk = str(c.get("risk") or "").strip()
        reason = thesis + (f" | Risk: {risk}" if risk else "")
        if upsert_candidate(sym, "web-sweep", reason[:500], date=day):
            added += 1
    return added
