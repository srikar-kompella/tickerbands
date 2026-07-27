"""
Round-4 (roadmap 1–8,10) tests: band engines, earnings moves, PEAD, EDGAR
parsing, FINRA shapes, portfolio risk math, score audit, morning sheet.
"""
import datetime as dt
import json

import pytest

from analysis.earnings_moves import (earnings_day_moves, expected_move_summary,
                                     pead_flag)
from analysis.portfolio_risk import portfolio_risk
from analysis.vol_bands import band_with_engine, conformal_halfwidth, ewma_vol
from providers.edgar import _parse_form4
from tests.conftest import write_md  # noqa: F401  (fixture helpers)


@pytest.fixture()
def client(tmp_db, mock_mode, tmp_discussions, monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "DISCUSSIONS_DIR", str(tmp_discussions))
    from fastapi.testclient import TestClient
    from app import app
    with TestClient(app) as c:
        yield c


# ─── band engines ──────────────────────────────────────────────────────────────

def _walk(n=400, vol=0.01, seed=3):
    import random
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(n):
        closes.append(closes[-1] * (1 + rng.gauss(0.0003, vol)))
    return closes


def test_ewma_reacts_faster_than_flat_after_regime_shift():
    from analysis.vol_bands import realized_vol
    calm = _walk(300, vol=0.005)
    shocked = calm + [calm[-1] * (1 + s) for s in
                      (0.04, -0.05, 0.045, -0.04, 0.05, -0.045)]
    # after 6 wild days, EWMA vol should exceed the flat-20d estimate's jump
    assert ewma_vol(shocked) > ewma_vol(calm)
    assert ewma_vol(shocked) > realized_vol(shocked, 20) * 0.8  # same ballpark
    # and both engines still produce sane annualized numbers
    assert 0 < ewma_vol(shocked) < 200


def test_conformal_quantile_known_distribution():
    # alternating ±1% and ±3% moves → 80th pct of |moves| = 3%
    closes = [100.0]
    pattern = [0.01, -0.01, 0.01, -0.01, 0.03, -0.03, 0.01, -0.01, 0.01, -0.01]
    for i in range(300):
        closes.append(closes[-1] * (1 + pattern[i % 10]))
    half = conformal_halfwidth(closes, window=250, q=0.80)
    assert half == pytest.approx(3.0, rel=0.05)


def test_band_with_engine_fallbacks():
    short = _walk(60)
    assert band_with_engine(short[-1], short, "conformal")["engine"] == "flat20"  # short hist
    assert band_with_engine(short[-1], short, "ewma")["engine"] == "ewma"
    assert band_with_engine(short[-1], short, "flat20")["engine"] == "flat20"
    long = _walk(300)
    b = band_with_engine(long[-1], long, "conformal")
    assert b["engine"] == "conformal" and b["low"] < long[-1] < b["high"]


def test_band_engine_endpoint_validation(client):
    assert client.put("/api/settings/band-engine", json={"engine": "vibes"}).status_code == 422
    assert client.put("/api/settings/band-engine", json={"engine": "ewma"}).json()["ok"]
    assert client.get("/api/analysis/AAPL").json()["band"]["engine"] == "ewma"
    v = client.get("/api/settings/band-validation").json()
    assert {"flat20", "ewma", "conformal"} <= set(v["overall"])


# ─── earnings moves + PEAD (pure) ──────────────────────────────────────────────

def _bars(dates_closes):
    return [{"date": d, "close": c, "volume": 1} for d, c in dates_closes]


def test_earnings_day_moves_bmo_vs_amc():
    bars = _bars([("2026-07-01", 100), ("2026-07-02", 110), ("2026-07-03", 99)])
    bmo = earnings_day_moves(bars, [{"date": "2026-07-02", "hour": "bmo"}])
    amc = earnings_day_moves(bars, [{"date": "2026-07-02", "hour": "amc"}])
    unk = earnings_day_moves(bars, [{"date": "2026-07-02", "hour": ""}])
    assert bmo[0]["move_pct"] == pytest.approx(10.0)     # that session
    assert amc[0]["move_pct"] == pytest.approx(-10.0)    # next session
    assert abs(unk[0]["move_pct"]) == pytest.approx(10.0) and unk[0]["timing_ambiguous"]


def test_expected_move_summary_ratio():
    hist = [{"date": "x", "move_pct": m, "timing_ambiguous": False}
            for m in (4.0, -2.0, 3.0, -5.0)]
    s = expected_move_summary(7.0, hist)
    assert s["hist_median_abs_pct"] == 3.5
    assert s["implied_vs_hist"] == pytest.approx(2.0)
    assert s["quarters"] == 4
    assert expected_move_summary(None, []) is None


