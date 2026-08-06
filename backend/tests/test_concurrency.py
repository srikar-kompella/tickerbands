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


# ── stance drift materiality (replaces checksum staleness) ────────────────────

def test_stance_drift_same_day_not_material():
    """A stance recorded after today's close shows ~0 drift — the checksum
    approach wrongly flagged it 'stale' the moment the band recentered."""
    from analysis.composer import _stance_with_drift
    bars = [{"date": "2026-08-03", "close": 125.65},
            {"date": "2026-08-04", "close": 162.66}]
    band = {"half_width_pct": 3.77}
    st = _stance_with_drift({"stance": "hold", "created_ts": "2026-08-04T23:51:04"},
                            bars, band)
    assert st["drift_pct"] == 0.0
    assert st["drift_material"] is False


def test_stance_drift_material_when_beyond_band():
    from analysis.composer import _stance_with_drift
    bars = [{"date": "2026-07-19", "close": 100.0},
            {"date": "2026-08-04", "close": 120.0}]
    band = {"half_width_pct": 2.0}
    st = _stance_with_drift({"stance": "hold", "created_ts": "2026-07-19T10:00:00"},
                            bars, band)
    # 20% move vs 2%×√11 ≈ 6.6% expectation → clearly material
    assert st["drift_pct"] == 20.0
    assert st["drift_material"] is True


def test_stance_drift_small_move_stays_quiet():
    from analysis.composer import _stance_with_drift
    bars = [{"date": "2026-07-28", "close": 100.0},
            {"date": "2026-08-04", "close": 101.5}]
    band = {"half_width_pct": 2.0}
    st = _stance_with_drift({"stance": "buy", "created_ts": "2026-07-28T10:00:00"},
                            bars, band)
    assert st["drift_material"] is False  # 1.5% < 2%×√5


def test_stance_drift_none_passthrough():
    from analysis.composer import _stance_with_drift
    assert _stance_with_drift(None, [], None) is None


# ── daily-candle normalization (duplicate-date crash regression) ──────────────

def test_normalize_daily_collapses_duplicate_dates():
    """Two Schwab candles mapping to the same date must collapse to ONE bar
    (the later emission wins) — duplicates crashed the price chart."""
    from providers.schwab import _normalize_daily
    day_ms = 86_400_000
    aug4 = 1_785_801_600_000  # 2026-08-04 00:00 UTC
    candles = [
        {"datetime": aug4 - day_ms, "close": 125.65, "open": 1, "high": 1, "low": 1, "volume": 10},
        {"datetime": aug4, "close": 160.00, "open": 1, "high": 1, "low": 1, "volume": 10},
        {"datetime": aug4 + 3_600_000, "close": 162.66, "open": 1, "high": 1, "low": 1, "volume": 20},
    ]
    out = _normalize_daily(candles, days=10)
    dates = [b["date"] for b in out]
    assert dates == sorted(set(dates)), "dates must be strictly ascending + unique"
    assert out[-1]["date"] == "2026-08-04"
    assert out[-1]["close"] == 162.66, "later candle for the same date wins"


def test_normalize_daily_sorts_out_of_order_input():
    from providers.schwab import _normalize_daily
    candles = [
        {"datetime": 1_785_801_600_000, "close": 2.0, "open": 0, "high": 0, "low": 0, "volume": 0},
        {"datetime": 1_785_715_200_000, "close": 1.0, "open": 0, "high": 0, "low": 0, "volume": 0},
    ]
    out = _normalize_daily(candles, days=10)
    assert [b["close"] for b in out] == [1.0, 2.0]


def test_analyze_chart_dates_strictly_ascending(tmp_db, mock_mode):
    """Contract the chart depends on, enforced end-to-end."""
    from analysis import composer
    chart = composer.analyze("NVDA")["chart"]
    dates = [p["date"] for p in chart]
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates))


# ── Discover digest export ────────────────────────────────────────────────────

def test_discover_report_html_lists_candidates(tmp_db):
    from analysis.discovery import upsert_candidate, list_candidates
    from api.report import build_discover_html
    upsert_candidate("ERII", "insider_cluster", "2 insiders bought within 14d",
                     market_cap_m=1200)
    upsert_candidate("ERII", "short_interest", "SI fell 22% period-over-period")
    html = build_discover_html(list_candidates())
    assert "ERII" in html and "$1.2B" in html
    assert "convergence" in html          # ≥2 sources badge
    assert "not" in html.lower() and "recommendation" in html.lower()
    assert "TickerLens_Discover_" in html  # save-as-PDF filename via <title>


def test_discover_report_route_empty_ok(tmp_db, mock_mode, tmp_discussions, monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "DISCUSSIONS_DIR", str(tmp_discussions))
    from fastapi.testclient import TestClient
    from app import app
    with TestClient(app) as client:
        r = client.get("/api/discovery/report")
        assert r.status_code == 200
        assert "No active candidates" in r.text


# ── secret-store stall protection ─────────────────────────────────────────────

def test_hanging_secret_store_cannot_stall_requests(monkeypatch):
    """A blocked OS keychain (hidden permission dialog) must degrade to env
    within the timeout — and disable itself for the rest of the process."""
    import sys
    import time as _time
    from providers import base

    class HangingKeyring:
        @staticmethod
        def get_password(service, name):
            _time.sleep(30)  # simulates a blocking permission dialog

    monkeypatch.setitem(sys.modules, "keyring", HangingKeyring())
    monkeypatch.setattr(base, "from_keychain", lambda n: None)
    monkeypatch.delenv("TICKERLENS_NO_KEYRING", raising=False)
    monkeypatch.setenv("STALL_ENV_KEY", "from-env")
    monkeypatch.setattr(base, "_STORE_BLOCKED", False)
    monkeypatch.setattr(base, "_STORE_TIMEOUT_S", 0.3)

    t0 = _time.monotonic()
    assert base.get_secret("stall_key", "STALL_ENV_KEY") == "from-env"
    assert _time.monotonic() - t0 < 2.0, "must not wait on the store"
    # second call: layer disabled, instant
    t0 = _time.monotonic()
    assert base.get_secret("stall_key2", "STALL_ENV_KEY") == "from-env"
    assert _time.monotonic() - t0 < 0.1


def test_secret_cache_hits_store_once(monkeypatch):
    import sys
    from providers import base
    calls = []

    class CountingKeyring:
        @staticmethod
        def get_password(service, name):
            calls.append(name)
            return "sekret"

    monkeypatch.setitem(sys.modules, "keyring", CountingKeyring())
    monkeypatch.delenv("TICKERLENS_NO_KEYRING", raising=False)
    monkeypatch.setattr(base, "_STORE_BLOCKED", False)
    base._SECRET_CACHE.pop("cache_key", None)
    assert base.get_secret("cache_key", "X") == "sekret"
    assert base.get_secret("cache_key", "X") == "sekret"
    assert calls == ["cache_key"], "keychain must be consulted exactly once"
    base._SECRET_CACHE.pop("cache_key", None)
