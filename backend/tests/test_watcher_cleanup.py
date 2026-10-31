from discussions_svc import watcher


def test_stop_cancels_pending_timers(monkeypatch):
    handler = watcher.DiscussionHandler()
    ingest_called = []

    monkeypatch.setattr(
        watcher,
        "_ingest",
        lambda path: ingest_called.append(path),
    )

    handler._schedule("test.md")
    handler.stop()

    assert ingest_called == []

def test_stop_waits_for_running_callback(monkeypatch):
    import threading

    handler = watcher.DiscussionHandler()
    started = threading.Event()
    finished = threading.Event()

    def fake_ingest(path):
        started.set()
        finished.wait(timeout=2)

    monkeypatch.setattr(watcher, "_ingest", fake_ingest)

    handler._schedule("test.md")
    assert started.wait(timeout=2)

    stop_thread = threading.Thread(target=handler.stop)
    stop_thread.start()

    assert stop_thread.is_alive()

    finished.set()
    stop_thread.join(timeout=2)

    assert not stop_thread.is_alive()

