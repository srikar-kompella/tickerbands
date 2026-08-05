"""
migrate_instance.py — import user data from another TickerLens installation
(e.g. a private/Prime checkout) into THIS instance's home (~/.tickerlens).

What it moves (and what it deliberately doesn't):
  MOVED    watchlist, setup_score_history, portfolio_snapshots,
           implied_move_history, actions, score_share_log,
           discovery_candidates, settings, claude_discussions (+ the
           discussion/discovery markdown files, mtimes preserved so the
           watcher's catch-up scan skips them instead of re-ingesting —
           re-ingestion would re-capture decision prices at TODAY's quote).
  COPIED   Schwab token file (copy, never move — the source stays intact).
  SKIPPED  the TTL cache (rebuilds itself in minutes).

Safety:
  * The SOURCE database is opened read-only (mode=ro). Nothing is ever
    written to the source installation.
  * The destination DB is backed up to tickerlens.db.bak-<timestamp> first.
  * Idempotent: rerunning inserts nothing new (row-identity checks), so a
    half-finished migration can simply be run again.

Usage (from this backend/ directory, any Python 3.10+):
  python3 migrate_instance.py \
      --source-db  ~/old-tickerlens/backend/tickerlens.db \
      --source-discussions ~/old-tickerlens/discussions \
      --source-discoveries ~/old-tickerlens/discoveries \
      --source-repo ~/old-tickerlens \
      --source-token /path/to/schwab_token.json \
      [--dest-home ~/.tickerlens] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time

# tables copied verbatim (column-intersection, row-identity dedupe).
# claude_discussions is handled separately (file_path rewrite).
PLAIN_TABLES = [
    "watchlist", "setup_score_history", "portfolio_snapshots",
    "implied_move_history", "actions", "score_share_log",
    "discovery_candidates", "settings",
]
# repo-root docs worth keeping as private notes (NEVER for the public repo)
NOTE_FILES = ["WORKLOG.md", "DESIGN_QUESTIONS.md", "ROADMAP.md"]


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _copy_table(src: sqlite3.Connection, dst: sqlite3.Connection,
                table: str, dry: bool) -> int:
    """Insert source rows absent from dest. Identity = all shared non-id
    columns equal (works for tables with no natural key, keeps reruns at 0)."""
    if not (_table_exists(src, table) and _table_exists(dst, table)):
        return 0
    shared = [c for c in _cols(src, table) if c in set(_cols(dst, table))
              and c != "id"]  # autoincrement ids never migrate
    if not shared:
        return 0
    col_list = ", ".join(shared)
    where = " AND ".join(f"{c} IS ?" for c in shared)  # IS handles NULLs
    added = 0
    for row in src.execute(f"SELECT {col_list} FROM {table}"):
        if dst.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1",
                       row).fetchone():
            continue
        if not dry:
            dst.execute(
                f"INSERT INTO {table} ({col_list}) VALUES "
                f"({', '.join('?' * len(shared))})", row)
        added += 1
    return added


def _copy_discussions(src: sqlite3.Connection, dst: sqlite3.Connection,
                      src_dir: str | None, dest_dir: str, dry: bool
                      ) -> tuple[int, int]:
    """Rows + files. file_path is rewritten to the destination tree and files
    are copied with mtimes intact (copy2) so file_mtime in the row still
    matches on disk — the watcher then treats them as already ingested."""
    if not _table_exists(src, "claude_discussions"):
        return 0, 0
    shared = [c for c in _cols(src, "claude_discussions")
              if c in set(_cols(dst, "claude_discussions")) and c != "id"]
    rows_added = files_copied = 0
    for row in src.execute(
            f"SELECT {', '.join(shared)} FROM claude_discussions"):
        d = dict(zip(shared, row))
        old_path = d["file_path"]
        # relative layout is {TICKER}/{file}.md — keep it
        rel = os.path.join(d.get("ticker", ""), os.path.basename(old_path))
        new_path = os.path.join(dest_dir, rel)
        d["file_path"] = new_path
        src_file = old_path
        if src_dir and not os.path.exists(src_file):
            src_file = os.path.join(src_dir, rel)  # row path stale? try tree
        if os.path.exists(src_file) and not os.path.exists(new_path):
            if not dry:
                os.makedirs(os.path.dirname(new_path), exist_ok=True)
                shutil.copy2(src_file, new_path)  # copy2 preserves mtime
            files_copied += 1
        if dst.execute("SELECT 1 FROM claude_discussions WHERE file_path=?",
                       (new_path,)).fetchone():
            continue
        if not dry:
            dst.execute(
                f"INSERT INTO claude_discussions ({', '.join(d)}) VALUES "
                f"({', '.join('?' * len(d))})", list(d.values()))
        rows_added += 1
    return rows_added, files_copied


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source-db", required=True)
    ap.add_argument("--source-discussions")
    ap.add_argument("--source-discoveries")
    ap.add_argument("--source-repo",
                    help="repo root; WORKLOG/DESIGN_QUESTIONS/ROADMAP are "
                         "copied to <dest-home>/notes/ as private notes")
    ap.add_argument("--source-token", help="schwab_token.json to copy in")
    ap.add_argument("--dest-home",
                    default=os.environ.get("TICKERLENS_HOME",
                                           os.path.expanduser("~/.tickerlens")))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    src_db = os.path.expanduser(a.source_db)
    home = os.path.expanduser(a.dest_home)
    dest_db = os.path.join(home, "tickerlens.db")
    dry = a.dry_run

    if not os.path.exists(src_db):
        print(f"[FAIL] source db not found: {src_db}")
        return 1
    os.makedirs(home, exist_ok=True)

    # destination schema (import via the app so migrations run) ------------
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    os.environ["TICKERLENS_DB_PATH"] = dest_db
    import db as dbmod
    if not dry:
        dbmod.init_db(dest_db)

    # backup before touching anything --------------------------------------
    if os.path.exists(dest_db) and not dry:
        bak = f"{dest_db}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(dest_db, bak)
        print(f"[ok] destination backed up → {bak}")

    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)  # NEVER writes
    dst = sqlite3.connect(dest_db)
    try:
        for table in PLAIN_TABLES:
            n = _copy_table(src, dst, table, dry)
            print(f"[{'dry' if dry else 'ok'}] {table:22s} +{n}")

        dest_disc = os.path.join(home, "discussions")
        rows, files = _copy_discussions(
            src, dst, os.path.expanduser(a.source_discussions or ""),
            dest_disc, dry)
        print(f"[{'dry' if dry else 'ok'}] claude_discussions     +{rows} rows, "
              f"+{files} files → {dest_disc}")

        if a.source_discoveries:
            sd = os.path.expanduser(a.source_discoveries)
            dd = os.path.join(home, "discoveries")
            n = 0
            if os.path.isdir(sd):
                for f in os.listdir(sd):
                    if f.endswith(".md") and not os.path.exists(
                            os.path.join(dd, f)):
                        if not dry:
                            os.makedirs(dd, exist_ok=True)
                            shutil.copy2(os.path.join(sd, f),
                                         os.path.join(dd, f))
                        n += 1
            print(f"[{'dry' if dry else 'ok'}] discovery sweeps       +{n} files")

        if a.source_repo:
            nd = os.path.join(home, "notes")
            n = 0
            for f in NOTE_FILES:
                sp = os.path.join(os.path.expanduser(a.source_repo), f)
                if os.path.exists(sp) and not os.path.exists(
                        os.path.join(nd, f)):
                    if not dry:
                        os.makedirs(nd, exist_ok=True)
                        shutil.copy2(sp, os.path.join(nd, f))
                    n += 1
            print(f"[{'dry' if dry else 'ok'}] private notes          +{n} → {nd}")

        if a.source_token:
            tp = os.path.expanduser(a.source_token)
            dt = os.path.join(home, "schwab_token.json")
            if os.path.exists(tp) and not os.path.exists(dt):
                if not dry:
                    shutil.copy2(tp, dt)
                    os.chmod(dt, 0o600)
                print(f"[{'dry' if dry else 'ok'}] schwab token copied → {dt}")
            elif os.path.exists(dt):
                print("[ok] schwab token already present — untouched")
            else:
                print(f"[warn] token not found at {tp} — run schwab_reauth.py")

        if not dry:
            dst.commit()
    finally:
        src.close()
        dst.close()

    print("\nNext steps:")
    print("  1. If you use Schwab: set quotes/daily_history/option_chain/"
          "account = \"schwab\" in", os.path.join(home, "config.toml"))
    print("  2. API keys resolve via keyring → OS keychain → env; nothing "
          "to move if they were already in your OS keychain.")
    print("  3. Restart the app; the watcher will pick everything up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