def test_pead_flag_windows():
    today = dt.date(2026, 7, 15)
    bars = _bars([((dt.date(2026, 5, 1) + dt.timedelta(days=i)).isoformat(), 100 + i)
                  for i in range(70)])
    ev = [{"date": "2026-06-20"}]
    sur = [{"surprise_pct": 12.0}]
    flag = pead_flag(ev, sur, bars, today)
    assert flag and flag["active"] and flag["direction"] == "positive"
    # small surprise → no flag; old report → no flag
    assert pead_flag(ev, [{"surprise_pct": 2.0}], bars, today) is None
    assert pead_flag([{"date": "2026-01-05"}], sur, bars, today) is None


# ─── EDGAR Form 4 parsing (fixture XML — no network) ───────────────────────────

FORM4 = """<?xml version="1.0"?><ownershipDocument>
<reportingOwner><reportingOwnerId><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><officerTitle>CEO</officerTitle></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable>
<nonDerivativeTransaction>
 <transactionDate><value>2026-07-10</value></transactionDate>
 <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
 <transactionAmounts><transactionShares><value>5000</value></transactionShares>
 <transactionPricePerShare><value>21.50</value></transactionPricePerShare></transactionAmounts>
</nonDerivativeTransaction>
<nonDerivativeTransaction>
 <transactionDate><value>2026-07-09</value></transactionDate>
 <transactionCoding><transactionCode>A</transactionCode></transactionCoding>
 <transactionAmounts><transactionShares><value>99999</value></transactionShares>
 <transactionPricePerShare><value>0</value></transactionPricePerShare></transactionAmounts>
</nonDerivativeTransaction>
</nonDerivativeTable></ownershipDocument>"""


def test_form4_parse_keeps_open_market_only():
    txs = _parse_form4(FORM4)
    assert len(txs) == 1                      # the A (award) row is dropped
    t = txs[0]
    assert t["code"] == "P" and t["owner"] == "Doe Jane" and t["title"] == "CEO"
    assert t["value"] == pytest.approx(5000 * 21.5)
    assert _parse_form4("not xml at all") == []


# ─── portfolio risk math (pure) ────────────────────────────────────────────────

def _series(seed, n=80, vol=0.01, drift=0.0):
    import random
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(n):
        closes.append(closes[-1] * (1 + rng.gauss(drift, vol)))
    return closes


def test_portfolio_risk_perfectly_correlated_has_ratio_one():
    a = _series(1)
    positions = [{"symbol": "X", "market_value": 5000.0},
                 {"symbol": "Y", "market_value": 5000.0}]
    r = portfolio_risk(positions, {"X": a, "Y": list(a)}, cash=0.0, spy_closes=a)
    assert r["available"]
    assert r["diversification_ratio"] == pytest.approx(1.0, abs=0.01)  # no benefit
    assert r["hhi"] == pytest.approx(0.5, abs=0.01)
    assert r["effective_positions"] == pytest.approx(2.0, abs=0.1)
    assert r["portfolio_beta"] == pytest.approx(1.0, abs=0.05)


def test_portfolio_risk_uncorrelated_diversifies_and_cash_scales():
    positions = [{"symbol": "X", "market_value": 5000.0},
                 {"symbol": "Y", "market_value": 5000.0}]
    closes = {"X": _series(2), "Y": _series(9)}
    r = portfolio_risk(positions, closes, cash=0.0, spy_closes=None)
    assert r["diversification_ratio"] > 1.15   # independent walks → real benefit
    r_cash = portfolio_risk(positions, closes, cash=10000.0, spy_closes=None)
    # same holdings + 50% cash → half the dollar band on double the base
    assert r_cash["band_dollars"] == pytest.approx(r["band_dollars"], rel=0.05)
    assert r_cash["total_value"] == pytest.approx(2 * r["total_value"], rel=0.01)


def test_portfolio_risk_excludes_short_history():
    positions = [{"symbol": "X", "market_value": 5000.0},
                 {"symbol": "NEW", "market_value": 1000.0}]
    r = portfolio_risk(positions, {"X": _series(3), "NEW": [10.0] * 5},
                       cash=0.0, spy_closes=None)
    assert r["available"] and r["excluded"] == ["NEW"]


# ─── score audit ───────────────────────────────────────────────────────────────

