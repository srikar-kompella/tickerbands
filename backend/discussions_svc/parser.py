"""
discussions_svc/parser.py — markdown+frontmatter → claude_discussions row.

(Package named *_svc so it can't shadow the repo-root discussions/ folder the
watcher observes.)

The Ask-Claude prompt instructs Claude Code CLI to write:

    discussions/{TICKER}/{ISO_DATETIME}.md
    ---
    ticker: AAPL
    created_ts: 2026-07-13T14:32:15
    summary: One-line takeaway for the timeline
    tags: [earnings-review, technical]
    context_hash: sha256:abc123
    ---
    # body...

Humans and models both write files, so parsing is forgiving where safe
(missing summary → first body line; missing ticker → parent folder name) and
strict where lying would corrupt the timeline (no body → ParseError).
"""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

import frontmatter

from db import connect


class ParseError(Exception):
    pass


def parse_discussion_file(path: str) -> dict[str, Any]:
    """Read + validate one markdown file → dict shaped like the DB row."""
    try:
        post = frontmatter.load(path)
    except Exception as e:  # YAML errors, encoding garbage, half-written files
        raise ParseError(f"frontmatter parse failed: {e}")

    body = (post.content or "").strip()
    if not body:
        raise ParseError("empty body")

    meta = post.metadata or {}

    ticker = str(meta.get("ticker") or "").upper().strip()
    if not ticker:
        # fall back to the folder convention discussions/{TICKER}/file.md
        ticker = os.path.basename(os.path.dirname(path)).upper()
    if not ticker or ticker.startswith("_"):
        raise ParseError("no ticker in frontmatter or folder name")

    created = meta.get("created_ts")
    if isinstance(created, (dt.datetime, dt.date)):
        created_ts = created.isoformat()
    elif created:
        created_ts = str(created)
    else:  # absent → file mtime is the honest default
        created_ts = dt.datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")

    summary = str(meta.get("summary") or "").strip()
    if not summary:
        # first non-heading body line, trimmed for the timeline
        for line in body.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                summary = line[:160]
                break

    tags = meta.get("tags") or []
    if isinstance(tags, str):  # tolerate `tags: earnings, technical`
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    # Optional Claude-supplied news assessment (prompt v3 asks for it after a
    # live news check in the CLI session). Invalid values are dropped, not fatal.
    news_view = str(meta.get("news_view") or "").lower().strip() or None
    if news_view not in {"bullish", "neutral", "bearish", None}:
        news_view = None

    # Optional decision journal entry (P3). The price at decision time is
    # captured at ingest (upsert_discussion), not here — parsing stays pure.
    decision = str(meta.get("decision") or "").lower().strip() or None
    if decision not in {"buy", "sell", "trim", "hold", "pass", None}:
        decision = None

    # Optional research STANCE — Claude's reasoned call from the session
    # (distinct from `decision`, which is what user decided). Invalid → dropped.
    stance = str(meta.get("stance") or "").lower().strip() or None
    if stance not in {"buy", "sell", "hold", "trim", "watch", None}:
        stance = None

    return {
        "ticker": ticker,
        "file_path": os.path.abspath(path),
        "prompt": meta.get("prompt"),
        "response_markdown": body,
        "summary": summary,
        "tags": json.dumps([str(t) for t in tags]),
        "context_hash": str(meta.get("context_hash") or "") or None,
        "context_snapshot": None,  # optional cache; not populated by files
        "news_view": news_view,
        "news_note": (str(meta.get("news_note")) if meta.get("news_note") else None),
        "decision": decision,
        "stance": stance,
        "stance_horizon": (str(meta.get("stance_horizon")).strip()
                           if meta.get("stance_horizon") else None),
        "stance_note": (str(meta.get("stance_note")).strip()
                        if meta.get("stance_note") else None),
        "created_ts": created_ts,
        "file_mtime": dt.datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds"),
    }


# ─── DB ops (upsert keyed on file_path — Q5b edits update in place) ────────────

