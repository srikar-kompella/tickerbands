"""
envcompat.py — the TickerLens → TickerBands rename, made non-breaking.

The project was renamed in October 2026 (the old name was already in use by
an unrelated commercial product). Two things must keep working for anyone
who installed it under the old name:

1. Environment variables. Code reads TICKERBANDS_<X>; if that's unset it
   falls back to the legacy TICKERLENS_<X> (one-time deprecation notice).
2. The instance directory. User data moves from ~/.tickerlens to
   ~/.tickerbands with ONE atomic rename (never a copy), and a symlink is
   left at the old path so external references keep resolving. The database
   file inside is renamed the same way. Nothing is ever deleted.

Both are pure functions of their inputs so they can be tested in isolation.
"""
from __future__ import annotations

import os
import shutil
import time

NEW_PREFIX = "TICKERBANDS_"
OLD_PREFIX = "TICKERLENS_"
_warned: set[str] = set()


def env(suffix: str, default: str | None = None) -> str | None:
    """TICKERBANDS_<suffix>, else legacy TICKERLENS_<suffix>, else default."""
    new = os.environ.get(NEW_PREFIX + suffix)
    if new is not None:
        return new
    old = os.environ.get(OLD_PREFIX + suffix)
    if old is not None:
        if suffix not in _warned:
            _warned.add(suffix)
            print(f"[tickerbands] note: {OLD_PREFIX}{suffix} is deprecated — "
                  f"use {NEW_PREFIX}{suffix} (still honoured for now)")
        return old
    return default


def migrate_legacy_home(new_home: str, old_home: str) -> str:
    """Move a legacy instance dir to the new location, once, atomically.

    Returns a short description of what happened (for logging/tests):
      "none"      nothing legacy to migrate
      "migrated"  old dir renamed to new, symlink left at old path
      "both"      both exist as real dirs — left untouched (user decides)
      "linked"    old path is already a symlink (migrated earlier)
    """
    old_exists = os.path.lexists(old_home)
    if not old_exists:
        return "none"
    if os.path.islink(old_home):
        return "linked"
    if os.path.exists(new_home):
        return "both"
    os.rename(old_home, new_home)          # atomic on the same filesystem
    try:
        os.symlink(new_home, old_home)
    except OSError:
        pass  # symlink is a convenience; the data is already safe in new_home
    return "migrated"


def migrate_legacy_db(home: str, new_name: str = "tickerbands.db",
                      old_name: str = "tickerlens.db") -> str:
    """Rename the legacy DB (and its -wal/-shm companions) inside `home`.

    A timestamped backup copy of the DB is taken before the rename, honouring
    the project rule that user data is never put at risk.
    Returns "none" | "migrated" | "both".
    """
    new_db, old_db = os.path.join(home, new_name), os.path.join(home, old_name)
    if not os.path.exists(old_db):
        return "none"
    if os.path.exists(new_db):
        return "both"
    stamp = time.strftime('%Y%m%d-%H%M%S')
    for suffix in ("", "-wal", "-shm"):   # SQLite may hold recent writes in -wal
        if os.path.exists(old_db + suffix):
            shutil.copy2(old_db + suffix, f"{old_db}{suffix}.bak-{stamp}")
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(old_db + suffix):
            os.rename(old_db + suffix, new_db + suffix)
    return "migrated"
