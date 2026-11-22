"""
Research prompt fixes of Nov 22, 2026 (api/research_prompt.py and the helpers
in news_relevance / research_quality it reads). Four test runs on real names —
a two-day-old listing, an option-income ETF, a community bank and a software
company — listed where the prompt did not fit the security or wasted steps.

Each test pins one fix: the security-type variants (payload `security_kind`),
the app's filing list in <snapshot>, near misses and older unexplained
sessions, the window since the last standing note (capped at 30 days) and the
incremental run, PENDING triggers, the source rules, and the small contract
fixes. Tickers, dates and figures in the fixtures are illustrative.
"""
import datetime as dt
import re

import pytest

from analysis import news_relevance as nr
from analysis import research_quality as rq
from api import research_prompt as rp
from tests import replay_fixtures as rf
from tests.test_research_prompt_v5 import _analyze, _block, _prompt, _template


@pytest.fixture()
def base(tmp_db, monkeypatch):
    """NVDA replay (Sat 2026-11-21) with fresh statements and no notes."""
    return _analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")


def _kind(a, kind, **extra):
    return _prompt({**a, "security_kind": kind, **extra})


def _step(p, start, end):
    return p[p.index(start):p.index(end)]


def _step1(p):
    return _step(p, "1. Research first", "2. Reconcile the data")


def _guards(p):
    """The style guards of test_research_prompt_v5, for every variant: no
    shouting, "never" only in the hard rules, nothing that asks or waits."""
    for word in ("MUST", "IMPORTANT", "REQUIRED", "ALWAYS", "NEVER", "DO NOT", "NOT the"):
        assert word not in p, word
    nevers = re.findall(r"never [a-z ]{0,24}", p)
    assert all(n.startswith(("never a price direction", "never invent one")) for n in nevers), nevers
    text = p[p.index("## Your task, in order"):p.index("<examples>")]
    assert "?" not in text
    left = re.sub(r"(?i)(do not|does not|never|stops to) (ask|wait)\w*|asked the user "
                  r"nothing|in place of asking", "", text)
    assert not re.search(r"(?i)\b(ask|wait|confirm with|check with|clarif)\w*", left), \
        re.search(r"(?i).{60}\b(ask|wait|confirm with|check with|clarif)\w*.{60}", left)


# ── 1. security-type variants ────────────────────────────────────────────────

def test_security_kind_reads_defensively():
    assert rp.security_kind({}) == "stock"
    assert rp.security_kind({"security_kind": None}) == "stock"
    assert rp.security_kind({"security_kind": " Fund "}) == "fund"
    assert rp.security_kind({"security_kind": "warrant"}) == "stock"
    assert rp.security_kind({"security_kind": "new_listing"}) == "new_listing"


def test_stock_variant_generic_legal_list_and_no_fixed_export_controls(base):
    p = _kind(base, "stock")
    s1 = _step1(p)
    assert "NVDA, a stock they follow" in p
    assert ("c. the company name with each of: SEC, DOJ, state attorneys general, subpoena, "
            "securities class actions (the terms may be combined into one search per name); "
            "and search for enforcement actions by the company's main regulator by name") in s1
    assert "export controls" not in s1
    assert "not its own legal-process pages" in s1
    assert "do not count a law firm's \"investigation\" notice as a filed case" in s1
    assert "d. the latest earnings release" in s1
    assert "e. the company's investor-relations page for the next earnings date" in s1
    _guards(p)


def test_stock_export_controls_only_when_the_snapshot_news_mentions_export_rules(base):
    heads = [{"headline": "ZZ chip maker faces new export controls on its accelerators",
              "summary": "", "source": "Example Wire", "ts": None}]
    s1 = _step1(_prompt({**base, "headlines": heads}))
    assert "securities class actions, export controls (the terms may be combined" in s1
    reg = [{"headline": "Agency tightens export rules", "terms": ["export"]}]
    assert ", export controls (the terms" in _step1(_prompt({**base, "regulatory_hits": reg}))


