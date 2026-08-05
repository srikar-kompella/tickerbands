"""
tests/test_concurrency.py — step-1 speedup invariants.

1. Cache in-flight dedupe: N threads asking for the SAME cold key must produce
   exactly ONE provider call (quota protection); everyone gets the same data.
2. Parallel composer: analyze() still returns every section with a valid
   status envelope (the ThreadPool refactor must not change the contract),
   and a section-level failure still degrades to 'unavailable' alone.
"""
import threading
import time

from cache.store import get_or_fetch
from providers.base import ProviderError


def test_inflight_dedupe_single_fetch(tmp_db):
    calls = []

    def slow_fetch():
        calls.append(1)
        time.sleep(0.3)  # long enough that all threads pile onto the lock
        return {"v": 42}

    results = []
    threads = [threading.Thread(
        target=lambda: results.append(
            get_or_fetch("dedupe:TEST", 60, slow_fetch)))
        for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1, f"expected 1 provider call, got {len(calls)}"
    assert len(results) == 6
    assert all(r["data"] == {"v": 42} for r in results)
    # exactly one 'live', the rest served from the just-warmed cache
    assert sum(1 for r in results if r["source"] == "live") == 1


def test_inflight_lock_still_raises_when_no_stale(tmp_db):
    def bad_fetch():
        raise ProviderError("unavailable", "boom")
    try:
        get_or_fetch("dedupe:BAD", 60, bad_fetch)
        assert False, "should have raised"
    except ProviderError as e:
        assert e.reason == "unavailable"


def test_parallel_analyze_contract_intact(tmp_db, mock_mode):
    from analysis import composer
    payload = composer.analyze("AAPL")
    sections = payload["sections"]
    for name in ("quote", "history", "options", "news_sentiment", "headlines",
                 "fundamentals", "recommendations", "earnings", "social",
                 "insiders", "short_interest"):
        assert name in sections, f"missing section {name}"
        assert sections[name]["status"] in {"ok", "stale", "unavailable", "disabled"}
    assert payload["band"] is not None
    assert payload["setup_score"] is not None


def test_parallel_analyze_one_failure_degrades_alone(tmp_db, mock_mode, monkeypatch):
    from analysis import composer
    from providers import mock as mockmod

    def boom(symbol):
        raise ProviderError("rate_limited", "simulated")
    monkeypatch.setattr(mockmod, "fetch_social", boom)

    payload = composer.analyze("MSFT")
    s = payload["sections"]
    assert s["social"]["status"] == "unavailable"
    assert s["social"]["error_reason"] == "rate_limited"
    assert s["quote"]["status"] == "ok"          # neighbors unharmed
    assert s["history"]["status"] == "ok"


# ── completed_bars session-close semantics (the evening-lag fix) ──────────────

def test_completed_bars_includes_today_after_close():
    import datetime as dt
    from zoneinfo import ZoneInfo
    from analysis.vol_bands import completed_bars
    ny = ZoneInfo("America/New_York")
    bars = [{"date": "2026-08-03", "close": 125.65},
            {"date": "2026-08-04", "close": 162.66}]
    evening = dt.datetime(2026, 8, 4, 20, 0, tzinfo=ny)
    out = completed_bars(bars, now=evening)
    assert out[-1]["date"] == "2026-08-04", "post-close bar must count"
    morning = dt.datetime(2026, 8, 4, 10, 30, tzinfo=ny)
    out = completed_bars(bars, now=morning)
    assert out[-1]["date"] == "2026-08-03", "intraday partial bar must drop"
    just_before = dt.datetime(2026, 8, 4, 16, 14, tzinfo=ny)
    assert completed_bars(bars, now=just_before)[-1]["date"] == "2026-08-03"


def test_completed_bars_explicit_today_stays_strict():
    import datetime as dt
    from analysis.vol_bands import completed_bars
    bars = [{"date": "2026-08-03"}, {"date": "2026-08-04"}]
    out = completed_bars(bars, today=dt.date(2026, 8, 4))
    assert [b["date"] for b in out] == ["2026-08-03"]
