"""
tests/conftest.py — shared fixtures.

Isolation strategy: every test gets a throwaway SQLite file (monkeypatched
into the db module) and, where relevant, TICKERLENS_MOCK=1 so no test ever
touches the network, the Keychain, or the real portfolio.db.
"""
import os
import sys

# make `import db`, `import config`, ... resolve when pytest runs from anywhere
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

# Tests assert against empty watchlists; the production startup seed
# (seed_watchlist.json) is opt-out via this env var. test_seed.py opts back in.
os.environ.setdefault("TICKERLENS_SKIP_SEED", "1")
os.environ.setdefault("TICKERLENS_NO_AUTOREFRESH", "1")

import db as dbmod  # noqa: E402


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Fresh schema in a temp file; all modules go through db.connect()."""
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(dbmod, "DB_PATH", path)
    dbmod.init_db(path)
    return path


@pytest.fixture()
def mock_mode(monkeypatch):
    monkeypatch.setenv("TICKERLENS_MOCK", "1")


@pytest.fixture()
def tmp_discussions(tmp_path, monkeypatch):
    """Point the watcher/scanner at a temp discussions/ tree."""
    import discussions_svc.watcher as w
    d = tmp_path / "discussions"
    d.mkdir()
    monkeypatch.setattr(w, "DISCUSSIONS_DIR", str(d))
    monkeypatch.setattr(w, "DISCUSSIONS_FAILED_DIR", str(d / "_failed"))
    return d


def write_md(dirpath, ticker, name, frontmatter_lines, body="# Analysis\n\nSolid."):
    """Helper: compose a discussion file the way Claude CLI would."""
    folder = dirpath / ticker
    folder.mkdir(exist_ok=True)
    path = folder / name
    fm = "\n".join(frontmatter_lines)
    path.write_text(f"---\n{fm}\n---\n{body}\n")
    return str(path)