def test_fund_variant(base):
    a = {**base, "sec_filings": [{"date": "2026-11-18", "form": "8-K", "items": "8.01",
                                  "desc": "8.01 other events", "url": "https://example.test/x"}],
         "sec_cik": "0000000001"}
    p = _kind(a, "fund")
    s1, snap = _step1(p), _block(p, "snapshot")
    assert "NVDA, a fund (an ETF or similar) they follow" in p
    # fund and issuer news; the filings a fund files, not 8-K items
    assert "a. fund and issuer news — the issuer's own page for the fund" in s1
    assert ("b. the fund's SEC filings since 2026-11-16: N-PORT (its holdings), 497 or 497K "
            "(prospectus and strategy changes), N-CSR (its reports) and 19a-1 notices") in s1
    assert "a fund files these, not 8-K items" in s1
    assert "8-K, the items named on its row" not in s1
    # the app's 8-K list is not shown for a fund; the full-text search line is
    assert "SEC filings since" not in snap
    assert "Fund filings: not fetched by the app." in snap and "efts.sec.gov" in snap
    # legal searches at the issuer and the top holdings, and tax rules
    assert ("c. the issuer by name (the sponsor or adviser behind the fund, not the ticker) "
            "with each of: SEC, DOJ, state attorneys general, subpoena, securities class "
            "actions; the same for the fund's three largest holdings") in s1
    assert "tax-rule change" in s1 and "export controls" not in s1
    # distributions with tax character; NAV against the index; concentration
    assert "d. distributions: the latest declared amount with its ex-date and pay date" in s1
    assert "how much was return of capital" in s1
    assert ("e. the fund against its index — the index named in its prospectus or fact sheet: "
            "any change to it or to the strategy, the expense ratio, NAV return against that "
            "index") in s1
    assert "10 largest holdings" in s1
    # no earnings or investor-relations steps anywhere in step 1
    assert "earnings release" not in s1 and "investor-relations" not in s1
    assert "(B) searches b, d and e — filings, distributions, and the fund against its index" in s1
    # step 2: no P/FCF or EV; reconcile the distribution
    s2 = _step(p, "2. Reconcile the data", "3. Set and check")
    assert "yields, position value, the premium or discount to NAV" in s2
    assert "P/FCF, EV" not in s2
    assert "A fund has no earnings, free cash flow or enterprise value" in s2
    assert "Say whether a distribution yield is forward or trailing." in s2
    # fund triggers
    s3 = _step(p, "3. Set and check", "4. Analyze")
    assert ("(the distribution per share, the return-of-capital share, NAV erosion against "
            "the index, the fee, the tracking gap)") in s3
    # step 4: the SPY-based net figure is not the fund's own doing
    s4 = _step(p, "4. Analyze", "5. Lay out")
    assert "measured against SPY, not against the fund's own index" in s4
    assert "do not read the SPY-based net figure as the fund's own doing" in s4
    assert "how much of each large move was the company's own" not in s4
    _guards(p)


def test_fund_rows_appear_in_the_snapshot_when_the_report_has_them(base):
    rv = base["report_view"]
    fields = {**rv["fields"],
              "fund_expense_ratio": {"label": "Expense ratio", "display": "0.68%",
                                     "warnings": [], "status": "ok"},
              "fund_nav": {"label": "NAV per share", "display": "$49.90",
                           "basis": "the source's latest published NAV", "warnings": []},
              "fund_schedule": {"label": "Distribution schedule",
                                "display": "monthly (12/yr) · latest $0.58, ex 2031-01-20",
                                "warnings": []}}
    p = _kind({**base, "report_view": {**rv, "fields": fields}}, "fund")
    snap = _block(p, "snapshot")
    assert "- Expense ratio: 0.68%" in snap
    assert "- NAV per share: $49.90 (the source's latest published NAV)" in snap
    assert "- Distribution schedule: monthly (12/yr) · latest $0.58, ex 2031-01-20" in snap


