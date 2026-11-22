"""
Research prompt fixes of Nov 22, 2026, round 3 (api/research_prompt.py and
the session helpers in news_relevance / research_quality). Four more test
runs (a new listing, an option-income ETF, a community bank, a software
company) listed where the prompt still left room for two sessions to differ;
each test pins one fix: which forms the filing list covers, sessions the
market explains, research_status for a page that refused to open, thin
coverage and the stance, filings on a weekend, combined legal searches,
PENDING without a search, the length budget, news_view, the window wording,
the INVALID sentence, a fund's EDGAR search and the auto-loaded project file.
Tickers, dates and figures are illustrative.
"""
import pytest

from analysis import news_relevance as nr
from analysis import research_quality as rq
from api import research_prompt as rp
from tests import replay_fixtures as rf
from tests.test_prompt_fixes_1122 import FOUND, _fresh_note, _guards, _note, _step, _step1
from tests.test_research_prompt_v5 import _analyze, _block, _prompt


@pytest.fixture()
def base(tmp_db, monkeypatch):
    """NVDA replay (Sat 2026-11-21): the Fri Nov 20 session is −14.01%,
    −2.97σ, and no notes."""
    return _analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")


def _out(p):
    return p[p.index("## Output contract"):p.index("```markdown")]


FILINGS = [{"date": "2026-11-18", "form": "8-K", "items": "8.01", "desc": "8.01 other events",
            "url": "https://example.test/8k"}]


# ── 1. which forms the filing list covers ────────────────────────────────────

def test_filing_forms_reads_the_payload_defensively():
    assert rp.filing_forms({}) is None
    assert rp.filing_forms({"sec_filing_forms": "8-K"}) is None        # not a list
    assert rp.filing_forms({"sec_filing_forms": []}) is None
    assert rp.filing_forms({"sec_filing_forms": [" 8-K", None, "", "8-K", "4"]}) == ["8-K", "4"]
    assert rp._covers(["4/A"], "4") and not rp._covers(["424B4"], "4")
    assert rp._covers(["424B4"], "424B") and rp._covers(["S-8 POS"], "S-8")
    assert rp._covers(["NT 10-Q"], "NT 10") and not rp._covers(["10-Q"], "NT 10")


def test_header_states_the_forms_the_payload_says_the_list_covers(base):
    forms = ["8-K", "10-Q", "10-K", "4", "144", "3", "S-8"]
    p = _prompt({**base, "sec_filings": FILINGS, "sec_cik": "0000000002",
                 "sec_filing_forms": forms})
    snap, s1 = _block(p, "snapshot"), _step1(p)
    assert ("SEC filings since 2026-11-16 — forms covered: 8-K, 10-Q, 10-K, Form 4, Form 144, "
            "Form 3, S-8 (EDGAR's index as the app fetched it, newest first)") in snap
    # step 1b names only the forms the list leaves out
    assert ("for forms the list leaves out (NT 10-Q and NT 10-K, S-1, 424B; a foreign issuer "
            "files 6-K and 20-F), scan EDGAR's index for any in the window and open one of a "
            "kind, summarizing the rest from the index") in s1
    assert "Form 4, 144" not in s1.split("for forms the list leaves out")[1][:40]
    _guards(p)


def test_header_folds_amendments_schedules_and_a_stocks_fund_forms(base):
    forms = ["8-K", "8-K/A", "10-Q", "4", "4/A", "SC 13G", "SCHEDULE 13G", "SCHEDULE 13D",
             "N-PORT-P", "497"]
    assert rp._forms_shown(forms, fund=False) == ["8-K", "10-Q", "4", "SC 13G", "SCHEDULE 13D"]
    assert "N-PORT-P" in rp._forms_shown(forms, fund=True)
    snap = _block(_prompt({**base, "sec_filings": FILINGS, "sec_cik": "0000000002",
                           "sec_filing_forms": forms}), "snapshot")
    assert ("— forms covered: 8-K, 10-Q, Form 4, SC 13G, SCHEDULE 13D; amendments included "
            "(EDGAR's index") in snap


def test_empty_list_does_not_say_other_forms_are_missing_when_they_are_covered(base):
    p = _prompt({**base, "sec_filings": [], "sec_cik": "0000000002",
                 "sec_filing_forms": ["8-K", "10-Q", "10-K", "4", "144"]})
    snap = _block(p, "snapshot")
    assert ("SEC filings since 2026-11-16: none in EDGAR's index when the app fetched it "
            "(forms covered: 8-K, 10-Q, 10-K, Form 4, Form 144)") in snap
    assert "other forms are not listed" not in snap
    # without the payload key the old inferred wording stands
    snap0 = _block(_prompt({**base, "sec_filings": [], "sec_cik": "0000000002"}), "snapshot")
    assert "no 8-K, 10-Q or 10-K in EDGAR's index when the app fetched it (other forms are not " \
           "listed)" in snap0


