"""
db.py — SQLite schema + connection helpers for TickerLens.

Tables
  watchlist            research targets (independent of the Portfolio app's — Q2)
  claude_discussions   ingested markdown discussions (file = source of truth, Q5)
  setup_score_history  every computed score, persisted for the organic sparkline (Q8d)
  cache                per-provider TTL cache (survives restarts — Q6)
  settings             user-tunable config (score weights, etc.) as JSON blobs

The schema is idempotent (CREATE IF NOT EXISTS) — running init_db() twice is safe.
"""
from __future__ import annotations

import sqlite3

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    symbol            TEXT PRIMARY KEY,
    note              TEXT DEFAULT '',
    tags              TEXT DEFAULT '[]',      -- JSON array of free-form strings
    added_ts          TEXT NOT NULL,
    last_analyzed_ts  TEXT                    -- bumped whenever analysis is fetched
);

-- Matches the handoff schema exactly, plus nothing (Q5: rows are hard-deleted
-- when the underlying file is deleted; file is source of truth).
CREATE TABLE IF NOT EXISTS claude_discussions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker            TEXT NOT NULL,
    file_path         TEXT NOT NULL UNIQUE,   -- the underlying markdown file
    prompt            TEXT,                   -- what was sent (null if freeform)
    response_markdown TEXT NOT NULL,          -- full markdown body
    summary           TEXT,                   -- from frontmatter (timeline display)
    tags              TEXT,                   -- JSON array from frontmatter
    context_hash      TEXT,                   -- which analysis snapshot it discusses
    context_snapshot  TEXT,                   -- optional JSON cache of that analysis
    created_ts        TEXT NOT NULL,
    file_mtime        TEXT NOT NULL           -- staleness detection
);
CREATE INDEX IF NOT EXISTS idx_discussions_ticker ON claude_discussions(ticker);
CREATE INDEX IF NOT EXISTS idx_discussions_ts     ON claude_discussions(created_ts);

-- Organic score history (Q8d): a row every time a score is computed. No
-- backfill — point-in-time sentiment/options/analyst history does not exist,
-- and a price-only backfill wearing the full score's name would be a lie.
CREATE TABLE IF NOT EXISTS setup_score_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    ts          TEXT NOT NULL,
    score       REAL NOT NULL,
    lean        TEXT NOT NULL,                -- BULLISH / NEUTRAL / BEARISH
    components  TEXT NOT NULL                 -- JSON: per-component contributions
);
CREATE INDEX IF NOT EXISTS idx_score_hist ON setup_score_history(symbol, ts);

-- SQLite-backed provider cache. Key convention: "<provider>:<kind>:<symbol>".
CREATE TABLE IF NOT EXISTS cache (
    key         TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,                -- JSON
    fetched_ts  REAL NOT NULL                 -- unix seconds
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL                 -- JSON
);

-- VRP accrual: the options-implied move captured whenever a symbol is
-- analyzed (one row/symbol/day). Once the horizon elapses, realized vs
-- implied becomes a per-ticker variance-risk-premium report — the data no
-- free API sells, built by using the app.
CREATE TABLE IF NOT EXISTS implied_move_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT NOT NULL,
    day          TEXT NOT NULL,               -- YYYY-MM-DD (dedupe key w/ symbol)
    spot         REAL NOT NULL,
    dte          INTEGER NOT NULL,
    implied_pct  REAL NOT NULL,
    UNIQUE(symbol, day)
);

-- Organic portfolio value history (same philosophy as setup_score_history):
-- one row per LIVE portfolio fetch — the chart grows with use, no backfill.
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    total_value  REAL NOT NULL,
    cash         REAL
);

-- Action plan (P5 — ported from the old Portfolio app's checklist).
CREATE TABLE IF NOT EXISTS actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT,
    action      TEXT NOT NULL,        -- TRIM / SELL / BUY / HOLD / free-form
    rationale   TEXT DEFAULT '',
    status      TEXT DEFAULT 'open',  -- open / done / dismissed
    priority    INTEGER DEFAULT 0,
    created_ts  TEXT NOT NULL
);
"""

# Columns added after the first release — SQLite CREATE IF NOT EXISTS won't
# alter existing tables, so idempotent ALTERs run at init (failure = exists).
MIGRATIONS = [
    "ALTER TABLE claude_discussions ADD COLUMN news_view TEXT",
    "ALTER TABLE claude_discussions ADD COLUMN news_note TEXT",
    # P3 decision journal: what the user decided, and the price when he decided.
    "ALTER TABLE claude_discussions ADD COLUMN decision TEXT",
    "ALTER TABLE claude_discussions ADD COLUMN decision_price REAL",
    # Research stance: Claude's reasoned buy/sell/hold/trim/watch from a
    # discussion session (the app surfaces it, never computes it — the
    # computed signals proved unable to call direction at n≈9,600).
    "ALTER TABLE claude_discussions ADD COLUMN stance TEXT",
    "ALTER TABLE claude_discussions ADD COLUMN stance_horizon TEXT",
    "ALTER TABLE claude_discussions ADD COLUMN stance_note TEXT",
]


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open the TickerLens DB (row access by name, FKs on, WAL for the
    watcher-thread + request-thread concurrency pattern)."""
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: str | None = None) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        for stmt in MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists — migration is idempotent
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Initialized {DB_PATH}")
