"""
discussions_svc/watcher.py — watchdog observer over discussions/.

Flow (locked architecture): Claude Code CLI writes discussions/{TICKER}/*.md →
this observer ingests it into SQLite → broadcaster pushes an SSE event → the
UI's timeline updates within ~a second. Zero manual save step.

Q5c — the partial-write race, handled: on_created fires when a file APPEARS,
not when the writer finishes; macOS FSEvents has no on_closed. So every
created/modified event goes through a per-path DEBOUNCE (a fresh event resets
the timer) and, when the timer fires, a STABILITY gate (size+mtime unchanged
across a short window) before parsing. Parse failures retry ×3 (file may still
be mid-write), then the file moves to discussions/_failed/ and an error event
tells the UI, instead of silently ingesting a truncated discussion.
"""
from __future__ import annotations

import os
import shutil
import threading
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from config import DISCOVERIES_DIR, DISCUSSIONS_DIR, DISCUSSIONS_FAILED_DIR
from discussions_svc.parser import (ParseError, delete_discussion,
                                    parse_discussion_file, upsert_discussion)
from discussions_svc.stream import broadcaster

DEBOUNCE_SECONDS = 1.0
STABILITY_PROBE_SECONDS = 0.4
PARSE_RETRIES = 3


def _is_discussion_md(path: str) -> bool:
    """Only .md files, and never anything under _failed/ (no ingest loops)."""
    return (path.endswith(".md")
            and os.sep + "_failed" + os.sep not in path
            and not os.path.basename(path).startswith("."))


def _wait_until_stable(path: str, timeout: float = 10.0) -> bool:
    """True once size+mtime stop changing; False if gone or still hot."""
    deadline = time.time() + timeout
    try:
        prev = os.stat(path)
    except OSError:
        return False
    while time.time() < deadline:
        time.sleep(STABILITY_PROBE_SECONDS)
        try:
            cur = os.stat(path)
        except OSError:
            return False
        if (cur.st_size, cur.st_mtime) == (prev.st_size, prev.st_mtime):
            return True
        prev = cur
    return False


def _ingest(path: str) -> None:
    """Stability gate → parse (with retries) → upsert → SSE. Runs on a
    debounce-timer thread; all failure paths emit an event, never raise."""
    if not _wait_until_stable(path):
        return  # deleted mid-write, or writer never settled — a later event will retry
    last_err = None
    for attempt in range(PARSE_RETRIES):
        try:
            row = parse_discussion_file(path)
            row_id = upsert_discussion(row)
            broadcaster.publish_threadsafe(
                {"type": "created", "ticker": row["ticker"], "id": row_id})
            return
        except ParseError as e:
            last_err = e
            time.sleep(0.5 * (attempt + 1))  # writer may still be flushing
    # Genuinely malformed: quarantine + tell the UI (no silent data loss).
    try:
        os.makedirs(DISCUSSIONS_FAILED_DIR, exist_ok=True)
        shutil.move(path, os.path.join(DISCUSSIONS_FAILED_DIR, os.path.basename(path)))
    except OSError:
        pass
    broadcaster.publish_threadsafe(
        {"type": "error", "file": os.path.basename(path), "detail": str(last_err)})


class DiscussionHandler(FileSystemEventHandler):
    """Created/modified share the debounced-ingest path (Q5b: edits upsert).
    Deletions remove the DB row immediately (Q5a: file is source of truth)."""

    def __init__(self) -> None:
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def _schedule(self, path: str) -> None:
        with self._lock:
            if (t := self._timers.get(path)) is not None:
                t.cancel()  # fresh write activity → restart the clock
            timer = threading.Timer(DEBOUNCE_SECONDS, self._fire, args=(path,))
            timer.daemon = True
            self._timers[path] = timer
            timer.start()

    def _fire(self, path: str) -> None:
        with self._lock:
            self._timers.pop(path, None)
        _ingest(path)

    # watchdog callbacks (observer thread) --------------------------------------
    def on_created(self, event):
        if not event.is_directory and _is_discussion_md(event.src_path):
            self._schedule(event.src_path)

    def on_modified(self, event):
        if not event.is_directory and _is_discussion_md(event.src_path):
            self._schedule(event.src_path)

    def on_moved(self, event):
        # editors save via tmp-rename; treat the destination as a write
        dest = getattr(event, "dest_path", "")
        if dest and _is_discussion_md(dest):
            self._schedule(dest)
        if _is_discussion_md(event.src_path):
            self._on_gone(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory and _is_discussion_md(event.src_path):
            self._on_gone(event.src_path)

    @staticmethod
    def _on_gone(path: str) -> None:
        removed = delete_discussion(path)
        if removed:
            broadcaster.publish_threadsafe(
                {"type": "deleted", "ticker": removed["ticker"], "id": removed["id"]})


def _ingest_discovery(path: str) -> None:
    """discoveries/*.md → candidate upserts (web-sweep file contract)."""
    if not _wait_until_stable(path):
        return
    try:
        import frontmatter
        from analysis.discovery import ingest_sweep_frontmatter
        post = frontmatter.load(path)
        day = str(post.metadata.get("date") or "")[:10] or None
        n = ingest_sweep_frontmatter(post.metadata, day)
        broadcaster.publish_threadsafe({"type": "discovery", "added": n})
    except Exception as e:
        broadcaster.publish_threadsafe(
            {"type": "error", "file": os.path.basename(path), "detail": str(e)[:200]})


class DiscoveryHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            threading.Timer(DEBOUNCE_SECONDS, _ingest_discovery,
                            args=(event.src_path,)).start()

    def on_modified(self, event):
        self.on_created(event)


def catch_up_scan() -> int:
    """Ingest files written while the backend was down (startup reconciliation).
    Files already in the DB with an unchanged mtime are skipped by upsert
    semantics (idempotent). Returns number of files examined."""
    count = 0
    for root, _dirs, files in os.walk(DISCUSSIONS_DIR):
        if os.sep + "_failed" in root:
            continue
        for fname in files:
            path = os.path.join(root, fname)
            if _is_discussion_md(path):
                count += 1
                try:
                    upsert_discussion(parse_discussion_file(path))
                except ParseError:
                    pass  # leave in place; live edits will re-trigger ingestion
    try:
        import frontmatter
        from analysis.discovery import ingest_sweep_frontmatter
        for fname in sorted(os.listdir(DISCOVERIES_DIR)) if os.path.isdir(DISCOVERIES_DIR) else []:
            if fname.endswith(".md"):
                fp = os.path.join(DISCOVERIES_DIR, fname)
                post = frontmatter.load(fp)
                ingest_sweep_frontmatter(post.metadata,
                                         str(post.metadata.get("date") or "")[:10] or None)
                count += 1
    except Exception:
        pass
    return count


def start_watcher() -> Observer:
    """Start observing discussions/ (created if missing). Called from lifespan;
    returns the observer so shutdown can stop it cleanly."""
    os.makedirs(DISCUSSIONS_DIR, exist_ok=True)
    os.makedirs(DISCOVERIES_DIR, exist_ok=True)
    observer = Observer()
    observer.schedule(DiscussionHandler(), DISCUSSIONS_DIR, recursive=True)
    observer.schedule(DiscoveryHandler(), DISCOVERIES_DIR, recursive=False)
    observer.daemon = True
    observer.start()
    return observer
