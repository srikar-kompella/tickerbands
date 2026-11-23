"""
Replay tests for reports that showed wrong numbers (Oct–Nov 2026).

One test (or assertion group) per row of the 2026-11-21 fact-check
table, plus the GOOGL 10/29 and VST 11/04 cases and the earlier defects
(earnings-date tags, analyst phantom, thin samples, share-class link, quote
cache). Written BEFORE the fix; they failed against the previous code (the
old composer had no injectable clock, priced everything off the after-hours
quote, and rendered vendor fundamentals verbatim).
"""
import re
import time

import pytest

from tests import replay_fixtures as rf


def _analyze(monkeypatch, providers, now, symbol, **kw):
    from analysis import composer
    rf.install(monkeypatch, providers)
    return composer.analyze(symbol, now=now, **kw)


def _f(a, key):
    return a["report_view"]["fields"][key]


@pytest.fixture()
def nvda(tmp_db, monkeypatch):
    rf.add_discussion("NVDA", "2026-11-13T09:00:00",
                      "Board had not hired a search firm as of Nov 13")
    rf.add_discussion("NVDA", "2026-11-21T10:05:00", "Hold through volatility",
                      news_view="neutral",
                      news_note="No material NVDA news today; looks like sector rotation",
                      stance="hold", stance_horizon="3-6mo",
                      stance_note="valuation compressed, thesis intact")
    return _analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")


# ── row 1–2: price, day move, sigma ──────────────────────────────────────────

def test_nvda_price_is_fridays_official_close_not_after_hours(nvda):
    b = nvda["price_basis"]
    assert b["state"] == "closed"
    assert b["price"] == 125.53 and b["price_kind"] == "official_close"
    assert b["session_date"] == "2026-11-20"
    assert "Official close" in b["price_label"] and "Fri Nov 20" in b["price_label"]
    assert _f(nvda, "price")["display"] == "$125.53"
    ah = _f(nvda, "after_hours")
    assert ah["value"] == 130.03 and "after hours" in ah["display"]
    assert "+3.58%" in ah["display"]


def test_nvda_day_move_is_the_last_real_session(nvda):
    assert _f(nvda, "day_move")["value"] == pytest.approx(-14.01, abs=0.01)
    assert "Fri Nov 20 session" in _f(nvda, "day_move")["display"]
    mv = nvda["move"]
    assert mv["z"] <= -2.5 and mv["outside_band"] is True     # ~3σ, outside
    assert mv["session_date"] == "2026-11-20"


def test_nvda_report_never_says_today_on_a_weekend(nvda, monkeypatch):
    from api.report import build_report_html
    page = build_report_html(nvda, [], [])
    import html
    head = html.unescape(page[page.index('<div class="head">'):page.index("<svg")])
    assert re.search(r"\btoday\b", head, re.I) is None, head
    assert "as of Friday's close" in head
    assert "$130.03" in head and "$125.53" in head      # AH shown only as its own line


# ── rows 5–9: price-dependent fundamentals at ONE price ───────────────────────

def test_nvda_market_cap_recomputed_from_report_price(nvda):
    f = _f(nvda, "market_cap")
    assert f["value"] == pytest.approx(125.53 * 24.10e9, rel=1e-6)
    assert f["display"] == "$3.03T"
    assert any("implies a price" in w for w in f["warnings"])  # vendor $3.52T flagged


def test_nvda_pe_at_report_price(nvda):
    assert _f(nvda, "eps_ttm")["value"] == pytest.approx(9.31)
    assert _f(nvda, "pe_ttm")["value"] == pytest.approx(13.48, abs=0.01)
    assert _f(nvda, "pe_ttm")["display"] == "13.5"


def test_nvda_fcf_includes_q3_and_p_fcf_at_report_price(nvda):
    assert _f(nvda, "fcf_ttm")["value"] == pytest.approx(148.72e9, rel=1e-4)
    assert _f(nvda, "p_fcf")["value"] == pytest.approx(20.34, abs=0.02)
    assert _f(nvda, "p_fcf")["display"] == "20.3x"
    assert nvda["statements_meta"]["covers_latest"] is True


def test_nvda_ev_and_net_cash_breakdown(nvda):
    ev = _f(nvda, "ev")
    assert ev["display"] == "$2.96T"
    nc = _f(nvda, "net_cash")
    assert nc["value"] == pytest.approx(65.0e9, rel=1e-6)
    assert "marketable equity securities $44.0B" in nc["display"]


def test_nvda_net_margin_basis_and_non_operating_flag(nvda):
    nm = _f(nvda, "net_margin")
    assert nm["value"] == pytest.approx(62.5, abs=0.05)
    assert "TTM" in nm["label"] and "2026-10-25" in nm["basis"]
    assert any("non-operating" in w for w in nm["warnings"])
    assert _f(nvda, "net_margin_q")["value"] == pytest.approx(56.4, abs=0.05)
    assert any("non-operating" in w for w in _f(nvda, "pe_ttm")["warnings"])


