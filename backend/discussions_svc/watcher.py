"""
discussions_svc/watcher.py — watchdog observer over discussions/.

Flow (locked architecture): Claude Code CLI writes discussions/{TICKER}/*.md →
this observer ingests it into SQLite → broadcaster pushes an SSE event → the
UI's timeline updates within ~a second. Zero manual save step.

Q5c — the partial-write race, handled: on_created fires when a file APPEARS,
not when the writer finishes; macOS FSEvents has no on_closed. So every
created/modified event goes through a per-path DEBOUNCE (a fresh event resets
the timer) and, when the timer fires, a STABILITY gate (size+mtime unchanged
across a short window) before parsing. Parse failures — of ANY exception
type — retry ×3 (file may still be mid-write), then the file moves to
discussions/_failed/ and an error event tells the UI, instead of silently
ingesting a truncated discussion or leaving a file that is neither ingested
nor quarantined. A database error while storing a good file is reported the
same way, but the file stays where it is: the next write or the startup scan
picks it up.

The startup scan (catch_up_scan) applies the same rule to files written while
the backend was down: a file that cannot be parsed is moved to _failed/ and
named on the console. It used to be skipped without a word, so a rejected
note sat in its ticker folder looking like any other and never showed up.
"""
from __future__ import annotations

import os
import shutil
import threading
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from config import DISCOVERIES_DIR, DISCUSSIONS_DIR, DISCUSSIONS_FAILED_DIR
from discussions_svc.parser import (delete_discussion, parse_discussion_file,
                                    upsert_discussion)
from discussions_svc.stream import broadcaster

DEBOUNCE_SECONDS = 1.0
STABILITY_PROBE_SECONDS = 0.4
PARSE_RETRIES = 3
# the startup scan leaves a file touched this recently to the live watcher
# (already running): it may still be mid-write
RECENT_WRITE_SECONDS = 5.0
# safety net under the file-system observer (see sweep_once)
SWEEP_SECONDS = 20.0
_SEEN: dict[str, float] = {}          # path → mtime of the version last ingested


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


def _quarantine(path: str) -> str | None:
    """Move a file that cannot be parsed to discussions/_failed/. Returns
    where it went, or None when the move itself failed (the file stays)."""
    dest = os.path.join(DISCUSSIONS_FAILED_DIR, os.path.basename(path))
    try:
        os.makedirs(DISCUSSIONS_FAILED_DIR, exist_ok=True)
        shutil.move(path, dest)
    except OSError:
        return None
    return dest


