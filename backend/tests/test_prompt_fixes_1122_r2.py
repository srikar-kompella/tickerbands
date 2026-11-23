"""
Research prompt fixes of Nov 22, 2026, round 2 (api/research_prompt.py and
research_quality.older_failed_sessions). Eight test runs on real names listed
where the prompt still did not fit; each test pins one fix: the variants
compose (kind + new listing), the Recent sessions rows cover the window
compactly, a post-earnings move may cite the release, the incremental run is
a short list, every filing form is read, the length budget, the user's rules
stay out of `proposed_triggers`, fund comparisons, regulators by charter,
older optional sessions, the research_status definition, and the INVALID
redo limited to the window. Tickers, dates and figures are illustrative.
"""
import datetime as dt

import pytest

from analysis import market_clock as mc
from analysis import research_quality as rq
from api import research_prompt as rp
from tests import replay_fixtures as rf
from tests.test_prompt_fixes_1122 import _guards, _step, _step1, _new_listing, FOUND, _note
from tests.test_research_prompt_v5 import _analyze, _block, _prompt


@pytest.fixture()
def base(tmp_db, monkeypatch):
    """NVDA replay (Sat 2026-11-21) with fresh statements and no notes."""
    return _analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")


def _trading_days(end: str, n: int) -> list[str]:
    d, out = dt.date.fromisoformat(end), []
    while len(out) < n:
        if mc.is_trading_day(d):
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return out[::-1]


def _rows(end="2026-11-20", n=25):
    return [{"date": d, "close": 100.0 + i, "change_pct": 0.5 if i % 2 else -0.4,
             "z": 0.3 if i % 2 else -0.25, "spy_change_pct": 0.2, "net_of_market_pct": 0.1}
            for i, d in enumerate(_trading_days(end, n))]


# ── 1. the variants compose ──────────────────────────────────────────────────

def test_kind_helpers_read_is_new_listing_with_the_old_fallback():
    assert rp.is_new_listing({"security_kind": "new_listing"})          # key absent
    assert rp.is_new_listing({"security_kind": "fund", "is_new_listing": True})
    assert not rp.is_new_listing({"security_kind": "stock", "is_new_listing": False})
    assert not rp.is_new_listing({"security_kind": "bank"})
    assert rp.base_kind({"security_kind": "new_listing"}) == "stock"
    assert rp.base_kind({"security_kind": "fund", "is_new_listing": True}) == "fund"
    assert rp.kind_words({"security_kind": "fund", "is_new_listing": True}) == \
        "a newly listed fund (an ETF or similar)"
    assert rp.kind_words({"security_kind": "bank", "is_new_listing": True}) == \
        "a newly listed bank stock"
    assert rp.kind_words({"security_kind": "new_listing"}) == "a newly listed stock"


def test_new_fund_gets_fund_steps_and_the_short_history_caveat(base):
    a = {**_new_listing(base), "security_kind": "fund", "is_new_listing": True}
    p = _prompt(a)
    s1, s2 = _step1(p), _step(p, "2. Reconcile the data", "3. Set and check")
    assert "NVDA, a newly listed fund (an ETF or similar) they follow" in p
    assert "d. distributions: the latest declared amount" in s1
    assert "e. the fund against its index — the index named in its prospectus" in s1
    assert "say how many sessions it has traded" in s1
    # no S-1 / lock-up company steps for a fund
    assert "S-1" not in s1 and "lock-up" not in s1
    # the σ caveat and the 5% list stack on top
    assert "The price history is too short for the app to measure a move in σ" in s1
    assert "Sessions in this window that moved 5% or more" in s1
    assert "A fund has no earnings" in s2 and "This fund is newly listed" in s2
    assert "restate them with the proceeds" not in s2 and "share count" not in s2
    _guards(p)


def test_new_bank_gets_bank_terms_and_prospectus_steps_without_ev_restatement(base):
    a = {**_new_listing(base), "security_kind": "bank", "is_new_listing": True}
    p = _prompt(a)
    s1, s2 = _step1(p), _step(p, "2. Reconcile the data", "3. Set and check")
    assert "NVDA, a newly listed bank stock they follow" in p
    assert "primary federal regulator, set by its charter" in s1
    assert "a matter opened before the window that is still live counts" in s1
    assert ("d. the prospectus — the S-1 and the final 424B on EDGAR — for the latest "
            "financials (balance sheet, net interest margin, credit quality, capital ratios)") in s1
    assert "e. the first lock-up expiry" in s1
    assert "This is a bank, so enterprise value, net cash and P/FCF" in s2
    assert "This is a new listing: say which share count your market cap uses" in s2
    assert "restate them with the proceeds" not in s2
    assert "Restate net cash and enterprise value" not in s2
    _guards(p)