def test_score_audit_joins_and_dedupes(client, tmp_db):
    from db import connect
    client.get("/api/analysis/AAPL")  # caches history for the join
    conn = connect()
    bars = json.loads(conn.execute(
        "SELECT payload FROM cache WHERE key='history:AAPL'").fetchone()["payload"])
    old_day = bars[-30]["date"]
    # two scores same day (dedupe → keep latest) + one recent (pending)
    conn.execute("DELETE FROM setup_score_history")
    for ts, score in ((f"{old_day}T09:00:00", 20.0), (f"{old_day}T15:00:00", 80.0),
                      (f"{bars[-1]['date']}T10:00:00", 50.0)):
        conn.execute("INSERT INTO setup_score_history (symbol, ts, score, lean, components) "
                     "VALUES ('AAPL', ?, ?, 'NEUTRAL', '[]')", (ts, score))
    conn.commit(); conn.close()
    audit = client.get("/api/score-audit").json()
    assert audit["n_scored_days"] == 1            # old day only; recent is pending
    assert audit["samples"][0]["score"] == 80.0   # the day's LATEST score won
    assert audit["samples"][0]["fwd5_pct"] is not None
    assert audit["sufficient"] is False


# ─── composer payload + morning sheet ──────────────────────────────────────────

def test_analysis_payload_has_round4_sections(client):
    a = client.get("/api/analysis/HIMS").json()
    assert a["insiders"]["available"] and isinstance(a["insiders"]["cluster_buy"], bool)
    assert a["short_interest"]["available"] and a["short_interest"]["pct_of_shares_out"] is not None
    assert a["earnings_move"]["quarters"] > 0
    assert a["band"]["engine"] in ("flat20", "ewma", "conformal")


def test_stance_flow_end_to_end(client, tmp_db, tmp_discussions):
    """Research stance: frontmatter → payload chip + screener column + staleness."""
    from discussions_svc.watcher import catch_up_scan
    from tests.conftest import write_md as wmd
    wmd(tmp_discussions, "AAPL", "s1.md", [
        "ticker: AAPL", "created_ts: 2026-07-14T10:00:00",
        "summary: full review", "stance: Buy", "stance_horizon: 3-6mo",
        "stance_note: valuation reasonable, cluster insider buying",
        "context_hash: sha256:oldsnapshot",
    ])
    assert catch_up_scan() >= 1
    a = client.get("/api/analysis/AAPL").json()
    st = a["claude_stance"]
    assert st["stance"] == "buy" and st["stance_horizon"] == "3-6mo"
    assert st["context_hash"] == "sha256:oldsnapshot" != a["context_hash"]  # → stale chip
    # screener column
    client.post("/api/watchlist", json={"symbol": "AAPL"})
    row = client.get("/api/watchlist/screener").json()[0]
    assert row["stance"] == "buy" and row["stance_ts"].startswith("2026-07-14")
    # report carries it
    assert "Claude stance" in client.get("/api/analysis/AAPL/report").text
    # invalid stance dropped, absent stance → null payload
    from discussions_svc.parser import parse_discussion_file
    p = wmd(tmp_discussions, "XOM", "s2.md", ["ticker: XOM", "stance: yolo-long"])
    assert parse_discussion_file(p)["stance"] is None
    assert client.get("/api/analysis/XOM").json()["claude_stance"] is None


def test_prompt_v5_stance_contract(client):
    p = client.get("/api/analysis/AAPL/prompt").json()["prompt"]
    assert "stance:" in p and "stance_horizon:" in p and "stance_note:" in p
    assert "honest 'no stance' beats a forced one" in p
    assert "NOT a short-term direction call" in p


# ─── premium lens: breach math + composition + endpoints ───────────────────────

def test_breach_probs_known_distribution():
    from analysis.breach import breach_prob_above, breach_prob_below, trading_days
    closes = [100.0]
    pattern = [0.02, -0.02, 0.02, -0.02, 0.06, -0.05, 0.02, -0.02, 0.02, -0.02]
    for i in range(600):
        closes.append(closes[-1] * (1 + pattern[i % 10]))
    # 1-day: exactly 10% of moves exceed +5%
    assert breach_prob_above(closes, 1, 0.05) == pytest.approx(0.10, abs=0.02)
    assert breach_prob_below(closes, 1, -0.04) == pytest.approx(0.10, abs=0.02)
    # complementarity-ish sanity + thin history → None
    assert breach_prob_above(closes, 1, -0.99) == pytest.approx(1.0, abs=0.01)
    assert breach_prob_above(closes[:100], 5, 0.02) is None
    assert trading_days(35) == 24 and trading_days(7) == 5