def test_bank_variant(base):
    rv = base["report_view"]
    fields = {**rv["fields"],
              "p_tbv": {"label": "Price / tangible book", "display": "1.25",
                        "basis": "report price; equity $1.0B − goodwill $0.2B", "warnings": []},
              "roe": {"label": "Return on equity (TTM)", "display": "9.8%", "warnings": []}}
    p = _kind({**base, "report_view": {**rv, "fields": fields}}, "bank")
    s1, snap = _step1(p), _block(p, "snapshot")
    assert "NVDA, a bank stock they follow" in p
    assert ("c. the company and its bank subsidiary by name with each of: SEC, DOJ, state "
            "attorneys general, subpoena, securities class actions (the terms may be combined "
            "into one search per name); and search for enforcement "
            "actions by the bank's primary federal regulator, set by its charter") in s1
    assert "state banking regulator" not in s1
    assert "export controls" not in s1
    assert "net interest margin, credit quality and capital ratios" in s1
    assert "- Price / tangible book: 1.25 (report price; equity $1.0B − goodwill $0.2B)" in snap
    assert "- Return on equity (TTM): 9.8%" in snap
    s2 = _step(p, "2. Reconcile the data", "3. Set and check")
    assert "market cap, P/E, price to tangible book, yields, position value" in s2
    assert "enterprise value, net cash and P/FCF in <snapshot> do not apply" in s2
    for term in ("price to tangible book value", "return on equity", "net interest margin",
                 "nonperforming loans", "capital ratios", "deposit trend"):
        assert term in s2, term
    s3 = _step(p, "3. Set and check", "4. Analyze")
    assert ("(price to tangible book, net interest margin, nonperforming loans, a capital "
            "ratio, deposits)") in s3
    s4 = _step(p, "4. Analyze", "5. Lay out")
    assert "Set aside its cash-flow, enterprise-value and acquisition rows" in s4
    _guards(p)


def _new_listing(base):
    """Two sessions of history: no σ for either session, the last one −8.5%."""
    basis = {**base["price_basis"], "day_move_pct": -8.5}
    recent = [{"date": "2026-11-19", "close": 97.0, "change_pct": 6.4, "z": None},
              {"date": "2026-11-20", "close": 88.75, "change_pct": -8.5, "z": None}]
    return {**base, "security_kind": "new_listing", "is_new_listing": True, "price_basis": basis,
            "move": {**base["move"], "z": None}, "big_move_sessions": [],
            "recent_sessions": recent}


def test_new_listing_variant(base):
    p = _prompt(_new_listing(base))
    s1 = _step1(p)
    assert "NVDA, a newly listed stock they follow" in p
    assert "d. the prospectus — the S-1 and the final 424B on EDGAR" in s1
    assert "there is no earnings release yet" in s1
    assert "e. the first lock-up expiry and the end of the quiet period" in s1
    assert "investor-relations page for the next earnings date" not in s1
    assert "a matter opened before the window that is still live counts, with its date" in s1
    # σ cannot be measured: "cannot tell", and the large moves are still named
    assert ("The price history is too short for the app to measure a move in σ, so no session "
            "here could be tested against 2σ. That means the test could not run, not that no "
            "session moved much.") in s1
    assert ("   Sessions in this window that moved 5% or more and still need a cause (no σ to "
            "test them with):\n"
            "   - 2026-11-19 (Thu): its row under Recent sessions in <snapshot>\n"
            "   - 2026-11-20 (Fri): the Day move row in <snapshot>\n") in s1
    assert ('move_cause "found" when each has its cause; research_status "partial" and '
            'move_cause "not_found" when one does not') in s1
    s4 = _step(p, "4. Analyze", "5. Lay out")
    assert "no move needs a story" not in s4
    assert "For each session listed in step 1, give the competing explanations" in s4
    # pre-IPO balance sheet and share-count caveats
    s2 = _step(p, "2. Reconcile the data", "3. Set and check")
    assert "may predate the offering, so restate them with the proceeds" in s2
    assert "shares outstanding after the offering or fully diluted" in s2
    _guards(p)


