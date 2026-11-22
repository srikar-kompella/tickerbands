"""
Report accuracy guards (2026-11-21 audit): consistency checks withhold
disagreeing fields, earnings dates carry a confirmed/estimated tag, share
classes link, stale live quotes are withheld, the market clock knows
weekends/holidays/pre-market, and every rendered field is documented.
"""
import copy
import datetime as dt
import os
import re

import pytest

from analysis import market_clock as mc
from analysis import price_basis, report_view
from tests import replay_fixtures as rf


@pytest.fixture()
def client(tmp_db, mock_mode, tmp_discussions, monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "DISCUSSIONS_DIR", str(tmp_discussions))
    from fastapi.testclient import TestClient
    from app import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def nvda_view(tmp_db, monkeypatch):
    from analysis import composer
    rf.install(monkeypatch, rf.nvda_providers())
    return composer.analyze("NVDA", now=rf.NVDA_NOW)


# ── consistency checks: a failure → "—" + chip naming both numbers ───────────

@pytest.mark.parametrize("field,raw_key,bad,needle", [
    ("market_cap", "shares", 20.0e9, "price × shares"),
    ("pe_ttm", "eps", 8.0, "price ÷ EPS"),
    ("p_fcf", "fcf", 127.0e9, "market cap ÷ FCF"),
    ("ev", "debt", 60.0e9, "market cap + debt − cash & securities"),
    ("day_move", "prior_close", 121.0, "price ÷ prior close"),
    ("position_value", "qty", 101.0, "shares × price"),
])
def test_check_failure_withholds_field_and_names_both_numbers(nvda_view, field, raw_key, bad, needle):
    view = copy.deepcopy(nvda_view["report_view"])
    view["raw"][raw_key] = bad
    checks = report_view.apply_checks(view)
    failed = [c for c in checks if not c["ok"]]
    assert failed and any(field in c["fields"] for c in failed)
    f = view["fields"][field]
    assert f["status"] == "withheld" and f["display"] == "—"
    assert any(needle in w for w in f["warnings"])


def test_day_pl_larger_than_position_fails(nvda_view):
    view = copy.deepcopy(nvda_view["report_view"])
    view["fields"]["position_day_pl"]["value"] = -25_000.0
    report_view.apply_checks(view)
    assert view["fields"]["position_day_pl"]["status"] == "withheld"


def test_sigma_band_status_must_agree(nvda_view):
    view = copy.deepcopy(nvda_view["report_view"])
    view["fields"]["day_move_sigma"]["outside_band"] = False   # contradicts |z|≈3
    report_view.apply_checks(view)
    assert view["fields"]["day_move_sigma"]["status"] == "withheld"


def test_untagged_earnings_date_fails(nvda_view):
    view = copy.deepcopy(nvda_view["report_view"])
    view["fields"]["earnings_next"]["tag"] = None
    report_view.apply_checks(view)
    assert view["fields"]["earnings_next"]["status"] == "withheld"


def test_failed_check_renders_alert_and_dash(nvda_view):
    from api.report import build_report_html
    a = copy.deepcopy(nvda_view)
    a["report_view"]["raw"]["shares"] = 20.0e9
    report_view.apply_checks(a["report_view"])
    page = build_report_html(a, [], [])
    assert "consistency check(s) failed" in page
    row = page.split("<td>Market cap")[1].split("</tr>")[0]
    assert "<td class='v'>—</td>" in row and "$3.03T" not in row


# ── earnings dates: confirmed vs estimated, never a past date ─────────────────

def _earn(nd, ec, today=dt.date(2026, 10, 20)):
    v = report_view.build({"symbol": "X", "basis": {}, "now": rf.ny(today.year, today.month, today.day, 12),
                           "earnings": {"available": True, "next_date": nd},
                           "earnings_confirm": ec})
    return v["fields"]["earnings_next"]


def test_earnings_estimated_when_no_company_confirmation():
    f = _earn("2026-10-27", {"date": "2026-10-27", "estimate": True})
    assert f["tag"] == "estimated"


def test_earnings_confirmed_when_yahoo_flag_says_announced():
    f = _earn("2026-10-28", {"date": "2026-10-28", "estimate": False})
    assert f["tag"] == "confirmed"