def _ingest(path: str) -> None:
    """Stability gate → parse (with retries) → upsert → SSE. Runs on a
    debounce-timer thread; all failure paths emit an event, never raise."""
    if not _wait_until_stable(path):
        return  # deleted mid-write, or writer never settled — a later event will retry
    row, last_err = None, None
    for attempt in range(PARSE_RETRIES):
        try:
            row = parse_discussion_file(path)
            break
        except Exception as e:  # noqa: BLE001 — ParseError or anything else:
            last_err = e        # the file is unreadable either way
            time.sleep(0.5 * (attempt + 1))  # writer may still be flushing
    if row is None:
        # Genuinely malformed: quarantine + tell the UI (no silent data loss).
        _quarantine(path)
        broadcaster.publish_threadsafe(
            {"type": "error", "file": os.path.basename(path), "detail": str(last_err)})
        return
    for attempt in range(PARSE_RETRIES):
        try:
            row_id = upsert_discussion(row)
            try:
                _SEEN[path] = os.path.getmtime(path)
            except OSError:
                pass
            broadcaster.publish_threadsafe(
                {"type": "created", "ticker": row["ticker"], "id": row_id})
            return
        except Exception as e:  # noqa: BLE001 — e.g. the database is locked
            last_err = e
            time.sleep(0.5 * (attempt + 1))
    # the file is fine, the store is not: leave the file in place and say so
    broadcaster.publish_threadsafe(
        {"type": "error", "file": os.path.basename(path),
         "detail": f"parsed but not stored: {last_err}"[:200]})


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
    semantics (idempotent). Returns number of files examined.

    A file that cannot be parsed is quarantined to _failed/ like the live
    watcher does, and named on the console with the reason — never skipped
    silently. Two cases stay in place: a file touched in the last
    RECENT_WRITE_SECONDS (it may be mid-write; the live watcher has it), and
    a file that parsed but could not be stored (the store is at fault)."""
    count = 0
    for root, _dirs, files in os.walk(DISCUSSIONS_DIR):
        if os.sep + "_failed" in root:
            continue
        for fname in files:
            path = os.path.join(root, fname)
            if not _is_discussion_md(path):
                continue
            count += 1
            try:
                row = parse_discussion_file(path)
            except Exception as e:  # noqa: BLE001 — one bad file must not stop the scan
                try:
                    fresh = time.time() - os.path.getmtime(path) < RECENT_WRITE_SECONDS
                except OSError:
                    continue                      # gone since the walk
                if fresh:
                    continue
                dest = _quarantine(path)
                print(f"[tickerbands] discussion rejected at startup: {path} — {e}"
                      + (f" (moved to {dest})" if dest else " (could not be moved to _failed/)"))
                continue
            try:
                upsert_discussion(row)
            except Exception as e:  # noqa: BLE001 — e.g. the database is locked
                print(f"[tickerbands] discussion parsed but not stored: {path} — {e}")
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


def _note_files() -> list[str]:
    out = []
    for root, _dirs, files in os.walk(DISCUSSIONS_DIR):
        if os.sep + "_failed" in root:
            continue
        out += [os.path.join(root, f) for f in files
                if _is_discussion_md(os.path.join(root, f))]
    return out


def prime_seen() -> None:
    """Record every note file as it stands now (startup: catch_up_scan has
    just reconciled them), so the sweep only acts on later changes."""
    for path in _note_files():
        try:
            _SEEN[path] = os.path.getmtime(path)
        except OSError:
            pass


def sweep_once(now: float | None = None) -> list[str]:
    """Ingest note files the observer never reported. File-system events can
    be dropped — a sleeping laptop, a synced or network folder, an editor that
    replaces the file in a way the observer misses — and then a finished
    research note sat on disk while the app showed the old one until a
    restart. Every SWEEP_SECONDS this compares each file's mtime with the
    version last ingested and runs the normal ingest (store + live event) for
    any that differ. Files touched in the last few seconds are left to the
    observer's own debounce. Returns the paths it ingested."""
    now = time.time() if now is None else now
    done = []
    for path in _note_files():
        try:
            m = os.path.getmtime(path)
        except OSError:
            continue
        if _SEEN.get(path) == m or now - m < RECENT_WRITE_SECONDS:
            continue
        _ingest(path)
        _SEEN.setdefault(path, m)       # a file that failed to store is not retried forever
        done.append(path)
    # a file removed while the observer was not looking: drop its row too
    for path in [p for p in list(_SEEN) if not os.path.exists(p)]:
        _SEEN.pop(path, None)
        try:
            row = delete_discussion(path)
        except Exception:  # noqa: BLE001
            row = None
        if row:
            broadcaster.publish_threadsafe(
                {"type": "deleted", "ticker": row.get("ticker"), "id": row.get("id")})
    return done


def _sweep_loop(stop: threading.Event) -> None:
    while not stop.wait(SWEEP_SECONDS):
        try:
            sweep_once()
        except Exception as e:  # noqa: BLE001 — the safety net must not die
            print(f"[tickerbands] note sweep failed: {e}")


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
    # the sweep thread stops with the process (daemon) or via observer.sweep_stop
    stop = threading.Event()
    t = threading.Thread(target=_sweep_loop, args=(stop,), daemon=True, name="note-sweep")
    t.start()
    observer.sweep_stop = stop          # type: ignore[attr-defined]
    return observer
