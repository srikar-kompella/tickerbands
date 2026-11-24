"""The live update of a research note must not depend on the file-system
observer alone: a dropped event (sleeping laptop, synced folder, an editor's
save style) left a finished note on disk while the app showed the old one."""
import os

import pytest

from tests.conftest import write_md


@pytest.fixture()
def w(tmp_db, tmp_discussions, monkeypatch):
    from discussions_svc import watcher
    monkeypatch.setattr(watcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(watcher, "_wait_until_stable", lambda p: True)
    monkeypatch.setattr(watcher, "_SEEN", {})
    events = []
    monkeypatch.setattr(watcher.broadcaster, "publish_threadsafe", events.append)
    watcher.events = events
    return watcher


def _old(path, seconds=60):
    t = os.path.getmtime(path) - seconds
    os.utime(path, (t, t))


def test_sweep_ingests_a_note_the_observer_never_reported(w, tmp_discussions):
    from discussions_svc.parser import list_discussions
    path = write_md(tmp_discussions, "ZZZT", "2031-02-03T10:15:00.md",
                    ["ticker: ZZZT", "summary: first"])
    _old(path)
    assert w.sweep_once() == [path]
    assert [d["summary"] for d in list_discussions("ZZZT")] == ["first"]
    assert w.events and w.events[-1]["type"] == "created" and w.events[-1]["ticker"] == "ZZZT"
    # nothing changed → nothing re-ingested, no second event
    n = len(w.events)
    assert w.sweep_once() == [] and len(w.events) == n


def test_sweep_picks_up_an_edit_and_skips_a_file_still_being_written(w, tmp_discussions):
    from discussions_svc.parser import list_discussions
    path = write_md(tmp_discussions, "ZZZT", "2031-02-03T10:15:00.md",
                    ["ticker: ZZZT", "summary: first"])
    _old(path, 120)
    w.sweep_once()
    # the session revises its note; the observer misses the change
    write_md(tmp_discussions, "ZZZT", "2031-02-03T10:15:00.md",
             ["ticker: ZZZT", "summary: revised"])
    assert w.sweep_once() == []                    # written seconds ago: left to the debounce
    _old(path, 30)
    assert w.sweep_once() == [path]
    assert [d["summary"] for d in list_discussions("ZZZT")] == ["revised"]


def test_notes_ingested_by_the_observer_are_not_ingested_again(w, tmp_discussions):
    path = write_md(tmp_discussions, "ZZZT", "2031-02-03T10:15:00.md",
                    ["ticker: ZZZT", "summary: first"])
    _old(path)
    w._ingest(path)                                # the observer's own path
    n = len(w.events)
    assert w.sweep_once() == [] and len(w.events) == n


def test_startup_priming_and_a_removed_file(w, tmp_discussions):
    from discussions_svc.parser import list_discussions
    path = write_md(tmp_discussions, "ZZZT", "2031-02-03T10:15:00.md",
                    ["ticker: ZZZT", "summary: first"])
    _old(path)
    w.catch_up_scan()
    w.prime_seen()
    assert w.sweep_once() == []                    # startup already reconciled it
    os.remove(path)
    w.sweep_once()
    assert list_discussions("ZZZT") == []
    assert w.events[-1]["type"] == "deleted" and w.events[-1]["ticker"] == "ZZZT"