def test_nvda_dividend_forward_and_trailing_labeled(nvda):
    fwd, trl = _f(nvda, "div_yield_forward"), _f(nvda, "div_yield_trailing")
    assert fwd["value"] == pytest.approx(0.25 * 4 / 125.53 * 100, abs=0.005)
    assert fwd["display"] == "0.80%"
    assert trl["value"] == pytest.approx(0.52 / 125.53 * 100, abs=0.005)
    assert "forward" in fwd["label"] and "trailing" in trl["label"]


# ── row 10: one beta ──────────────────────────────────────────────────────────

def test_nvda_one_beta_labeled(nvda):
    from api.report import build_report_html
    from api.routes import build_claude_prompt
    b = _f(nvda, "beta")
    assert "60 daily returns vs SPY" in b["basis"]
    page = build_report_html(nvda, [], [])
    assert "2.4" not in page.split("Beta vs SPY")[1][:200]   # vendor beta not shown
    p = build_claude_prompt(nvda)
    assert "Quote only the snapshot beta (60 daily returns vs SPY)" in p
    assert "Beta vs SPY: 4.77 (60 daily returns vs SPY)" in p


# ── rows 3–4, 11: news coverage + carried-forward notes ───────────────────────

def test_nvda_irrelevant_headlines_dropped(nvda):
    hv = _f(nvda, "headlines")
    assert hv["value"] == 0 and hv["items"] == []
    from api.report import build_report_html
    page = build_report_html(nvda, [], [])
    for junk in ("Intel shares", "Jim Cramer", "annuity", "Anthropic closes"):
        assert junk not in page


def test_nvda_news_check_after_3_sigma_move_is_flagged(nvda):
    w = " ".join(_f(nvda, "claude_news")["warnings"])
    assert "no material news" in w and "failed" in w
    sw = " ".join(_f(nvda, "claude_stance")["warnings"])
    assert "re-run research" in sw


def test_nvda_prompt_demands_window_cause_and_regulatory_pass(nvda):
    from api.routes import build_claude_prompt
    p = build_claude_prompt(nvda)
    # the window starts at the last note that still stands (Nov 13, STALE)
    assert "Window: 2026-11-13 → 2026-11-21" in p
    assert "Same-day headlines are not enough" in p
    assert '"no material news" is not an available conclusion' in p
    assert "two independent sources" in p and "cause not found after searching" in p
    # "export controls" is a search only when the snapshot's news rows mention
    # export rules; the company's main regulator is always one
    for term in ("SEC", "DOJ", "state attorneys general", "the company's main regulator",
                 "class actions", "8-K"):
        assert term in p
    assert "- 2026-11-13T09:00:00 ·" in p          # prior notes are dated…
    assert "trust: Out of date" in p and "trust: Don't use" in p      # …and judged
    assert "$130.03" in p and "an extended-hours quote, not the price" in p
    assert "Market cap: $3.03T" in p and "P/E (TTM): 13.5" in p


def test_nvda_old_discussions_dated_in_report(nvda):
    from api.report import build_report_html
    from discussions_svc.parser import list_discussions
    page = build_report_html(nvda, list_discussions("NVDA"), [])
    assert "Written 2026-11-13 — figures and facts in this note are as of that date" in page
    assert "STALE — written before the ≥2σ session of 2026-11-20 closed" in page
    # the Nov 21 note said "no material news" after that session
    assert "INVALID — its news check failed" in page and "do not build on this note" in page


# ── row 12: analyst feed; earlier: phantom score contribution ─────────────────

def test_nvda_stale_analyst_feed_is_no_data(nvda):
    assert _f(nvda, "analyst_mix")["display"] == "—"
    assert any("stale" in w for w in _f(nvda, "analyst_mix")["warnings"])
    row = next(c for c in nvda["setup_score"]["components"]
               if c["component"] == "analyst_trend")
    assert row["available"] is False and row["points"] == 0.0


def test_nvda_two_message_social_not_scored(nvda):
    row = next(c for c in nvda["setup_score"]["components"]
               if c["component"] == "social_buzz")
    assert row["available"] is False and "below minimum" in row["status"]
    assert row["sample_n"] == 2
    assert _f(nvda, "social")["display"] == "—"


# ── row 13: one price basis for the position ──────────────────────────────────

def test_nvda_position_valued_at_report_price(nvda):
    pv = _f(nvda, "position_value")
    assert pv["value"] == pytest.approx(100 * 125.53)
    assert "× $125.53" in pv["display"]
    dpl = _f(nvda, "position_day_pl")
    assert dpl["value"] == pytest.approx(100 * (125.53 - 145.98))
    assert all(c["ok"] for c in nvda["report_view"]["checks"]), nvda["report_view"]["checks"]


