"""
Composer + API integration tests — all in mock mode (deterministic fixtures,
zero network/Keychain). These are the snapshot-level guarantees: payload shape,
degradation behavior, endpoint contracts, and the honesty constraints
(no direction-prediction language in user-facing strings).
"""
import json

import pytest

import config
from analysis import composer
from db import connect
from tests.conftest import write_md


@pytest.fixture()
def client(tmp_db, mock_mode, tmp_discussions, monkeypatch):
    """TestClient with lifespan (watcher runs against the tmp discussions dir)."""
    import config as cfg
    monkeypatch.setattr(cfg, "DISCUSSIONS_DIR", str(tmp_discussions))
    from fastapi.testclient import TestClient
    from app import app
    with TestClient(app) as c:
        yield c


# ─── composer.analyze ──────────────────────────────────────────────────────────

def test_analyze_full_payload_shape(tmp_db, mock_mode):
    a = composer.analyze("AAPL")
    # top-level contract the frontend types depend on:
    for key in ("symbol", "quote", "signals", "band", "move", "beta", "week52",
                "band_coverage", "setup_score", "score_history", "chart",
                "sections", "context_hash", "generated_ts"):
        assert key in a, f"missing {key}"
    assert a["symbol"] == "AAPL"
    assert a["band"]["low"] < a["band"]["prev_close"] < a["band"]["high"]
    assert 0 <= a["setup_score"]["score"] <= 100
    assert len(a["chart"]) == 30
    assert a["sections"]["quote"]["status"] == "ok"
    # sections envelope must never embed payloads (that's the top-level keys' job)
    assert "data" not in a["sections"]["quote"]


def test_analyze_persists_score_history_organically(tmp_db, mock_mode):
    composer.analyze("NVDA")
    composer.analyze("NVDA")
    conn = connect()
    n = conn.execute("SELECT COUNT(*) c FROM setup_score_history WHERE symbol='NVDA'").fetchone()["c"]
    conn.close()
    assert n == 2  # Q8d: history accumulates per analysis, no backfill


def test_analyze_context_hash_stable_for_same_content(tmp_db, mock_mode):
    a1 = composer.analyze("MSFT")
    a2 = composer.analyze("MSFT")  # cache-served → identical content
    assert a1["context_hash"] == a2["context_hash"]
    assert a1["context_hash"].startswith("sha256:")


def test_analyze_no_options_ticker_reweights_score(tmp_db, mock_mode):
    a = composer.analyze("PHYS")  # mock mirrors real life: no listed options
    assert a["options"] == {"available": False}
    score = a["setup_score"]
    opt_row = next(c for c in score["components"] if c["component"] == "options_positioning")
    assert opt_row["available"] is False and opt_row["points"] == 0.0
    assert score["components_available"] < score["components_total"]  # Q8c visible


def test_analyze_band_coverage_core20_vs_other(tmp_db, mock_mode):
    core = composer.analyze("AAPL")["band_coverage"]
    other = composer.analyze("ZZZT")["band_coverage"]
    assert core["scope"] == "this_ticker" and core["days"] > 400
    assert other["scope"] == "basket_overall"  # honesty: no fake per-ticker stat


def test_analyze_touches_watchlist_timestamp(tmp_db, mock_mode):
    conn = connect()
    conn.execute("INSERT INTO watchlist (symbol, added_ts) VALUES ('TSLA', '2026-07-01T00:00:00')")
    conn.commit(); conn.close()
    composer.analyze("TSLA")
    conn = connect()
    ts = conn.execute("SELECT last_analyzed_ts FROM watchlist WHERE symbol='TSLA'").fetchone()[0]
    conn.close()
    assert ts is not None


def test_feature_flag_disables_section(tmp_db, mock_mode, monkeypatch):
    monkeypatch.setitem(config.FEATURES, "social", False)
    a = composer.analyze("META")
    assert a["sections"]["social"]["status"] == "disabled"
    assert a["social"] is None


# ─── API endpoints ─────────────────────────────────────────────────────────────