def test_earnings_sources_disagree_shows_both_and_estimated():
    f = _earn("2026-10-27", {"date": "2026-10-28", "estimate": False})   # GOOGL case
    assert f["tag"] == "estimated"
    assert any("Finnhub 2026-10-27 vs Yahoo 2026-10-28" in w for w in f["warnings"])


def test_past_cached_earnings_date_never_shown():
    f = _earn("2026-10-19", {}, today=dt.date(2026, 10, 21))             # ISTR case
    assert f["value"] is None
    assert any("already passed" in w for w in f["warnings"])


# ── share-class linkage (GOOG report ↔ GOOGL position) ────────────────────────

def test_goog_report_links_googl_position(tmp_db, monkeypatch):
    from analysis import composer
    p = rf.googl_providers()
    q = p["quotes"]("GOOGL")
    p["quotes"] = lambda s: {**q, "symbol": "GOOG", "name": "Alphabet Inc Class C"}
    rf.install(monkeypatch, p)
    a = composer.analyze("GOOG", now=rf.GOOGL_NOW)
    f = a["report_view"]["fields"]["position_value"]
    assert f["linked"]["symbol"] == "GOOGL"
    assert "you hold 40 GOOGL (other share class" in f["display"]


# ── quote freshness: a stale live quote is not the price ─────────────────────

def test_open_market_stale_quote_withheld():
    hist = rf.bars_ending(dt.date(2026, 10, 28), [308.6])
    q = {"last": 270.0, "regular_last": 270.0,
         "quote_time_ms": rf.ms(rf.ny(2026, 10, 29, 9, 45))}
    b, _ = price_basis.build(q, hist, now=rf.ny(2026, 10, 29, 10, 30))
    assert b["price"] is None
    assert any("min old" in w for w in b["warnings"])


def test_open_market_cache_stale_quote_withheld():
    hist = rf.bars_ending(dt.date(2026, 10, 28), [308.6])
    q = {"last": 270.0, "regular_last": 270.0,
         "quote_time_ms": rf.ms(rf.ny(2026, 10, 29, 10, 29))}
    b, _ = price_basis.build(q, hist, now=rf.ny(2026, 10, 29, 10, 30), quote_status="stale")
    assert b["price"] is None


def test_report_route_forces_fresh_quote(monkeypatch, client):
    from analysis import composer
    seen = {}
    real = composer.analyze

    def spy(symbol, *a, **kw):
        seen.update(kw)
        return real(symbol, *a, **kw)
    monkeypatch.setattr(composer, "analyze", spy)
    assert client.get("/api/analysis/AAPL/report").status_code == 200
    assert seen.get("fresh_core") is True


# ── market clock ──────────────────────────────────────────────────────────────

def test_clock_weekend_holiday_premarket_post():
    assert mc.session_state(rf.ny(2026, 11, 21, 11, 25))["state"] == "closed"
    th = mc.session_state(rf.ny(2026, 11, 26, 12, 0))          # Thanksgiving
    assert th["state"] == "closed" and th["last_session"] == dt.date(2026, 11, 25)
    pre = mc.session_state(rf.ny(2026, 11, 23, 8, 0))
    assert pre["state"] == "pre" and pre["last_session"] == dt.date(2026, 11, 20)
    half = mc.session_state(rf.ny(2026, 11, 27, 13, 30))        # 1 PM early close
    assert half["state"] == "post"


def test_premarket_price_is_prior_close_with_labeled_premarket_quote():
    hist = rf.bars_ending(dt.date(2026, 11, 20), [145.98, 125.53])
    q = {"last": 128.0, "regular_last": 125.53,
         "quote_time_ms": rf.ms(rf.ny(2026, 11, 23, 7, 55))}
    b, _ = price_basis.build(q, hist, now=rf.ny(2026, 11, 23, 8, 0))
    assert b["price"] == 125.53 and b["move_word"] == "Fri Nov 20"
    assert b["after_hours"]["kind"] == "pre-market"


# ── every rendered field is documented ────────────────────────────────────────

def test_every_report_field_is_documented(nvda_view):
    doc = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "REPORT_FIELDS.md")
    text = open(doc, encoding="utf-8").read()
    documented = set(re.findall(r"^\| `([a-z0-9_]+)` \|", text, re.M))
    keys = set(nvda_view["report_view"]["fields"])
    missing = keys - documented
    assert not missing, f"undocumented report fields: {sorted(missing)}"