def test_nvda_earnings_date_tagged_and_data_as_of_line(nvda):
    e = _f(nvda, "earnings_next")
    assert e["tag"] == "estimated" and "estimated" in e["display"]
    asof = nvda["report_view"]["data_as_of"]
    assert "Official close" in asof["price"]
    assert "2026-10-25" in asof["statements"]
    assert "required window" in asof["news"]


# ── statement freshness (NVDA FCF $127B; GOOGL pre-Q3 P/E) ─────────────────────

def _seed_cache(key, data, age_s):
    from cache.store import _put_row
    from db import connect
    _put_row(key, data)
    conn = connect()
    try:
        conn.execute("UPDATE cache SET fetched_ts = ? WHERE key = ?",
                     (time.time() - age_s, key))
        conn.commit()
    finally:
        conn.close()


def test_nvda_stale_statement_cache_refetched_after_earnings(tmp_db, monkeypatch):
    _seed_cache("statements:NVDA", rf.nvda_statements(fresh=False), 7 * 3600)
    a = _analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")
    assert a["statements_meta"]["refreshed"] is True
    assert _f(a, "fcf_ttm")["value"] == pytest.approx(148.72e9, rel=1e-4)


def test_nvda_missing_latest_quarter_is_warned(tmp_db, monkeypatch):
    a = _analyze(monkeypatch, rf.nvda_providers(fresh_statements=False),
                 rf.NVDA_NOW, "NVDA")
    assert a["statements_meta"]["covers_latest"] is False
    assert _f(a, "fcf_ttm")["value"] == pytest.approx(127.0e9, rel=1e-3)
    assert any("does not include the latest quarter" in w
               for w in _f(a, "fcf_ttm")["warnings"])
    assert "does not include the latest quarter" in a["report_view"]["data_as_of"]["statements"]


# ── GOOGL 10/29: day P/L, open market, statements after earnings ─────────────

def test_googl_day_pl_never_exceeds_position(tmp_db, monkeypatch):
    a = _analyze(monkeypatch, rf.googl_providers(), rf.GOOGL_NOW, "GOOGL")
    b = a["price_basis"]
    assert b["state"] == "open" and b["price"] == 270.00
    assert b["prior_date"] == "2026-10-28"              # today's partial bar excluded
    dpl = _f(a, "position_day_pl")
    assert dpl["value"] == pytest.approx(40 * (270.00 - 308.60))
    assert abs(dpl["value"]) <= _f(a, "position_value")["value"]
    assert any("broker day P/L −$25,000.00 exceeds" in w for w in dpl["warnings"])


def test_googl_statements_refetched_after_q3(tmp_db, monkeypatch):
    _seed_cache("statements:GOOGL", rf.googl_statements(False), 7 * 3600)
    a = _analyze(monkeypatch, rf.googl_providers(True), rf.GOOGL_NOW, "GOOGL")
    assert a["statements_meta"]["refreshed"] is True
    assert a["report_view"]["data_as_of"]["statements"].startswith(
        "TTM through quarter ended 2026-09-30")


def test_googl_statements_still_pre_q3_warns(tmp_db, monkeypatch):
    a = _analyze(monkeypatch, rf.googl_providers(False), rf.GOOGL_NOW, "GOOGL")
    assert any("does not include the latest quarter" in w
               for w in _f(a, "eps_ttm")["warnings"])


# ── VST 11/04: day move vs after-hours, published and unpublished bar ─────────

def test_vst_day_move_is_the_session_not_after_hours(tmp_db, monkeypatch):
    a = _analyze(monkeypatch, rf.vst_providers(), rf.VST_NOW, "VST")
    assert a["price_basis"]["price"] == 150.00
    assert _f(a, "day_move")["value"] == pytest.approx(-14.69, abs=0.01)
    assert "today" in _f(a, "day_move")["display"]          # it IS today's session
    assert a["move"]["outside_band"] is True
    assert _f(a, "after_hours")["value"] == 148.48
    dpl = _f(a, "position_day_pl")
    assert dpl["value"] == pytest.approx(40 * (150.00 - 175.83))
    assert any("exceeds the whole position" in w for w in dpl["warnings"])


def test_vst_unpublished_bar_uses_broker_regular_close(tmp_db, monkeypatch):
    a = _analyze(monkeypatch, rf.vst_providers(bar_published=False), rf.VST_NOW, "VST")
    b = a["price_basis"]
    assert b["synthetic_close"] is True and b["price"] == 150.00
    assert _f(a, "day_move")["value"] == pytest.approx(-14.69, abs=0.01)
    assert a["band"]["anchor_date"] == "2026-11-04"       # band not on a stale close