def test_new_listing_with_no_large_move_says_the_test_could_not_run(base):
    a = _new_listing(base)
    a["recent_sessions"] = [{**x, "change_pct": 1.2} for x in a["recent_sessions"]]
    a["price_basis"] = {**a["price_basis"], "day_move_pct": 1.2}
    s4 = _step(_prompt(a), "4. Analyze", "5. Lay out")
    assert ("The app could not test any session against 2σ (the price history is too short) "
            "and step 1 lists no move of 5% or more.") in s4


# ── 2. speed: the filing list in <snapshot>, the release exhibit ─────────────

FILINGS = [
    {"date": "2026-11-17", "form": "8-K", "items": "2.02,9.01", "desc": "2.02 results of operations",
     "url": "https://www.sec.gov/Archives/edgar/data/0/fictional-8k.htm", "red_flag": False},
    {"date": "2026-11-19", "form": "8-K", "items": "5.02", "desc": "5.02 officer change",
     "url": "https://www.sec.gov/Archives/edgar/data/0/fictional-8k-2.htm", "red_flag": True},
]
EXHIBIT = "https://www.sec.gov/Archives/edgar/data/0/fictional-ex991.htm"


def test_filings_are_listed_in_the_snapshot_and_step_1b_reads_them(base):
    smeta = {**base["statements_meta"],
             "release": {"date": "2026-11-17", "url": FILINGS[0]["url"], "exhibit_url": EXHIBIT}}
    p = _prompt({**base, "sec_filings": FILINGS, "sec_cik": "0000000002",
                 "statements_meta": smeta})
    snap, s1 = _block(p, "snapshot"), _step1(p)
    assert ("SEC filings since 2026-11-16 — 8-K, 10-Q and 10-K only (EDGAR's index as the "
            "app fetched it, newest first)\n"
            "- 2026-11-19 8-K — items 5.02 officer change (red-flag item) · "
            "https://www.sec.gov/Archives/edgar/data/0/fictional-8k-2.htm\n"
            f"- 2026-11-17 8-K — items 2.02 results of operations · exhibit 99.1: {EXHIBIT}") in snap
    assert ("b. the SEC filings listed in <snapshot> (SEC filings since 2026-11-16): read every "
            "one filed in the window — for an 8-K, the items named on its row and its "
            "exhibits; a Form 4 or 144 is an insider's trade or planned sale") in s1
    assert ("check EDGAR for anything newer than the list only when the snapshot is more "
            "than one trading day old") in s1
    assert ("for forms the list leaves out (Form 4, 144, Form 3, S-8, NT 10-Q and NT 10-K, "
            "S-1, 424B; a foreign issuer files 6-K and 20-F)") in s1
    # the submissions JSON stays as the fallback, with what to do when it is cut short
    assert "https://data.sec.gov/submissions/CIK0000000002.json" in s1
    assert "if your fetch tool cuts that file short" in s1
    # the earnings release links its exhibit, not the 8-K cover
    assert f"d. the latest earnings release ({EXHIBIT});" in s1
    assert "cover page" not in s1
    # the filings are not repeated on the regulatory feed row
    assert "filings are listed above" in snap or "regulatory & legal — nothing matched" in snap


def test_empty_filing_list_says_which_forms_it_covers(base):
    snap = _block(_prompt({**base, "sec_filings": [], "sec_cik": "0000000002"}), "snapshot")
    assert ("SEC filings since 2026-11-16: no 8-K, 10-Q or 10-K in EDGAR's index when the app "
            "fetched it (other forms are not listed)") in snap