def test_every_form_covered_leaves_no_left_out_clause_and_form_3_s8_are_explained(base):
    forms = ["8-K", "10-Q", "10-K", "4", "144", "3", "S-8", "NT 10-Q", "NT 10-K", "S-1",
             "424B4", "6-K", "20-F"]
    s1 = _step1(_prompt({**base, "sec_filings": FILINGS, "sec_cik": "0000000002",
                         "sec_filing_forms": forms}))
    assert "forms the list leaves out" not in s1
    assert "a Form 3 is a new insider's first report of holdings" in s1
    assert "an S-8 registers shares for employee stock plans" in s1


def test_fund_with_fund_forms_covered_keeps_the_list_line_even_when_empty(base):
    p = _prompt({**base, "security_kind": "fund", "sec_filings": [], "sec_cik": "0000000003",
                 "sec_filing_forms": ["N-PORT-P", "497", "497K", "N-CSR"]})
    snap = _block(p, "snapshot")
    assert "none in EDGAR's index when the app fetched it (forms covered: N-PORT-P, 497, " \
           "497K, N-CSR)" in snap
    assert "Fund filings: not fetched by the app" not in snap
    # a fund whose list covers no fund form still gets the full-text search line
    snap2 = _block(_prompt({**base, "security_kind": "fund", "sec_filings": FILINGS,
                            "sec_filing_forms": ["8-K"]}), "snapshot")
    assert "Fund filings: not fetched by the app" in snap2


# ── 2. sessions the market explains ──────────────────────────────────────────

def _market_rows(rows, net=-1.2, spy=-12.0):
    """The Nov 20 row rewritten as a market day: −14.01% at −2.97σ (σ
    4.72%), only `net` of it net of the market."""
    out = [dict(x) for x in rows]
    out[-1].update(spy_change_pct=spy, net_of_market_pct=net)
    return out


def test_market_net_uses_the_sessions_own_row():
    rows = [{"date": "2026-11-20", "change_pct": -3.0, "z": -2.4, "net_of_market_pct": -0.8}]
    assert nr.market_net("2026-11-20", rows) == -0.8           # σ 1.25%, net 0.8% < σ
    assert nr.market_net("2026-11-19", rows) is None           # no row
    big_net = [{**rows[0], "net_of_market_pct": -1.3}]
    assert nr.market_net("2026-11-20", big_net) is None        # net ≥ 1σ → the company's own
    no_beta = [{**rows[0], "net_of_market_pct": None}]
    assert nr.market_net("2026-11-20", no_beta) is None        # no beta / SPY → today's rule
    no_z = [{**rows[0], "z": None}]
    assert nr.market_net("2026-11-20", no_z) is None
    assert nr.market_net("2026-11-20", no_z, 1.25, "2026-11-20") == -0.8   # move's σ for its day


def test_big_sessions_marks_market_moves_and_the_mark_survives_a_second_pass():
    basis = {"session_date": "2026-11-20", "price_kind": "official_close"}
    move = {"z": -2.4, "move_pct": -3.0, "daily_sigma_pct": 1.25}
    bars = [{"session_date": "2026-11-12", "z": 2.6, "move_pct": 5.0},
            {"session_date": "2026-11-20", "z": -2.4, "move_pct": -3.0}]
    rows = [{"date": "2026-11-12", "change_pct": 5.0, "z": 2.6, "net_of_market_pct": 4.1},
            {"date": "2026-11-20", "change_pct": -3.0, "z": -2.4, "net_of_market_pct": -0.8}]
    big = nr.big_sessions(move, basis, bars, rows)
    assert [nr.is_market_move(s) for s in big] == [False, True]
    assert big[1]["market_net_pct"] == -0.8
    # the composer's list passed on as `sessions`, without rows: the report's
    # own session is rebuilt from `move` and keeps its mark
    again = nr.big_sessions(move, basis, big)
    assert [nr.is_market_move(s) for s in again] == [False, True]
    assert [s["session_date"] for s in nr.cause_sessions(again)] == ["2026-11-12"]
    # judging helpers leave the market move out
    t = nr._parse_ts("2026-11-19T09:00:00")
    assert [s["session_date"] for s in nr.sessions_closed_after(t, again)] == []
    assert [s["session_date"] for s in
            nr.sessions_to_explain(None, again, "2026-11-10..2026-11-20", "2026-11-20")] == \
        ["2026-11-12"]


