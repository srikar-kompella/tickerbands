"""
providers/portfolio_db.py — READ-ONLY window into the Portfolio app's SQLite DB.

Q3 ruling: reuse, don't duplicate. Opened with SQLite's read-only URI mode —
this process physically cannot write to portfolio.db, so the Portfolio app's
sole write ownership is enforced by the connection itself, not by discipline.

Failure contract (Q12): file missing / locked / schema drift ⇒ ProviderError
("unavailable") ⇒ the ownership chip hides. Nothing else in the app depends on
this provider.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any

from config import PORTFOLIO_DB_PATH

name = "portfolio_db"


def _connect() -> sqlite3.Connection:
    from providers.base import ProviderError
    if not PORTFOLIO_DB_PATH or not os.path.exists(PORTFOLIO_DB_PATH):
        raise ProviderError("unavailable", f"portfolio.db not found at {PORTFOLIO_DB_PATH}")
    try:
        conn = sqlite3.connect(f"file:{PORTFOLIO_DB_PATH}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        raise ProviderError("unavailable", f"portfolio.db open failed: {e}")


def health_check() -> bool:
    try:
        conn = _connect()
        conn.close()
        return True
    except Exception:
        return False


def fetch_position(symbol: str) -> dict[str, Any]:
    """Position context for one symbol from the LATEST snapshot.

    Portfolio schema (stable since June): snapshots(id, ts, total_value) ←
    positions(snapshot_id, symbol, qty, price, market_value, cost_basis).
    """
    from providers.base import ProviderError
    conn = _connect()
    try:
        snap = conn.execute(
            "SELECT id, ts, total_value FROM snapshots ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if not snap:
            return {"owned": False, "as_of": None}
        row = conn.execute(
            "SELECT qty, price, market_value, cost_basis FROM positions "
            "WHERE snapshot_id = ? AND symbol = ?",
            (snap["id"], symbol.upper()),
        ).fetchone()
        if not row or not row["qty"]:
            return {"owned": False, "as_of": snap["ts"]}
        mv, cb = row["market_value"] or 0.0, row["cost_basis"] or 0.0
        total = snap["total_value"] or 0.0
        return {
            "owned": True,
            "qty": row["qty"],
            "avg_cost": round(cb / row["qty"], 2) if row["qty"] else None,
            "market_value": round(mv, 2),
            "cost_basis": round(cb, 2),
            "gain": round(mv - cb, 2),
            "gain_pct": round((mv - cb) / cb * 100, 2) if cb else None,
            "weight_pct": round(mv / total * 100, 2) if total else None,
            "as_of": snap["ts"],
        }
    except sqlite3.Error as e:
        raise ProviderError("unavailable", f"portfolio.db query failed: {e}")
    finally:
        conn.close()


def fetch_watchlist_symbols() -> list[dict[str, Any]]:
    """One-time import source for TickerLens's own watchlist (Q2)."""
    conn = _connect()
    try:
        rows = conn.execute("SELECT symbol, note FROM watchlist").fetchall()
        return [{"symbol": r["symbol"], "note": r["note"] or ""} for r in rows]
    finally:
        conn.close()


def fetch(ticker: str) -> dict[str, Any]:
    return {"position": fetch_position(ticker)}