def test_long_filing_list_is_cut_with_a_count(base):
    many = [{**FILINGS[1], "date": f"2026-11-{d:02d}"} for d in range(1, 16)]
    snap = _block(_prompt({**base, "sec_filings": many, "sec_cik": "1"}), "snapshot")
    assert snap.count(" 8-K — items 5.02") == rp.FILINGS_SHOWN
    assert f"- and {15 - rp.FILINGS_SHOWN} older, not listed" in snap


def test_c3_prefers_the_release_exhibit(tmp_db, monkeypatch):
    a = _analyze(monkeypatch, rf.nvda_providers(fresh_statements=False), rf.NVDA_NOW, "NVDA")
    smeta = {**a["statements_meta"],
             "release": {"date": "2026-11-17", "url": FILINGS[0]["url"], "exhibit_url": EXHIBIT}}
    p = _prompt({**a, "statements_meta": smeta})
    c3 = next(ln for ln in _block(p, "data_caveats").splitlines() if ln.startswith("C3 "))
    assert f"Earnings release: {EXHIBIT} (8-K filed 2026-11-17, exhibit 99.1)." in c3
    # without the exhibit the cover is linked and the session is told where the figures are
    smeta["release"] = {"date": "2026-11-17", "url": FILINGS[0]["url"]}
    p = _prompt({**a, "statements_meta": smeta})
    assert ("d. the latest earnings release (https://www.sec.gov/Archives/edgar/data/0/"
            "fictional-8k.htm — the 8-K's cover page; the figures are in its exhibit 99.1);") in p


def test_news_page_one_sweep_and_dated_searches_replace_per_day_searches(base):
    s1 = _step1(_prompt(base))
    assert "the company's own news or press-release page" in s1
    assert "one search sweep" in s1
    assert "for each day of the window" not in s1
    assert "a weekend or market holiday needs a search of its own only where a story is known" in s1
    # the parallel-subagent paragraph is kept, adapted to the steps
    assert "run step 1 as three in parallel and collect all three results — (A) search a" in s1


# ── 3. near misses and older unexplained sessions ────────────────────────────

def test_near_miss_sessions_rule():
    rows = [
        # −8.75% at −1.96σ (σ 4.46%), −4.87% of it net of the market → near miss
        {"date": "2026-11-19", "change_pct": -8.75, "z": -1.96, "net_of_market_pct": -4.87},
        # same size but mostly the market → not
        {"date": "2026-11-18", "change_pct": -8.0, "z": -1.8, "net_of_market_pct": -1.0},
        # under 1.5σ → not
        {"date": "2026-11-17", "change_pct": -3.0, "z": -1.2, "net_of_market_pct": -3.0},
        # a full 2σ session is not a near miss (it is listed as a 2σ session)
        {"date": "2026-11-16", "change_pct": -9.0, "z": -2.1, "net_of_market_pct": -8.0},
        # no z / no net → skipped
        {"date": "2026-11-13", "change_pct": -7.0, "z": None, "net_of_market_pct": -7.0},
        {"date": "2026-11-12", "change_pct": 7.0, "z": 1.7, "net_of_market_pct": None},
        # before the window
        {"date": "2026-11-06", "change_pct": 7.5, "z": 1.7, "net_of_market_pct": 6.0},
    ]
    got = nr.near_miss_sessions(rows, "2026-11-10")
    assert [s["session_date"] for s in got] == ["2026-11-19"]
    assert got[0] == {"session_date": "2026-11-19", "z": -1.96, "move_pct": -8.75,
                      "net_pct": -4.87}
    assert nr.near_miss_sessions(rows, "2026-11-10", {"2026-11-19"}) == []
    assert nr.near_miss_sessions(None) == []


