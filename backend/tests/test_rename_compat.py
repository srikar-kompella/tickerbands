"""TickerLens → TickerBands rename: legacy env vars, data dir and DB move.

Every case runs in tmp_path; nothing here may touch a real home directory.
"""
import os
import sqlite3

import envcompat


def test_env_prefers_new_name(monkeypatch):
    monkeypatch.setenv("TICKERBANDS_FOO", "new")
    monkeypatch.setenv("TICKERLENS_FOO", "old")
    assert envcompat.env("FOO") == "new"


def test_env_falls_back_to_legacy_with_notice(monkeypatch, capsys):
    monkeypatch.delenv("TICKERBANDS_BAR", raising=False)
    monkeypatch.setenv("TICKERLENS_BAR", "old")
    envcompat._warned.discard("BAR")
    assert envcompat.env("BAR") == "old"
    assert envcompat.env("BAR") == "old"
    out = capsys.readouterr().out
    assert out.count("TICKERLENS_BAR is deprecated") == 1  # warned once


def test_env_default(monkeypatch):
    monkeypatch.delenv("TICKERBANDS_BAZ", raising=False)
    monkeypatch.delenv("TICKERLENS_BAZ", raising=False)
    assert envcompat.env("BAZ") is None
    assert envcompat.env("BAZ", "d") == "d"


def test_home_none(tmp_path):
    assert envcompat.migrate_legacy_home(str(tmp_path / "new"), str(tmp_path / "old")) == "none"


def test_home_migrated_keeps_data_and_leaves_symlink(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    (old / "discussions").mkdir(parents=True)
    (old / "discussions" / "x.md").write_text("keep me")
    assert envcompat.migrate_legacy_home(str(new), str(old)) == "migrated"
    assert (new / "discussions" / "x.md").read_text() == "keep me"
    assert os.path.islink(old) and (old / "discussions" / "x.md").read_text() == "keep me"
    # idempotent: second start sees the symlink and does nothing
    assert envcompat.migrate_legacy_home(str(new), str(old)) == "linked"


def test_home_both_exist_untouched(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(); new.mkdir()
    (old / "a").write_text("1"); (new / "b").write_text("2")
    assert envcompat.migrate_legacy_home(str(new), str(old)) == "both"
    assert (old / "a").exists() and (new / "b").exists() and not os.path.islink(old)


def _make_db(path):
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE t (v TEXT)")
    c.execute("INSERT INTO t VALUES ('row')")
    c.commit()
    return c  # left open so -wal/-shm exist


def test_db_migrated_with_backup_and_wal(tmp_path):
    old = tmp_path / "tickerlens.db"
    c = _make_db(str(old))
    had_wal = os.path.exists(str(old) + "-wal")
    c.close()
    assert envcompat.migrate_legacy_db(str(tmp_path)) == "migrated"
    assert not old.exists()
    new = tmp_path / "tickerbands.db"
    assert sqlite3.connect(new).execute("SELECT v FROM t").fetchone() == ("row",)
    backups = [p.name for p in tmp_path.iterdir() if ".bak-" in p.name]
    assert any(n.startswith("tickerlens.db.bak-") for n in backups)
    if had_wal:
        assert not os.path.exists(str(old) + "-wal")


def test_db_none_and_both(tmp_path):
    assert envcompat.migrate_legacy_db(str(tmp_path)) == "none"
    (tmp_path / "tickerlens.db").write_text("old")
    (tmp_path / "tickerbands.db").write_text("new")
    assert envcompat.migrate_legacy_db(str(tmp_path)) == "both"
    assert (tmp_path / "tickerlens.db").read_text() == "old"


def test_old_format_score_export_still_imports(tmp_db):
    """Bundles exported before the rename (format tickerlens-...-v1) import."""
    from fastapi.testclient import TestClient
    import app as appmod
    client = TestClient(appmod.app)
    bundle = {"format": "tickerlens-score-history-v1",
              "rows": [{"symbol": "AAPL", "ts": "2026-10-01T10:00:00",
                        "score": 61.5, "lean": "NEUTRAL", "components": "[]"}]}
    r = client.post("/api/score-history/import", json=bundle)
    assert r.status_code == 200, r.text
    exp = client.get("/api/score-history/export").json()
    assert exp["format"] == "tickerbands-score-history-v1"
    assert any(row["symbol"] == "AAPL" for row in exp["rows"])