def test_new_listing_falls_back_to_security_kind_when_the_flag_is_absent(base):
    a = _new_listing(base)
    a.pop("is_new_listing")
    p = _prompt(a)
    assert "NVDA, a newly listed stock they follow" in p
    assert "d. the prospectus — the S-1 and the final 424B" in _step1(p)


# ── 2. Recent sessions: compact rows over the whole window ───────────────────

def test_recent_rows_show_the_last_five_and_every_listed_session(base):
    rows = _rows()
    i = next(k for k, x in enumerate(rows) if x["date"] == "2026-10-29")
    rows[i].update(change_pct=-6.0, z=-2.5, net_of_market_pct=-5.5)
    a = {**base, "recent_sessions": rows,
         "news_window": {"from": "2026-10-23", "to": "2026-11-21"},
         "big_move_sessions": base["big_move_sessions"]
         + [{"session_date": "2026-10-29", "z": -2.5, "move_pct": -6.0}]}
    p = _prompt(a)
    snap, s1 = _block(p, "snapshot"), _step1(p)
    assert "- Thu 2026-10-29: $" in snap                      # listed → its own row
    for d in _trading_days("2026-11-19", 4):                  # the last 5 (with Nov 20)
        assert f" {d}: $" in snap, d
    assert "- 2026-10-29 (Thu): its row under Recent sessions in <snapshot>" in s1
    summary = next(ln for ln in snap.splitlines() if " other sessions " in ln)
    assert summary.startswith(f"- 19 other sessions {rows[0]['date']} to 2026-11-13, "
                              "not listed in step 1: moves −0.40% to +0.50%, largest 0.30σ")
    assert "- Tue 2026-10-27: $" not in snap
    # 25 rows cost less than the 10 rows used to
    data = len(_block(p, "data_caveats")) + len(snap)
    assert data < len(_block(_prompt(base), "data_caveats")) + len(_block(_prompt(base), "snapshot")) + 300
    assert "has no row" not in s1
    _guards(p)


def test_listed_session_without_a_row_is_named_instead_of_claiming_rows(base):
    a = {**base, "news_window": {"from": "2026-10-23", "to": "2026-11-21"},
         "big_move_sessions": base["big_move_sessions"]
         + [{"session_date": "2026-10-29", "z": -2.5, "move_pct": -6.0}]}
    p = _prompt(a)
    s1, s4 = _step1(p), _step(p, "4. Analyze", "5. Set the verdict")
    assert "- 2026-10-29 (Thu): −6.00%, −2.50σ" in s1
    assert ("2026-10-29 has no row, so take its close and the S&P 500's move from a dated "
            "source.") in s1
    assert "(for 2026-10-29, which has no row, use the S&P 500's move that day" in s4
    assert "use those rows" not in p


# ── 3. post-earnings moves may cite the release ──────────────────────────────

def test_session_after_the_results_release_may_cite_the_release(base):
    filings = [{"date": "2026-11-19", "form": "8-K", "items": "2.02,9.01",
                "desc": "2.02 results of operations", "url": "https://example.test/8k"}]
    p = _prompt({**base, "sec_filings": filings, "sec_cik": "0000000002"})
    s1 = _step1(p)
    assert ("- 2026-11-20 (Fri): the Day move row in <snapshot> — on or right after the "
            "results release of 2026-11-19") in s1
    assert ("the release may be the primary source of the cause, plus one other source, or "
            "the release alone where no coverage exists") in s1
    assert '"reported cause: the results release of <date>"' in s1
    # no release in the list → no marker; the rule still covers a release the
    # session finds itself (ISTR: the 8-K predated the app's list); a fund has none
    s1 = _step1(_prompt(base))
    assert "— on or right after the results release" not in s1
    assert ("A listed session on or right after the company's own results release is the "
            "market's reaction to it") in s1
    assert "results release" not in _step1(_prompt({**base, "security_kind": "fund"}))
    assert "results release" not in _step1(_prompt(_new_listing(base)))   # none published yet
    assert rp.after_release("2026-11-23", ["2026-11-20"]) == "2026-11-20"   # Fri → Mon
    assert rp.after_release("2026-11-24", ["2026-11-20"]) is None
    _guards(p)