def test_near_miss_is_listed_and_recorded_without_touching_move_cause(base):
    recent = [dict(x) for x in base["recent_sessions"]]
    i = next(k for k, x in enumerate(recent) if x["date"] == "2026-11-19")
    recent[i].update(change_pct=-8.75, z=-1.96, net_of_market_pct=-4.87)
    p = _prompt({**base, "recent_sessions": recent})
    s1 = _step1(p)
    assert ("   Near misses — sessions in this window between 1.5σ and 2σ whose move net of the "
            "market was at least 1σ:\n"
            "   - 2026-11-19 (Thu): its row under Recent sessions in <snapshot>\n") in s1
    assert ("A near miss does not change move_cause or research_status, which follow the 2σ "
            "sessions only; one whose cause you did not find goes under `## Unknowns` with the "
            "searches you ran.") in s1
    s4 = _step(p, "4. Analyze", "5. Lay out")
    assert "Do the same for each near miss listed there." in s4
    out = p[p.index("## Output contract"):]
    assert "(near misses, market-wide moves and older sessions do not count here)" in out
    _guards(p)


def _note(ts, **meta):
    return {"created_ts": ts, "summary": "s", "research_meta": meta}


FOUND = {"prompt_version": 5, "research_status": "complete", "move_cause": "found"}


def test_older_unexplained_sessions_lie_after_the_note_and_before_the_window():
    basis = {"session_date": "2026-11-20", "price_kind": "official_close"}
    move = {"z": 0.3, "move_pct": 0.5}
    sessions = [{"session_date": "2026-07-30", "z": -2.5, "move_pct": -4.0},   # before the note
                {"session_date": "2026-09-02", "z": -2.41, "move_pct": -3.1},  # older, unexplained
                {"session_date": "2026-10-30", "z": -2.9, "move_pct": -3.3}]   # inside the window
    notes = [_note("2026-08-04T09:00:00", **FOUND, news_window="2026-07-28..2026-08-04")]
    got = rq.older_unexplained_sessions(notes, move, basis, sessions, "2026-10-21")
    assert [s["session_date"] for s in got] == ["2026-09-02"]
    # no window start, or no sound note → nothing listed
    assert rq.older_unexplained_sessions(notes, move, basis, sessions, None) == []
    bad = [_note("2026-08-04T09:00:00", prompt_version=5, research_status="partial")]
    assert rq.older_unexplained_sessions(bad, move, basis, sessions, "2026-10-21") == []
    # a later sound note that explains the session takes it off the list
    notes.append(_note("2026-09-03T09:00:00", **FOUND, news_window="2026-08-27..2026-09-03"))
    assert rq.older_unexplained_sessions(notes, move, basis, sessions, "2026-10-21") == []
    # they never make a note INVALID: the STALE note stays STALE
    st = rq.prior_note_status(notes[:1], move, basis, sessions)
    assert st[0]["status"] == rq.STALE


def test_older_sessions_line_is_optional_and_one_line(base, monkeypatch):
    rf.add_discussion("NVDA", "2026-08-04T09:00:00", "A summer note")
    older = [{"session_date": f"2026-09-{d:02d}", "z": -2.4, "move_pct": -3.1}
             for d in range(1, 11)]
    monkeypatch.setattr(rq, "older_unexplained_sessions", lambda *a, **k: older)
    p = _prompt(base)
    s1 = _step1(p)
    line = next(ln for ln in s1.splitlines() if ln.startswith("   Older sessions"))
    assert ("Older sessions still unexplained — each moved 2σ or more before this window and "
            f"no note that still stands gives its cause (the {rp.OLDER_SHOWN} "
            "most recent of 10): 2026-09-03 (−3.10%, −2.40σ);") in line
    assert "2026-09-02" not in line and "2026-09-10 (−3.10%, −2.40σ)." in line
    assert ("Give a one-line cause for each where one search finds it: one opened source is "
            "enough, a cause seen only in a search-result headline is written \"not verified\", "
            "and \"not looked into\" covers the rest. These are optional: they change "
            "neither research_status nor move_cause.") in line
    _guards(p)


# ── 4. the window and the incremental run ────────────────────────────────────