def test_lens_endpoint_edges_and_flags(client):
    lens = client.get("/api/lens/HIMS").json()
    assert lens["available"] and lens["calls"] and lens["puts"]
    for r in lens["calls"]:
        assert r["strike"] > lens["spot"]          # OTM only for covered calls
        assert r["dte"] <= 35                      # validation boundary enforced
        assert r["oi"] >= 50 and r["spread_pct"] <= 12
        assert -100 <= r["edge_pp"] <= 100
    for r in lens["puts"]:
        assert r["strike"] < lens["spot"]
    # mock market overpays tails → top-ranked edge should be positive
    assert lens["calls"][0]["edge_pp"] > 0
    assert "REJECTED" in lens["caveats"]["validation"]["45d"]
    # no options → clean unavailable, not an error
    assert client.get("/api/lens/PHYS").json()["available"] is False


def test_vrp_accrues_once_per_day(client, tmp_db):
    from db import connect
    client.get("/api/analysis/NVDA")
    client.get("/api/analysis/NVDA")   # same day → still one row
    conn = connect()
    n = conn.execute("SELECT COUNT(*) FROM implied_move_history WHERE symbol='NVDA'").fetchone()[0]
    conn.close()
    assert n == 1
    v = client.get("/api/vrp/NVDA").json()
    assert len(v["samples"]) == 1 and v["samples"][0]["realized_pct"] is None  # today: unresolved


def test_whatif_math_and_guard(client):
    r = client.post("/api/portfolio/whatif",
                    json={"changes": {"MSFT": -3000}}).json()
    # trimming into cash: dollar band should not increase
    assert r["after"]["risk"]["band_dollars"] <= r["before"]["risk"]["band_dollars"] * 1.001
    assert r["cash_after"] == pytest.approx(r["cash_before"] + 3000, abs=1)
    assert client.post("/api/portfolio/whatif",
                       json={"changes": {"MSFT": 10_000_000}}).status_code == 422


# ─── after-hours quote view (pure fixture tests — Schwab fields vary by session) ─

def test_extended_view_from_extended_object():
    from providers.schwab import _extended_view
    v = _extended_view(
        q={"lastPrice": 101.2, "quoteTime": 2_000},
        regular={"regularMarketLastPrice": 100.0, "regularMarketPercentChange": 1.4,
                 "regularMarketTradeTime": 1_000},
        extended={"lastPrice": 101.2},
    )
    assert v["is_extended"] is True
    assert v["ah_price"] == 101.2
    assert v["ah_change_pct"] == pytest.approx(1.2)
    assert v["regular_last"] == 100.0 and v["regular_change_pct"] == 1.4


def test_extended_view_inferred_from_composite_drift():
    from providers.schwab import _extended_view
    # no `extended` object, but the composite last traded away from the
    # official close AFTER the regular print → post-market inference
    v = _extended_view(
        q={"lastPrice": 99.0, "quoteTime": 5_000},
        regular={"regularMarketLastPrice": 100.0, "regularMarketTradeTime": 4_000},
        extended={},
    )
    assert v["is_extended"] and v["ah_change_pct"] == pytest.approx(-1.0)


def test_extended_view_regular_session_is_flat():
    from providers.schwab import _extended_view
    # same price / older quote time → NOT an extended session
    v = _extended_view(
        q={"lastPrice": 100.0, "quoteTime": 900},
        regular={"regularMarketLastPrice": 100.0, "regularMarketTradeTime": 1_000},
        extended={},
    )
    assert v["is_extended"] is False and v["ah_price"] is None


def test_watchlist_quotes_carry_ah_fields(client):
    client.post("/api/watchlist", json={"symbol": "AAPL"})
    q = client.get("/api/watchlist/quotes").json()["AAPL"]
    assert {"price", "day_pct", "ah_price", "ah_change_pct", "is_extended"} <= set(q)


def test_analysis_quote_carries_ah_fields(client):
    q = client.get("/api/analysis/MSFT").json()["quote"]
    assert "is_extended" in q and "regular_last" in q
    if q["is_extended"]:
        assert q["ah_price"] is not None and q["ah_change_pct"] is not None


def test_morning_sheet_renders(client):
    client.post("/api/watchlist", json={"symbol": "NVDA"})
    page = client.get("/api/morning-sheet").text
    assert "Morning sheet" in page and "NVDA" in page
    assert "TickerLens_MorningSheet_" in page      # print filename
    assert "no direction predictions" in page