def test_note_status_and_flags_skip_a_market_move_and_keep_the_rule_without_a_row():
    basis = {"session_date": "2026-11-20", "price_kind": "official_close"}
    move = {"z": -2.4, "move_pct": -3.0, "daily_sigma_pct": 1.25}
    rows = [{"date": "2026-11-20", "change_pct": -3.0, "z": -2.4, "net_of_market_pct": -0.8}]
    na = _note("2026-11-21T09:00:00", prompt_version=5, research_status="complete",
               move_cause="na", news_window="2026-11-16..2026-11-21")
    marked = nr.big_sessions(move, basis, None, rows)
    assert rq.prior_note_status([na], move, basis, marked)[0]["status"] == rq.OK
    flags = nr.research_flags(na, na, move, basis, sessions=marked)
    assert not any("does not state the cause" in f for f in flags["news"] + flags["stance"])
    # the same note written before that close is not STALE for a market move
    early = _note("2026-11-19T09:00:00", **FOUND, news_window="2026-11-13..2026-11-19")
    assert rq.prior_note_status([early], move, basis, marked)[0]["status"] == rq.OK
    # no row (or no beta): the plain ≥2σ rule
    assert rq.prior_note_status([na], move, basis, None)[0]["status"] == rq.INVALID
    no_beta = [{**rows[0], "net_of_market_pct": None}]
    plain = nr.big_sessions(move, basis, None, no_beta)
    assert rq.prior_note_status([na], move, basis, plain)[0]["status"] == rq.INVALID
    assert rq.prior_note_status([early], move, basis, plain)[0]["status"] == rq.STALE


def test_composer_marks_market_moves_from_its_recent_rows(tmp_db, monkeypatch):
    seen = []
    real = nr.big_sessions

    def spy(move, basis, sessions=None, recent=None):
        seen.append(recent)
        return real(move, basis, sessions, recent)

    monkeypatch.setattr(nr, "big_sessions", spy)
    a = _analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")
    assert any(r == a["recent_sessions"] and r for r in seen)
    # the replay's Nov 20 is −18.30% net of the market: the company's own move
    assert not any(nr.is_market_move(s) for s in a["big_move_sessions"])


def test_prompt_labels_a_market_move_and_does_not_ask_for_its_cause(base):
    p = _prompt({**base, "recent_sessions": _market_rows(base["recent_sessions"])})
    s1, snap, out = _step1(p), _block(p, "snapshot"), _out(p)
    assert "still need a cause" not in s1
    assert ("Explained by the market — moved 2σ or more, but less than 1σ net of the market "
            "(move − beta × SPY's move, from its Recent sessions row), so no news cause is "
            "needed: 2026-11-20 (Fri): market-wide move (net of market −1.20%). Name each as a "
            "market move under `## What happened`; it changes neither move_cause nor "
            "research_status.") in s1
    assert "- Fri 2026-11-20: the Day move row above; SPY −12.00%; net −1.20% — market-wide " \
           "move" in snap
    s4 = _step(p, "4. Analyze", "5. Lay out")
    assert "No session in the window still needs a cause" in s4
    assert "(near misses, market-wide moves and older sessions do not count here)" in out
    _guards(p)


def test_prompt_without_net_figure_keeps_the_2_sigma_rule(base):
    rows = [dict(x) for x in base["recent_sessions"]]
    rows[-1].update(net_of_market_pct=None)
    s1 = _step1(_prompt({**base, "recent_sessions": rows}))
    assert "Sessions in this window that moved 2σ or more and still need a cause:" in s1
    assert "market-wide move" not in s1
    # the replay's own row: −18.30% net of the market is the company's move
    assert "market-wide move" not in _step1(_prompt(base))


def test_prior_note_with_na_reads_ok_for_a_market_move(base):
    rf.add_discussion("NVDA", "2026-11-21T09:00:00", "Market day; nothing company-specific",
                      news_view="neutral", news_note="Nov 16–21: market-wide selloff",
                      research_meta={"prompt_version": 5, "research_status": "complete",
                                     "move_cause": "na",
                                     "news_window": "2026-11-16..2026-11-21"})
    notes = _block(_prompt({**base, "recent_sessions": _market_rows(base["recent_sessions"])}),
                   "prior_notes")
    assert "STATUS: OK" in notes
    assert "STATUS: INVALID" in _block(_prompt(base), "prior_notes")