def test_api_analysis_ok(client):
    r = client.get("/api/analysis/aapl")  # lowercase in → uppercase out
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "AAPL"
    assert body["band"]["coverage_target"] == 0.80


def test_api_watchlist_crud_cycle(client):
    assert client.get("/api/watchlist").json() == []
    r = client.post("/api/watchlist", json={"symbol": "hims", "note": "sentiment name",
                                            "tags": ["research", "volatile"]})
    assert r.status_code == 201 and r.json()["symbol"] == "HIMS"
    lst = client.get("/api/watchlist").json()
    assert len(lst) == 1 and lst[0]["tags"] == ["research", "volatile"]
    r = client.patch("/api/watchlist/HIMS", json={"note": "updated"})
    assert r.status_code == 200
    assert client.get("/api/watchlist").json()[0]["note"] == "updated"
    assert client.delete("/api/watchlist/HIMS").status_code == 200
    assert client.delete("/api/watchlist/HIMS").status_code == 404  # gone is gone


def test_api_watchlist_validation(client):
    assert client.post("/api/watchlist", json={"symbol": ""}).status_code == 422
    assert client.patch("/api/watchlist/NOPE", json={"note": "x"}).status_code == 404


def test_api_settings_roundtrip_and_validation(client):
    s = client.get("/api/settings").json()
    assert s["weights"] == config.DEFAULT_WEIGHTS
    assert "does not predict" in s["disclaimer"]

    new = {**config.DEFAULT_WEIGHTS, "momentum": 50.0}
    assert client.put("/api/settings/weights", json={"weights": new}).status_code == 200
    assert client.get("/api/settings").json()["weights"]["momentum"] == 50.0

    bad_unknown = client.put("/api/settings/weights",
                             json={"weights": {"vibes": 10}})
    assert bad_unknown.status_code == 422
    bad_negative = client.put("/api/settings/weights",
                              json={"weights": {**config.DEFAULT_WEIGHTS, "momentum": -5}})
    assert bad_negative.status_code == 422
    bad_all_zero = client.put("/api/settings/weights",
                              json={"weights": {k: 0 for k in config.DEFAULT_WEIGHTS}})
    assert bad_all_zero.status_code == 422


def test_api_weights_change_alters_score(client):
    base = client.get("/api/analysis/GOOGL").json()["setup_score"]["score"]
    # crank momentum to everything, then force-refresh compute
    client.put("/api/settings/weights",
               json={"weights": {"momentum": 100, "news_sentiment": 0,
                                 "options_positioning": 0, "analyst_trend": 0,
                                 "social_buzz": 0}})
    momentum_only = client.get("/api/analysis/GOOGL").json()["setup_score"]["score"]
    assert momentum_only != base  # weights are live, not decorative


def test_api_glossary_shape(client):
    g = client.get("/api/glossary").json()
    assert "expected_range" in g["entries"]
    assert g["features"]["setup_score"] is True
    # glossary honesty: the range entry must not promise direction
    assert "direction" in g["entries"]["expected_range"]["why"].lower()


def test_api_prompt_contract(client):
    r = client.get("/api/analysis/AAPL/prompt")
    assert r.status_code == 200
    p = r.json()["prompt"]
    # the watcher's parser depends on these exact instructions:
    import config as _cfg
    import os as _os
    assert _os.path.join(_cfg.DISCUSSIONS_DIR, "AAPL") in p
    assert "ticker: AAPL" in p and "context_hash:" in p and "summary:" in p
    # honesty constraint baked into the prompt itself:
    assert "do NOT predict price direction" in p
    # writing contract: plain-English, structured, no context echo
    assert "NO finance background" in p
    assert "## TL;DR" in p
    assert "Do NOT paste this prompt" in p
    assert r.json()["context_hash"].startswith("sha256:")