# ── 4. incremental run: a short list ─────────────────────────────────────────

def test_incremental_run_is_news_filings_and_open_conditions(tmp_db, monkeypatch):
    from tests.test_prompt_fixes_1122 import _fresh_note
    _fresh_note()
    p = _prompt(_analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA"))
    s1 = _step1(p)
    assert "   b. filings since 2026-11-21T09:00: any row of the filing list" in s1
    assert ("   c. each open condition listed in <prior_notes>: one search or filing each, "
            "since 2026-11-21T09:00 — except one that cannot resolve before a future date, "
            "which is PENDING with that date and needs no search.") in s1
    assert ("Run the legal and regulator searches again only if a or b turns up a new "
            "headline or filing about the company.") in s1
    assert "state attorneys general" not in s1 and "d. the latest earnings release" not in s1
    assert "This list is short: run it as one parallel batch" in s1
    assert "run step 1 as three in parallel" not in s1
    out = p[p.index("## Output contract"):]
    assert "(on this incremental run, the short list since 2026-11-21T09:00)" in out
    _guards(p)


# ── 5. every filing form is read ─────────────────────────────────────────────

def test_wide_filing_list_is_read_whole(base):
    filings = [{"date": "2026-11-18", "form": "4", "desc": "4", "url": "https://example.test/4"},
               {"date": "2026-11-17", "form": "NT 10-Q", "desc": "NT 10-Q", "red_flag": True,
                "url": "https://example.test/nt"}]
    p = _prompt({**base, "sec_filings": filings, "sec_cik": "0000000002"})
    snap, s1 = _block(p, "snapshot"), _step1(p)
    assert "SEC filings since 2026-11-16 (EDGAR's index as the app fetched it, newest first)" in snap
    assert "8-K, 10-Q and 10-K only" not in snap
    assert "- 2026-11-17 NT 10-Q (red-flag item)" in snap
    assert "an NT 10-Q or NT 10-K is a late filing, a red flag to explain" in s1
    assert "a Form 4 or 144 is an insider's trade or planned sale" in s1
    assert "(Form 4 and 144, NT 10-Q and NT 10-K" not in s1        # they are in the list


def test_same_day_insider_forms_share_a_row_and_a_cut_list_says_so(base):
    many = [{"date": "2026-11-12", "form": "144", "desc": "planned insider sale (Form 144)",
             "url": f"https://example.test/144-{i}"} for i in range(4)]
    many += [{"date": f"2026-11-{d:02d}", "form": "8-K", "items": "8.01", "desc": "8.01 other events",
              "url": f"https://example.test/8k-{d}"} for d in range(1, 13)]
    p = _prompt({**base, "sec_filings": many, "sec_cik": "0000000002"})
    snap, s1 = _block(p, "snapshot"), _step1(p)
    assert ("- 2026-11-12 144 ×4 — planned insider sale (Form 144) · https://example.test/144-0 "
            "(the other 3 are on EDGAR)") in snap
    assert snap.count(" 144 ") == 1
    assert f"- and {13 - rp.FILINGS_SHOWN} older, not listed (on EDGAR, back to 2026-11-01)" in snap
    assert "check EDGAR as well for the older ones the list cuts off" in s1


def test_fund_filing_list_replaces_the_full_text_search_line(base):
    filings = [{"date": "2026-11-18", "form": "N-PORT-P", "desc": "N-PORT-P",
                "url": "https://example.test/nport"}]
    p = _prompt({**base, "security_kind": "fund", "sec_filings": filings})
    snap, s1 = _block(p, "snapshot"), _step1(p)
    assert "- 2026-11-18 N-PORT-P · https://example.test/nport" in snap
    assert "Fund filings: not fetched by the app" not in snap
    assert "b. the fund's filings listed in <snapshot>" in s1
    _guards(p)


# ── 6 & 7. length, triggers, notes the notes point to ────────────────────────

def test_length_budget_triggers_and_referenced_notes(base):
    p = _prompt(base)
    out = p[p.index("## Output contract"):p.index("```markdown")]
    assert "about 1,100 words excluding tables and stay under 1,300" in out
    assert '"Searched, nothing found: X; Y; Z"' in out
    assert ("`proposed_triggers` holds only the triggers you set in step 3, not the user's "
            "own rules.") in out
    s3 = _step(p, "3. Set and check", "4. Analyze")
    assert "(frontmatter `proposed_triggers`, which holds only the triggers you set)" in s3
    assert ("The user's rules are checked under `## Triggers, checked` and not copied into "
            "`proposed_triggers`.") in s3
    task = _step(p, "## Your task, in order", "1. Research first")
    assert "a file in the same folder that those notes point to (for example a worklog" in task


# ── 8. funds ─────────────────────────────────────────────────────────────────

def test_fund_index_fallback_and_trailing_yield(base):
    p = _prompt({**base, "security_kind": "fund"})
    s1, s2 = _step1(p), _step(p, "2. Reconcile the data", "3. Set and check")
    assert "the index named in its prospectus or fact sheet" in s1
    assert ("compare the fund's price change with the price change of an ETF that tracks that "
            "index over the same days, and say the comparison is price-based") in s1
    assert ("The trailing yield at the report price rises mechanically when the price falls: "
            "compare it with the issuer's stated distribution rate") in s2


# ── 9. regulators ────────────────────────────────────────────────────────────

def test_regulator_searches_are_searches_and_banks_follow_the_charter(base):
    s1 = _step1(_prompt(base))
    assert "search for enforcement actions by the company's main regulator" in s1
    s1 = _step1(_prompt({**base, "security_kind": "bank"}))
    assert "enforcement-action lists" not in s1 and "state banking regulator" not in s1
    assert ("the OCC for a national bank (\"N.A.\" or \"National\" in its name), the Federal "
            "Reserve for a state bank that is a Fed member, the FDIC for one that is not") in s1


# ── 10 & 12. older sessions; INVALID redo limited to the window ──────────────

def test_older_failed_sessions_from_an_invalid_note():
    basis = {"session_date": "2026-11-20", "price_kind": "official_close"}
    move = {"z": 0.3, "move_pct": 0.5}
    sessions = [{"session_date": "2026-09-02", "z": -2.41, "move_pct": -3.1},
                {"session_date": "2026-10-22", "z": -2.2, "move_pct": -2.9}]
    notes = [_note("2026-09-03T09:00:00", prompt_version=5, research_status="complete",
                   move_cause="na", news_window="2026-08-27..2026-09-03"),     # INVALID
             _note("2026-10-23T09:00:00", **FOUND, news_window="2026-10-16..2026-10-23")]
    assert rq.prior_note_status(notes, move, basis, sessions)[0]["status"] == rq.INVALID
    got = rq.older_failed_sessions(notes, move, basis, sessions, "2026-10-23")
    assert [s["session_date"] for s in got] == ["2026-09-02"]
    # a later sound note that explains it, or no window start → nothing
    assert rq.older_failed_sessions(notes, move, basis, sessions, None) == []
    notes.append(_note("2026-09-04T09:00:00", **FOUND, news_window="2026-08-28..2026-09-04"))
    assert rq.older_failed_sessions(notes, move, basis, sessions, "2026-10-23") == []


def test_invalid_redo_is_limited_to_the_window_and_older_line_wording(base, monkeypatch):
    rf.add_discussion("NVDA", "2026-11-10T09:00:00", "First pass",
                      research_meta={"prompt_version": 5, "research_status": "partial"})
    monkeypatch.setattr(rq, "older_failed_sessions",
                        lambda *a, **k: [{"session_date": "2026-09-02", "z": -2.41,
                                          "move_pct": -3.1}])
    p = _prompt(base)
    s1, s4 = _step1(p), _step(p, "4. Analyze", "5. Set the verdict")
    assert ("redo its work for the sessions inside this window and list its created_ts in "
            "`supersedes`; its failed checks on sessions before the window are not redone "
            "here — step 1 lists them under older sessions still unexplained.") in s4
    line = next(ln for ln in s1.splitlines() if ln.startswith("   Older sessions"))
    assert "2026-09-02 (−3.10%, −2.41σ)" in line
    assert "one opened source is enough" in line
    assert 'a cause seen only in a search-result headline is written "not verified"' in line
    _guards(p)


# ── 11. research_status counts the 5% sessions of a new listing ──────────────

def test_research_status_counts_large_moves_of_a_new_listing(base):
    out = _prompt(_new_listing(base))
    out = out[out.index("## Output contract"):]
    assert ("every 2σ session listed in step 1 — and every 5% session listed there for want "
            "of a σ, which counts the same way — has its cause") in out
    plain = _prompt(base)
    assert "for want of a σ" not in plain[plain.index("## Output contract"):]