def test_news_window_since_the_last_standing_note_capped_at_30_days():
    today, last = dt.date(2026, 11, 22), "2026-11-20"
    assert nr.news_window(None, last, today) == {"from": "2026-11-16", "to": "2026-11-22"}
    # a recent note inside the 5 sessions does not shorten them
    assert nr.news_window("2026-11-19", last, today) == {
        "from": "2026-11-16", "to": "2026-11-22", "last_note": "2026-11-19"}
    # a note 31 days back starts the window at the note… up to the cap
    assert nr.news_window("2026-10-29", last, today) == {
        "from": "2026-10-29", "to": "2026-11-22", "last_note": "2026-10-29"}
    assert nr.news_window("2026-08-07", last, today) == {
        "from": "2026-10-23", "to": "2026-11-22", "last_note": "2026-08-07", "capped": True}
    # a note dated in the future or unreadable is ignored
    assert nr.news_window("2027-01-01", last, today) == {"from": "2026-11-16", "to": "2026-11-22"}
    assert nr.news_window("soon", last, today) == {"from": "2026-11-16", "to": "2026-11-22"}


def test_last_research_date_skips_invalid_notes():
    basis = {"session_date": "2026-11-20", "price_kind": "official_close"}
    notes = [_note("2026-10-01T09:00:00", **FOUND),
             _note("2026-11-01T09:00:00", prompt_version=5, research_status="failed")]
    assert rq.last_research_date(notes, {"z": 0.2}, basis) == "2026-10-01"
    assert rq.last_research_date([], {"z": 0.2}, basis) is None


def test_window_wording_matches_the_code(tmp_db, monkeypatch):
    """The two eval runs whose last note was weeks old got a 7-day window under
    a sentence that said "since the last report … whichever is longer"."""
    rf.add_discussion("NVDA", "2026-11-05T09:00:00", "An early-November note")
    a = _analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")
    assert a["news_window"] == {"from": "2026-11-05", "to": "2026-11-21",
                                "last_note": "2026-11-05"}
    s1 = _step1(_prompt(a))
    assert ("Window: 2026-11-05 → 2026-11-21 (16 days back, 12 trading sessions) — since the last research note on this "
            "ticker that still stands (OK or STALE in <prior_notes>), at most 30 days back, or "
            "the last 5 trading sessions, whichever is longer. Here it starts at the note of "
            "2026-11-05.") in s1
    # longer than 5 sessions: one sweep for the last 5, one per calendar week before
    assert "one for each calendar week before that (2026-11-05 up to 2026-11-16)" in s1


def _fresh_note(ts="2026-11-21T09:00:00"):
    rf.add_discussion("NVDA", ts, "Cause of the Nov 20 drop found; hold",
                      news_view="bearish", news_note="Nov 16–21: cause found",
                      stance="hold", stance_horizon="3-6mo", stance_note="n",
                      research_meta={**FOUND, "news_window": "2026-11-16..2026-11-21",
                                     "price_used": 125.53, "stance_confidence": "low",
                                     "review_by": "2027-01-15",
                                     "upgrade_if": ["x"], "downgrade_if": ["y"]})


def test_incremental_run_when_an_ok_note_is_under_24_hours_old(tmp_db, monkeypatch):
    _fresh_note()
    a = _analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")
    p = _prompt(a)
    assert "STATUS: OK" in _block(p, "prior_notes")
    s1 = _step1(p)
    assert ("This is an incremental run: the note of 2026-11-21T09:00 in <prior_notes> is OK "
            "and less than 24 hours old") in s1
    assert "the short list below, since 2026-11-21T09:00" in s1
    assert '("as of 2026-11-21, earlier note")' in s1
    assert ("   a. company news — the company's own news or press-release page since "
            "2026-11-21T09:00, and one search sweep for that period") in s1
    # the 2σ session the fresh note explained is not asked for again
    assert "Sessions in this window that moved 2σ or more and still need a cause" not in s1
    step7 = _step(p, "7. Check your work", "8. Write the file")
    assert ("everything since the note of 2026-11-21T09:00 is covered, the rest of the window "
            "2026-11-16 → 2026-11-21 is carried forward from that note with dates") in step7
    assert 'news_window: "2026-11-21..2026-11-21"' in _template(p)
    _guards(p)