_ROW_DEFAULTS = {
    "prompt": None, "context_hash": None, "context_snapshot": None,
    "news_view": None, "news_note": None, "decision": None,
    "decision_price": None, "stance": None, "stance_horizon": None,
    "stance_note": None,
}


def _capture_decision_price(ticker: str) -> float | None:
    """P3: the price on record when a decision lands. Cached quote first
    (fresh if the user just ran the analysis — the normal flow), cached
    history's last close as fallback. None if neither — the review view
    shows '—' rather than inventing a fill price."""
    import json as _json
    conn = connect()
    try:
        q = conn.execute("SELECT payload FROM cache WHERE key = ?",
                         (f"quote:{ticker}",)).fetchone()
        if q:
            last = (_json.loads(q["payload"]) or {}).get("last")
            if last:
                return float(last)
        h = conn.execute("SELECT payload FROM cache WHERE key = ?",
                         (f"history:{ticker}",)).fetchone()
        if h:
            bars = _json.loads(h["payload"]) or []
            if bars:
                return float(bars[-1]["close"])
    finally:
        conn.close()
    return None


def upsert_discussion(row: dict[str, Any]) -> int:
    row = {**_ROW_DEFAULTS, **row}
    if row.get("decision") and row.get("decision_price") is None:
        row["decision_price"] = _capture_decision_price(row["ticker"])
    conn = connect()
    try:
        cur = conn.execute(
            """INSERT INTO claude_discussions
               (ticker, file_path, prompt, response_markdown, summary, tags,
                context_hash, context_snapshot, news_view, news_note,
                decision, decision_price, stance, stance_horizon, stance_note,
                created_ts, file_mtime)
               VALUES (:ticker, :file_path, :prompt, :response_markdown, :summary,
                       :tags, :context_hash, :context_snapshot, :news_view,
                       :news_note, :decision, :decision_price, :stance,
                       :stance_horizon, :stance_note, :created_ts, :file_mtime)
               ON CONFLICT(file_path) DO UPDATE SET
                 ticker=excluded.ticker, prompt=excluded.prompt,
                 response_markdown=excluded.response_markdown,
                 summary=excluded.summary, tags=excluded.tags,
                 context_hash=excluded.context_hash,
                 news_view=excluded.news_view, news_note=excluded.news_note,
                 decision=excluded.decision,
                 decision_price=COALESCE(claude_discussions.decision_price,
                                         excluded.decision_price),
                 stance=excluded.stance, stance_horizon=excluded.stance_horizon,
                 stance_note=excluded.stance_note,
                 created_ts=excluded.created_ts, file_mtime=excluded.file_mtime
            """, row)
        conn.commit()
        got = conn.execute("SELECT id FROM claude_discussions WHERE file_path = ?",
                           (row["file_path"],)).fetchone()
        return int(got["id"]) if got else int(cur.lastrowid)
    finally:
        conn.close()


def delete_discussion(file_path: str) -> dict[str, Any] | None:
    """Q5a: file deleted → row hard-deleted (file is source of truth).
    Returns {id, ticker} of the removed row for the SSE event, else None."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT id, ticker FROM claude_discussions WHERE file_path = ?",
            (os.path.abspath(file_path),)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM claude_discussions WHERE id = ?", (row["id"],))
        conn.commit()
        return {"id": row["id"], "ticker": row["ticker"]}
    finally:
        conn.close()


def list_discussions(ticker: str | None = None, limit: int = 100) -> list[dict]:
    # Ordered by LAST ACTIVITY (file_mtime), newest first — an edited/appended
    # file surfaces at the top instead of sitting at its original created_ts
    # position (the user's ask #2). created_ts breaks ties.
    conn = connect()
    try:
        if ticker:
            rows = conn.execute(
                "SELECT * FROM claude_discussions WHERE ticker = ? "
                "ORDER BY file_mtime DESC, created_ts DESC LIMIT ?",
                (ticker.upper(), limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM claude_discussions "
                "ORDER BY file_mtime DESC, created_ts DESC LIMIT ?",
                (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d.get("tags") or "[]")
            out.append(d)
        return out
    finally:
        conn.close()