# ─── Discover (leads pipeline) ─────────────────────────────────────────────────

def test_discovery_upsert_dedupe_and_dismiss_memory(tmp_db):
    from analysis.discovery import upsert_candidate, list_candidates, is_obvious
    assert is_obvious("AAPL") and is_obvious("XX", 90_000) and not is_obvious("CRDO", 5_000)
    assert upsert_candidate("CRDO", "pead", "beat by 30%") is True
    assert upsert_candidate("CRDO", "pead", "beat by 30%") is False        # exact dup
    assert upsert_candidate("AAPL", "pead", "beat") is False               # obvious filtered
    assert upsert_candidate("CRDO", "web-sweep", "optics thesis") is True  # merge new source
    rows = list_candidates()
    assert len(rows) == 1 and set(rows[0]["sources"]) == {"pead", "web-sweep"}
    # dismiss → hidden; NEW source type later → returned flag
    from db import connect
    conn = connect(); conn.execute(
        "UPDATE discovery_candidates SET status='dismissed' WHERE symbol='CRDO'")
    conn.commit(); conn.close()
    assert list_candidates() == []
    upsert_candidate("CRDO", "insider-cluster", "3 filings")
    rows = list_candidates(include_dismissed=True)
    assert rows[0]["returned"] == 1 and rows[0]["status"] == "dismissed"


def test_discovery_scan_and_endpoints(client, tmp_db):
    r = client.post("/api/discovery/scan").json()
    assert r["ok"] and r["total_added"] >= 3          # mock screens fire
    rows = client.get("/api/discovery").json()
    syms = {x["symbol"] for x in rows}
    assert "CRDO" in syms and "AAPL" not in syms and "SPY" not in syms  # obvious excluded
    assert "MILD" not in syms                          # SI change below threshold
    crdo = next(x for x in rows if x["symbol"] == "CRDO")
    assert len(crdo["sources"]) >= 2                   # pead + insider converge in mock
    # promote → watchlist tagged discovery; patch dismiss round-trip
    assert client.post("/api/discovery/CRDO/promote").json()["ok"]
    wl = {w["symbol"]: w for w in client.get("/api/watchlist").json()}
    assert "discovery" in wl["CRDO"]["tags"]
    assert client.patch("/api/discovery/IONQ", json={"status": "dismissed"}).json()["ok"]
    assert client.patch("/api/discovery/NOPE", json={"status": "dismissed"}).status_code == 404
    assert "discoveries/" in client.get("/api/discovery/prompt").json()["prompt"]


def test_discovery_sweep_ingest(tmp_db):
    from analysis.discovery import ingest_sweep_frontmatter, list_candidates
    n = ingest_sweep_frontmatter({"candidates": [
        {"ticker": "adv", "thesis": "insider buys + buyback", "risk": "margin"},
        {"ticker": "QQQ", "thesis": "obvious", "risk": ""},
        {"ticker": "", "thesis": "no symbol"},
    ]}, "2026-07-18")
    assert n == 1
    row = list_candidates()[0]
    assert row["symbol"] == "ADV" and "Risk: margin" in row["reasons"][0]["reason"]


# ─── rough-notes round: bottom context + logged export/import ──────────────────

def test_bottom_context_in_payload(client):
    a = client.get("/api/analysis/HIMS").json()
    bc = a["bottom_context"]
    assert bc is not None
    assert -100 <= bc["drawdown_pct"] <= 0.01
    assert bc["above_52w_low_pct"] >= 0
    assert 0 <= bc["range_percentile"] <= 100
    assert isinstance(bc["bottom_decile"], bool)
    assert "NOT whether this is the bottom" in bc["note"]  # the honesty line ships


def test_score_history_export_import_logged(client, tmp_db):
    client.get("/api/analysis/AAPL")   # creates ≥1 score row
    exp = client.get("/api/score-history/export").json()
    assert exp["format"] == "tickerlens-score-history-v1" and len(exp["rows"]) >= 1
    # import into same instance → full dedupe; foreign row → added
    r = client.post("/api/score-history/import", json={"rows": exp["rows"]}).json()
    assert r["imported"] == 0
    r2 = client.post("/api/score-history/import", json={"rows": [
        {"symbol": "ZZZZ", "ts": "2026-07-01T10:00:00", "score": 61.0, "lean": "BULLISH"}]}).json()
    assert r2["imported"] == 1
    log = client.get("/api/score-history/share-log").json()
    assert len(log) == 3                                  # 1 export + 2 imports
    assert {l["direction"] for l in log} == {"export", "import"}