def test_no_incremental_run_for_an_older_or_unsound_note(tmp_db, monkeypatch):
    _fresh_note("2026-11-20T08:00:00")          # 27 hours before the snapshot
    p = _prompt(_analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA"))
    assert "incremental run" not in p
    rf.add_discussion("NVDA", "2026-11-21T10:00:00", "Unfinished",
                      research_meta={"prompt_version": 5, "research_status": "partial"})
    p = _prompt(_analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA"))
    assert "incremental run" not in p


# ── 5. triggers ──────────────────────────────────────────────────────────────

def test_pending_verdict_and_the_users_own_rules(base):
    p = _prompt(base)
    s3 = _step(p, "3. Set and check", "4. Analyze")
    assert "Mark each trigger MET, NOT MET, PENDING or UNKNOWN" in s3
    assert "PENDING means the event or filing the trigger depends on has not happened yet" in s3
    assert ("check those as well, as written — also where one is a price level: the limit on "
            "price direction covers the triggers you set, not the user's own rules.") in s3
    assert "`## Triggers, checked` — each trigger from step 3 as MET / NOT MET / PENDING / " \
           "UNKNOWN" in p
    task = _step(p, "## Your task, in order", "1. Research first")
    assert ("A project file your tool loaded into this session by itself (for example a "
            "CLAUDE.md from another folder) counts as the user's notes only when it is about "
            "the user's own investing (rules, plans, positions); otherwise ignore it. Notes "
            "that count bring along a file in the same folder that those notes point to") in task
    # the owned example uses the PENDING verdict too
    assert "the add trigger is PENDING until the Q1 release" in p


# ── 6. sources ───────────────────────────────────────────────────────────────

def test_source_rules(base):
    s1 = _step1(_prompt(base))
    assert "Copies of one wire story on several sites count as one source" in s1
    assert ("A search tool's own summary of its results is not a source: open the page and "
            "quote it.") in s1
    assert ("Where <snapshot> shows a large move and your searches find nothing, widen them "
            "before you log that") in s1
    out = _prompt(base)
    assert ("`sources_count` counts distinct publishers, with copies of one wire story counted "
            "once.") in out


# ── 7. small contract fixes ──────────────────────────────────────────────────

def test_number_slots_are_unquoted_and_the_quoting_rule_says_so(base):
    p = _prompt(base)
    tpl = _template(p)
    assert "\nsources_count: <a whole number, unquoted" in tpl
    assert "\nprice_used: <a plain number, unquoted" in tpl
    assert ("The two numbers, `sources_count` and `price_used`, are written without quotes, "
            "as in the examples.") in p
    assert ('Under the table, put the searches that found nothing on one line — "Searched, '
            'nothing found: X; Y; Z" — not a row each.') in p


def test_broker_day_pl_disagreement_is_left_out_of_the_prompt_only(base):
    rv = base["report_view"]
    f = dict(rv["fields"]["position_day_pl"])
    f["warnings"] = ["broker day P/L −$9,999.00 exceeds the whole session's move"]
    pv = dict(rv["fields"]["position_value"])
    pv["warnings"] = ["broker values it at $1.00 (its own mark) — the app uses the report price"]
    fields = {**rv["fields"], "position_day_pl": f, "position_value": pv}
    snap = _block(_prompt({**base, "report_view": {**rv, "fields": fields}}), "snapshot")
    assert "broker day P/L" not in snap
    assert "broker values it at $1.00" in snap          # other broker notes stay
    assert "broker day P/L" in fields["position_day_pl"]["warnings"][0]   # report untouched