def test_api_prompt_embeds_prior_discussions(client, tmp_discussions):
    """Continuity (static prompt, fresh knowledge): prior summaries ride along."""
    from discussions_svc.watcher import catch_up_scan
    write_md(tmp_discussions, "AAPL", "2026-07-10T09:00:00.md",
             ["ticker: AAPL", "created_ts: 2026-07-10T09:00:00",
              "summary: Decided range is fair; wait for earnings", "tags: [decision]"])
    assert catch_up_scan() >= 1
    p = client.get("/api/analysis/AAPL/prompt").json()["prompt"]
    assert "Prior discussions on this ticker" in p
    assert "Decided range is fair; wait for earnings" in p
    # and a fresh ticker has no phantom section:
    p2 = client.get("/api/analysis/XOM/prompt").json()["prompt"]
    assert "Prior discussions" not in p2


def test_api_symbol_search(client):
    hits = client.get("/api/search", params={"q": "apple"}).json()
    assert any(h["symbol"] == "AAPL" for h in hits)
    hits2 = client.get("/api/search", params={"q": "sprott"}).json()
    assert any(h["symbol"] == "PHYS" for h in hits2)
    # too-short query is a validation error, not a 500
    assert client.get("/api/search", params={"q": "a"}).status_code == 422


def test_api_discussions_list_after_manual_upsert(client, tmp_discussions):
    # write a file and ingest synchronously via catch-up (deterministic, no
    # watcher-timing flakiness in tests):
    from discussions_svc.watcher import catch_up_scan
    write_md(tmp_discussions, "AAPL", "2026-07-12T10:00:00.md",
             ["ticker: AAPL", "summary: test take", "tags: [t1]"])
    assert catch_up_scan() >= 1
    rows = client.get("/api/discussions", params={"ticker": "AAPL"}).json()
    assert len(rows) == 1 and rows[0]["summary"] == "test take"
    assert client.get("/api/discussions").json()  # unified timeline too


def test_api_sync_invalidates_cache(client):
    client.get("/api/analysis/XOM")            # populate cache
    r = client.post("/api/sync").json()
    assert r["ok"] is True and r["invalidated"] > 0


def test_api_health(client):
    h = client.get("/api/health").json()
    assert h["ok"] is True and h["mock_mode"] is True
    assert {"schwab", "finnhub", "stocktwits", "edgar", "finra", "yfinance"} <= set(h["providers"])


# ─── watcher unit pieces (no Observer — deterministic) ─────────────────────────

def test_watcher_ingest_and_quarantine(tmp_db, tmp_discussions):
    from discussions_svc.watcher import _ingest, _is_discussion_md
    from discussions_svc.parser import list_discussions

    good = write_md(tmp_discussions, "NVDA", "good.md",
                    ["ticker: NVDA", "summary: fine"])
    _ingest(good)
    assert len(list_discussions("NVDA")) == 1

    # malformed → moved to _failed/, no DB row, no exception
    bad_dir = tmp_discussions / "TSLA"
    bad_dir.mkdir()
    bad = bad_dir / "bad.md"
    bad.write_text("---\nticker: TSLA\n---\n\n")  # empty body
    _ingest(str(bad))
    assert len(list_discussions("TSLA")) == 0
    assert not bad.exists()
    assert (tmp_discussions / "_failed" / "bad.md").exists()

    # filter: _failed content and non-md must be ignored by the handler
    assert _is_discussion_md(str(tmp_discussions / "AAPL" / "x.md"))
    assert not _is_discussion_md(str(tmp_discussions / "_failed" / "x.md"))
    assert not _is_discussion_md(str(tmp_discussions / "AAPL" / "x.txt"))
    assert not _is_discussion_md(str(tmp_discussions / "AAPL" / ".hidden.md"))


def test_no_direction_prediction_language_anywhere(client):
    """The non-goal, enforced: user-facing strings must not claim direction
    prediction. Scans glossary + disclaimer + prompt for banned phrasing."""
    banned = ["will go up", "will go down", "predicts direction",
              "buy signal confidence", "% confidence it rises"]
    corpus = json.dumps(client.get("/api/glossary").json()).lower()
    corpus += client.get("/api/settings").json()["disclaimer"].lower()
    corpus += client.get("/api/analysis/AAPL/prompt").json()["prompt"].lower()
    for phrase in banned:
        assert phrase not in corpus, f"banned phrase present: {phrase}"