# ── 3 & 4. research_status, pages that refuse to open, thin coverage ─────────

def test_research_status_covers_a_page_that_refused_to_open(base):
    p = _prompt(base)
    out = _out(p)
    assert ('"partial" means a required search was not run, or the cause of a listed '
            "session was not found") in out
    assert ("A search that ran but whose key page refused to open is logged as \"not "
            "verified\" (name the page under `## Unknowns`); it does not by itself make "
            "research partial when the app's lists in <snapshot> or another source cover the "
            "same ground.") in out
    assert ("List only links you actually opened in this session, plus any page that refused "
            "to open: its link marked \"(could not open)\" and its claim \"not verified\".") in out
    s7 = _step(p, "7. Check your work", "8. Write the file")
    assert ("every link in the evidence table is one you opened in this session, and its quote "
            "is copied from that page — except a row for a page that refused to open, which "
            "keeps its link marked \"(could not open)\", has no quote and reads \"not "
            "verified\"") in s7
    _guards(p)


def test_thin_coverage_may_keep_the_stance_with_a_reason_and_the_app_rule_stays(base):
    s6 = _step(_prompt(base), "6. Give one stance", "7. Check your work")
    assert ("If research_status is failed, or partial because a required search was not run, "
            "the stance is watch instead") in s6
    assert ("When every required search ran and only a listed session's cause is still not "
            "found, research_status stays \"partial\" and move_cause \"not_found\", but the "
            "stance may stay the one your analysis supports (hold or buy, for example) where "
            "stance_note starts with \"research incomplete\" and gives the reason the missing "
            "cause does not change the thesis; without such a reason it is watch.") in s6
    # the app side is unchanged: partial is INVALID and flagged on the stance
    n = _note("2026-11-21T09:00:00", prompt_version=5, research_status="partial",
              move_cause="not_found")
    basis = {"session_date": "2026-11-20", "price_kind": "official_close"}
    assert rq.prior_note_status([n], {"z": -2.97}, basis)[0]["status"] == rq.INVALID
    flags = rq.stance_flags({**n, "stance": "hold", "research_meta": {
        **n["research_meta"], "upgrade_if": ["x"], "downgrade_if": ["y"]}})
    assert any(f.startswith("research incomplete (research_status: partial)") for f in flags)


# ── 5. filings on a weekend ──────────────────────────────────────────────────

def test_edgar_newer_check_only_when_the_list_can_be_behind(base, tmp_db):
    s1 = _step1(_prompt({**base, "sec_filings": FILINGS, "sec_cik": "0000000002"}))
    assert ("check EDGAR for anything newer than the list only when the snapshot is more than "
            "one trading day old (C1 gives its date) or a story points to a filing the list "
            "does not hold") in s1
    assert "check EDGAR only for anything newer" not in s1
    fund = _step1(_prompt({**base, "security_kind": "fund",
                           "sec_filings": [{"date": "2026-11-18", "form": "N-PORT-P",
                                            "url": "https://example.test/n"}]}))
    assert "only when the snapshot is more than one trading day old" in fund


