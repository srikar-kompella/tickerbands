"""
Verdict (prompt v6) — wiring: file → parser → DB → /api/analysis payload
(claude_stance verdict keys, trust, valuation_range), the report fields and
HTML export, Watchlist rows, GET /api/discussions trust, the scorecard on a
derived stance, and the SEC per-year inputs of valuation_range.
Fictional ZZ tickers only.
"""
import pytest

from tests.conftest import write_md

B = 1e9

V6 = [
    'ticker: "ZZVW"', "created_ts: 2026-11-21T10:00:00", "prompt_version: 6",
    'summary: "Stretched; two soft signals"', 'research_status: "complete"',
    'move_cause: "na"', 'news_window: "2026-11-14..2026-11-21"',
    'verdict: "stretched"', 'value_grade: "fair"', 'risk_grade: "watch"',
    'value_basis: "P/FCF 24 vs five-year median 23: fair on its own history"',
    'verdict_note: "Fair price, but two confirmed soft signals drop it a step"',
    'soft_signals: ["receivables +35% vs revenue +9% (10-Q filed 2026-11-04)", '
    '"guidance cut (8-K 2026-11-04)"]',
    'watch_items: ["press report of an inquiry, unnamed sources (2026-11-18)"]',
    'owner_note: "your rule — trim only above 10% weight — is not met"',
    'stance_confidence: "medium"', 'price_used: 101.5',
    'upgrade_if: ["by 2027-03-01: guidance restored"]',
    'downgrade_if: ["by 2027-03-01: a third soft signal confirmed"]',
    'review_by: "2027-03-01"',
]


@pytest.fixture()
def client(tmp_db, mock_mode, tmp_discussions, monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "DISCUSSIONS_DIR", str(tmp_discussions))
    from fastapi.testclient import TestClient
    from app import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def v6_note(client, tmp_discussions):
    from discussions_svc.watcher import catch_up_scan
    write_md(tmp_discussions, "ZZVW", "2026-11-21T10-00-00.md", V6,
             body="## TL;DR\nStretched.")
    assert catch_up_scan() >= 1
    return client


def test_analysis_payload_carries_the_verdict_keys(v6_note):
    a = v6_note.get("/api/analysis/ZZVW").json()
    cs = a["claude_stance"]
    # legacy keys kept, derived stance
    assert cs["stance"] == "trim" and cs["label"] == "TRIM"
    assert cs["stance_note"].startswith("Fair price")
    assert cs["format"] == "v6" and cs["verdict"] == "stretched"
    assert cs["value_grade"] == "fair" and cs["risk_grade"] == "watch"
    assert cs["verdict_label"] == "Stretched (Fair · Watch)"
    assert len(cs["soft_signals"]) == 2 and cs["hard_flags"] == []
    assert cs["watch_items"] == ["press report of an inquiry, unnamed sources (2026-11-18)"]
    assert cs["owner_line"] in ("Not owned: pass", "You own it: consider trimming",
                                "If you own it: consider trimming · if not: pass")
    assert cs["owner_note"].startswith("your rule")
    assert cs["expected_verdict"] == "stretched" and cs["expected_risk"] == "watch"
    assert cs["status"] in ("OK", "STALE") and cs["trust"] in ("current", "out_of_date")
    assert cs["value_basis"].startswith("P/FCF 24")
    # rulebook followed → no rulebook / risk flags
    w = a["report_view"]["fields"]["claude_stance"]["warnings"]
    assert not any("rulebook" in x or "risk_grade" in x for x in w)
    f = a["report_view"]["fields"]["claude_stance"]
    assert f["label"] == "Research verdict" and f["group"] == "research"
    assert f["display"].startswith("Stretched (Fair · Watch) — Fair price")
    assert f["format"] == "v6" and f["trust"] == cs["trust"]


def test_analysis_payload_has_a_valuation_range_and_report_fields(v6_note):
    a = v6_note.get("/api/analysis/ZZVW").json()
    vr = a["valuation_range"]
    assert set(vr) >= {"measure", "label", "now", "now_basis", "points", "median", "low",
                       "high", "grade_hint", "normalized", "warnings", "status"}
    # mock fiscal years carry no dates → the points are left out, said so
    assert vr["status"] == "no_data" and vr["grade_hint"] is None and vr["warnings"]
    F = a["report_view"]["fields"]
    assert {"valuation_now", "valuation_points", "valuation_range"} <= set(F)
    assert all(F[k]["group"] == "valuation" for k in
               ("valuation_now", "valuation_points", "valuation_range"))
    assert "valuation" in a["report_view"]["groups"]


def test_report_html_shows_the_verdict_strip(v6_note):
    page = v6_note.get("/api/analysis/ZZVW/report").text
    assert "Research verdict (2026-11-21)" in page
    assert "Stretched (Fair · Watch)" in page
    assert "Soft signals:</b> receivables +35% vs revenue +9%" in page
    assert "Watch items:</b> press report of an inquiry" in page
    assert "your rule — trim only above 10% weight — is not met" in page
    assert "Valuation vs its own history" in page