def test_incremental_filings_step_uses_the_same_condition(tmp_db, monkeypatch):
    _fresh_note()
    s1 = _step1(_prompt(_analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")))
    assert ("b. filings since 2026-11-21T09:00: any row of the filing list in <snapshot> dated "
            "since then; check EDGAR for anything newer than the list only when the snapshot "
            "is more than one trading day old") in s1


# ── 6. legal searches may be combined ────────────────────────────────────────

def test_legal_terms_may_be_combined_per_name(base):
    for kind in ("stock", "bank"):
        assert "(the terms may be combined into one search per name)" in \
            _step1(_prompt({**base, "security_kind": kind}))
    fund = _step1(_prompt({**base, "security_kind": "fund"}))
    assert ("(the terms may be combined into one search per name: the issuer and each "
            "holding)") in fund


# ── 7. PENDING without a search ──────────────────────────────────────────────

def test_future_dated_conditions_are_pending_without_a_search(base):
    p = _prompt(base)
    s3 = _step(p, "3. Set and check", "4. Analyze")
    assert ("A trigger or open condition that cannot resolve before a future date (the next "
            "earnings release, for example) is marked PENDING with that date, without a "
            "search.") in s3
    assert "each open condition in <prior_notes> that can already have resolved" in _step1(p)


# ── 8. length ────────────────────────────────────────────────────────────────

def test_length_budget_and_the_shorter_incremental_note(base, monkeypatch):
    out = _out(_prompt(base))
    assert "Aim for about 1,100 words excluding tables and stay under 1,300" in out
    assert "500 words" not in out
    _fresh_note()
    inc = _out(_prompt(_analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")))
    assert ("On this incremental run aim for about 500 words and do not restate the earlier "
            "note: point to it for anything unchanged.") in inc


# ── 9. news_view ─────────────────────────────────────────────────────────────

def test_news_view_is_defined(base):
    assert ("`news_view` is the overall tone of news about this security in the window — "
            "company news, not the market's moves.") in _out(_prompt(base))
    assert "— fund or issuer news, not the market's moves." in \
        _out(_prompt({**base, "security_kind": "fund"}))


# ── 10. window wording ───────────────────────────────────────────────────────

def test_window_counts_days_back_and_sessions_and_the_last_five_end_on_the_last_session(
        tmp_db, monkeypatch):
    rf.add_discussion("NVDA", "2026-11-05T09:00:00", "An early-November note")
    a = _analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")
    s1 = _step1(_prompt(a))
    assert "Window: 2026-11-05 → 2026-11-21 (16 days back, 12 trading sessions)" in s1
    # Saturday: the five sessions end on Friday's session, not the calendar end
    assert "for the last 5 trading sessions (2026-11-16 through 2026-11-20)" in s1
    assert "through 2026-11-21)" not in s1


def test_capped_window_says_30_days_back(base):
    p = _prompt({**base, "news_window": {"from": "2026-10-22", "to": "2026-11-21",
                                         "last_note": "2026-09-01", "capped": True}})
    s1 = _step1(p)
    assert "Window: 2026-10-22 → 2026-11-21 (30 days back, 22 trading sessions)" in s1
    assert "at most 30 days back" in s1 and "(31 days)" not in s1


# ── 11. the INVALID sentence ─────────────────────────────────────────────────

def test_invalid_sentence_only_with_an_invalid_note(tmp_db, monkeypatch):
    p0 = _prompt(_analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA"))
    assert "A note marked INVALID" not in p0
    _fresh_note()                                    # an OK note
    s4 = _step(_prompt(_analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")),
               "4. Analyze", "5. Lay out")
    assert "Carry a fact forward from an OK or STALE note" in s4
    assert "A note marked INVALID" not in s4
    rf.add_discussion("NVDA", "2026-11-20T21:00:00", "Unfinished",
                      research_meta={"prompt_version": 5, "research_status": "partial",
                                     "move_cause": "not_found",
                                     "news_window": "2026-11-16..2026-11-20"})
    s4 = _step(_prompt(_analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")),
               "4. Analyze", "5. Lay out")
    assert "A note marked INVALID is not a basis for anything" in s4


# ── 12. a fund's EDGAR search names the trust ────────────────────────────────

def test_fund_edgar_search_names_the_fund_family(base):
    rv = base["report_view"]
    fields = {**rv["fields"], "fund_category": {
        "label": "Category", "value": "Derivative Income · Example Funds",
        "display": "Derivative Income · Example Funds", "warnings": []}}
    a = {**base, "security_kind": "fund", "report_view": {**rv, "fields": fields}}
    assert rp.fund_family(a) == "Example Funds"
    p = _prompt(a)
    snap, s1 = _block(p, "snapshot"), _step1(p)
    assert ("EDGAR files a fund's N-PORT, 497 and N-CSR under its trust (the fund family), "
            "not its ticker. Full-text search for the window on the family's name (Example "
            "Funds; use the trust's exact name where it differs)") in snap
    assert "q=%22Example+Funds%22" in snap
    assert ("under its trust's name: search EDGAR for the trust or fund family, not the "
            "ticker") in s1
    # family unknown: the ticker query with the instruction to swap in the trust
    assert rp.fund_family({**base, "security_kind": "fund"}) is None
    snap0 = _block(_prompt({**base, "security_kind": "fund"}), "snapshot")
    assert "(put the trust's name in place of the ticker)" in snap0 and "q=%22NVDA%22" in snap0
    _guards(p)


# ── 13. the auto-loaded project file ─────────────────────────────────────────

def test_auto_loaded_project_file_counts_only_when_about_the_users_investing(base):
    task = _step(_prompt(base), "## Your task, in order", "1. Research first")
    assert ("counts as the user's notes only when it is about the user's own investing "
            "(rules, plans, positions); otherwise ignore it.") in task
    assert "counts as the user's notes in the same way" not in task