def test_rulebook_mismatch_reaches_the_payload(client, tmp_discussions):
    from discussions_svc.watcher import catch_up_scan
    lines = [x for x in V6 if not x.startswith(("verdict:", "risk_grade:"))]
    write_md(tmp_discussions, "ZZVX", "2026-11-21T10-00-00.md",
             [x.replace("ZZVW", "ZZVX") for x in lines]
             + ['verdict: "attractive"', 'risk_grade: "clean"'], body="## TL;DR\nx")
    catch_up_scan()
    a = client.get("/api/analysis/ZZVX").json()
    w = a["research_flags"]["stance"]
    assert any(x.startswith("verdict does not follow the rulebook: expected stretched") for x in w)
    assert any(x.startswith("risk_grade does not match the lists: expected watch") for x in w)


def test_earlier_note_is_format_earlier_with_trust(client, tmp_discussions):
    from discussions_svc.watcher import catch_up_scan
    write_md(tmp_discussions, "ZZVY", "2026-11-20T10-00-00.md", [
        'ticker: "ZZVY"', "created_ts: 2026-11-20T10:00:00", "prompt_version: 5",
        'stance: "hold"', 'research_status: "complete"', 'move_cause: "na"'],
        body="## TL;DR\nold")
    catch_up_scan()
    cs = client.get("/api/analysis/ZZVY").json()["claude_stance"]
    assert cs["format"] == "earlier" and cs["stance"] == "hold" and "verdict" not in cs
    assert cs["trust"] in ("current", "out_of_date")


def test_screener_rows_carry_verdict_fields(v6_note):
    v6_note.post("/api/watchlist", json={"symbol": "ZZVW"})
    v6_note.post("/api/watchlist", json={"symbol": "ZZVZ"})          # no note
    rows = v6_note.get("/api/watchlist/screener", params={"symbols": "ZZVW,ZZVZ"}).json()
    r = next(x for x in rows if x["symbol"] == "ZZVW")
    assert r["stance"] == "trim" and r["verdict"] == "stretched"
    assert r["verdict_label"] == "Stretched (Fair · Watch)"
    assert r["value_grade"] == "fair" and r["risk_grade"] == "watch" and r["format"] == "v6"
    assert r["owner_line"].startswith(("If you own it", "You own it", "Not owned"))
    assert r["trust"] in ("current", "out_of_date", None)
    empty = next(x for x in rows if x["symbol"] == "ZZVZ")
    for k in ("verdict", "verdict_label", "value_grade", "risk_grade", "trust",
              "owner_line", "format"):
        assert k in empty and empty[k] is None


def test_discussions_rows_carry_trust(v6_note):
    rows = v6_note.get("/api/discussions", params={"ticker": "ZZVW"}).json()
    assert rows and all("trust" in r for r in rows)
    from analysis import research_quality as rq
    assert all(r["trust"] == rq.trust(r["status"]) for r in rows)


def test_scorecard_scores_a_v6_note_on_its_derived_stance():
    from analysis.scorecard import score_stances
    bars = [{"date": "2026-11-20", "close": 100.0}, {"date": "2026-12-20", "close": 90.0}]
    spy = [{"date": "2026-11-20", "close": 500.0}, {"date": "2026-12-20", "close": 505.0}]
    out = score_stances([{"ticker": "ZZVW", "stance": "trim", "stance_horizon": None,
                          "stance_note": "Fair price, but two soft signals",
                          "created_ts": "2026-11-21T10:00:00"}], {"ZZVW": bars}, spy)
    row = out["rows"][0]
    # stretched → trim keeps today's trim expectation: lagging SPY is "working"
    assert row["stance"] == "trim" and row["working"] is True
    assert out["summary"]["trim"]["n"] == 1


def test_scorecard_route_picks_up_the_derived_stance(v6_note):
    rows = v6_note.get("/api/stances/scorecard").json().get("rows") or []
    assert any(r["ticker"] == "ZZVW" and r["stance"] == "trim" for r in rows)


# ── SEC per-year inputs ────────────────────────────────────────────────────────

def _fact(start, end, val, form="10-K", filed="2031-02-01"):
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed,
            "accn": "0000000000-31-000009"}


def _inst(end, val, filed="2031-02-01"):
    return {"end": end, "val": val, "form": "10-K", "filed": filed,
            "accn": "0000000000-31-000009"}


def test_sec_annual_carries_shares_filed_date_and_tangible_book():
    from providers.sec_xbrl import normalize
    rev = [_fact("2029-01-01", "2029-12-31", 300 * B, filed="2030-02-01"),
           _fact("2030-01-01", "2030-12-31", 350 * B)]
    us = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": rev}},
        "NetIncomeLoss": {"units": {"USD": [dict(r, val=r["val"] * 0.3) for r in rev]}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
            _fact("2029-01-01", "2029-12-31", 1.0e9, filed="2030-02-01"),
            _fact("2030-01-01", "2030-12-31", 1.1e9)]}},
        "StockholdersEquity": {"units": {"USD": [_inst("2029-12-31", 100 * B, "2030-02-01"),
                                                 _inst("2030-12-31", 120 * B)]}},
        "Goodwill": {"units": {"USD": [_inst("2029-12-31", 10 * B, "2030-02-01"),
                                       _inst("2030-12-31", 12 * B)]}},
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [
            _inst("2030-12-31", 5 * B)]}},
    }
    s = normalize({"facts": {"us-gaap": us}})
    a0, a1 = s["annual"][0], s["annual"][1]
    assert a0["end"] == "2030-12-31" and a0["shares_diluted"] == 1.1e9
    assert a0["shares_filed"] == "2031-02-01" and a1["shares_filed"] == "2030-02-01"
    assert a0["tangible_book"] == pytest.approx(108 * B)
    assert a1["tangible_book"] == pytest.approx(90 * B)
