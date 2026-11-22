"""Fixes from the NVDA export of 2026-11-22 00:08 (weekend, market closed):

1. ATM implied vol read over a closed market drifted with the clock (115.5%
   Saturday morning, 132.5% after midnight, same ±8.05% straddle).
2. The "reported cause" list led with commentary ("…could reach your 401(k)")
   and the regulatory pass listed headlines whose only match was the blurb.
3. Markdown lists written under a sentence, or nested by 2 spaces, ran
   together in the exported report.
4. The research session could not verify recent closes or the market's move
   for a session; the app has both.
"""
import datetime as dt
import math

from analysis import news_relevance as nr
from analysis import options_iv, vol_bands
from api.report import md, normalize_md
from tests import replay_fixtures as rf
from tests.test_research_prompt_v5 import _analyze, _block, _prompt

FRI_CLOSE = {"price_kind": "official_close", "session_date": "2026-11-20"}
CHAIN = {"available": True, "implied_move_pct": 8.05, "implied_move_dte": 2,
         "expiry_date": "2026-11-23"}


# ── 1. implied vol restated to the close ──────────────────────────────────────

def test_weekend_iv_reads_converge_to_one_value_at_the_close():
    sat = options_iv.restate_to_close({**CHAIN, "atm_iv_pct": 115.5,
                                       "as_of": "2026-11-21T16:22:00+00:00"}, FRI_CLOSE)
    sun = options_iv.restate_to_close({**CHAIN, "atm_iv_pct": 132.5,
                                       "as_of": "2026-11-22T05:08:05+00:00"}, FRI_CLOSE)
    # two broker reads 17% apart are one figure once the clock is taken out
    assert abs(sat["atm_iv_pct"] - sun["atm_iv_pct"]) < 0.5
    assert 98 <= sun["atm_iv_pct"] <= 99.5
    assert sun["atm_iv_raw_pct"] == 132.5
    assert sun["atm_iv_note"].startswith("restated to the Fri Nov 20 close: the broker "
                                         "shows 132.5%")


def test_iv_is_left_alone_during_the_session_and_never_mutates_the_cache():
    live = {**CHAIN, "atm_iv_pct": 80.0, "as_of": "2026-11-20T18:00:00+00:00"}
    assert options_iv.restate_to_close(live, {"price_kind": "live",
                                              "session_date": "2026-11-20"}) is live
    # read at the close itself → nothing to restate
    at_close = {**CHAIN, "atm_iv_pct": 80.0, "as_of": "2026-11-20T21:00:00+00:00"}
    assert options_iv.restate_to_close(at_close, FRI_CLOSE) is at_close
    cached = {**CHAIN, "atm_iv_pct": 132.5, "as_of": "2026-11-22T05:08:05+00:00"}
    out = options_iv.restate_to_close(cached, FRI_CLOSE)
    assert out is not cached and cached["atm_iv_pct"] == 132.5


def test_expiry_falls_back_to_read_date_plus_days_when_the_date_is_missing():
    o = {"implied_move_dte": 1, "as_of": "2026-11-22T05:08:05+00:00"}
    assert options_iv.expiry_close(o).date() == dt.date(2026, 11, 23)


def test_implied_move_spans_trading_sessions_not_calendar_days():
    # a Friday-close straddle to Monday covers ONE session, read Sat or Sun
    assert options_iv.sessions_to_expiry(CHAIN, FRI_CLOSE) == 1
    # Thanksgiving week: Fri close → Fri Nov 27 expiry = Mon, Tue, Wed, Fri
    o = {"expiry_date": "2026-11-27"}
    assert options_iv.sessions_to_expiry(o, FRI_CLOSE) == 4
    # during a live session the current session counts
    assert options_iv.sessions_to_expiry(CHAIN, {"price_kind": "live",
                                                 "session_date": "2026-11-20"}) == 2


def test_report_labels_sessions_and_compares_with_a_band_widened_by_root_n(tmp_db, monkeypatch):
    a = _analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")
    f = a["report_view"]["fields"]
    o = a["options"]
    n = options_iv.sessions_to_expiry(o, a["price_basis"])
    assert f["implied_move"]["label"] == f"Implied move ({n} sessions)"
    assert "expiry " in f["implied_move"]["basis"]
    want = o["implied_move_pct"] / (a["band"]["half_width_pct"] * math.sqrt(n))
    assert abs(f["implied_vs_band"]["value"] - want) < 1e-9


# ── 2. cause candidates and the regulatory pass ──────────────────────────────

def _h(title, day, summary=""):
    ts = dt.datetime(2026, 11, day, 15, 0, tzinfo=dt.timezone.utc).timestamp()
    return {"headline": title, "summary": summary, "ts": ts, "source": "Yahoo"}


NVDA_FEED = [
    _h("Nvidia's new China threat could reach your 401(k)", 21, "export controls probe"),
    _h("Nvidia faces US accounting probe amid deepening turmoil: media", 21),
    _h("Marvell Just Dropped 22% in a Week as Nvidia's Turmoil Hit AI Chips. "
       "Sell, or Load Up Before Dec. 1?", 21, "the SEC is reportedly looking"),
    _h("Nvidia's Worst Losing Streak Since 2008 Now Has an SEC Problem. "
       "What Shareholders Need to Know", 21),
    _h("Nvidia shares tumble 14% after Reuters reports SEC accounting scrutiny", 20),
]


def test_cause_candidates_put_reported_events_before_commentary():
    found = nr.move_cause(NVDA_FEED, [], "2026-11-20", -4.46)["found"]
    heads = [x["headline"] for x in found]
    assert heads[0].startswith("Nvidia shares tumble 14% after Reuters")   # that day, in the headline
    assert heads[1].startswith("Nvidia faces US accounting probe")
    # commentary and blurb-only matches come last
    assert all(x["commentary"] for x in found[-2:])
    assert heads.index("Nvidia's new China threat could reach your 401(k)") > 1


def test_regulatory_pass_needs_the_term_in_the_headline_and_lists_reports_first():
    hits = nr.regulatory_scan(NVDA_FEED, "2026-11-16")
    heads = [x["headline"] for x in hits]
    assert not any(h.startswith("Marvell") for h in heads)       # matched only its blurb
    assert not any("401(k)" in h for h in heads)
    assert heads[-1].startswith("Nvidia's Worst Losing Streak")  # commentary last
    assert [x["commentary"] for x in hits] == [False, False, True]


# ── 3. markdown in the exported report ───────────────────────────────────────

NOTE = ("Summing four quarters, in $B:\n- Q4 FY26: 34.9\n- Q3 FY27: 43.8\n\n"
        "**Hold.** Selling now.\n- **Upgrade if:**\n  - by 2026-12-04, the 10-Q;\n"
        "  - by 2027-02-28, a CEO.\n- **Review by 2026-12-07.**\n\n"
        "| a | b |\n|---|---|\n| - x | 2 |\n\n```\n- code, not a list\n```\n")


def test_lists_under_a_sentence_and_two_space_nesting_render_as_lists():
    html = md(NOTE)
    assert "<li>Q4 FY26: 34.9</li>" in html and "<li>Q3 FY27: 43.8</li>" in html
    assert "<li><strong>Upgrade if:</strong><ul>" in html
    assert "<li>by 2026-12-04, the 10-Q;</li>" in html
    assert "- Q4 FY26" not in html and "- by 2026" not in html
    assert "<table>" in html


def test_normalize_leaves_fences_and_tables_alone():
    out = normalize_md(NOTE)
    assert "```\n- code, not a list\n```" in out
    assert "| - x | 2 |" in out
    assert normalize_md("plain text") == "plain text"


# ── 4. recent sessions in the snapshot ───────────────────────────────────────

def test_recent_sessions_arithmetic():
    bars = [{"date": f"d{i:02d}", "close": 100 + (i % 2)} for i in range(30)]
    spy = [{"date": f"d{i:02d}", "close": 400 + (i % 3)} for i in range(30)]
    adj = [b["close"] for b in bars]
    rs = vol_bands.recent_sessions(bars, adj, spy, 1.5, n=5)
    assert [x["date"] for x in rs] == ["d25", "d26", "d27", "d28", "d29"]
    last = rs[-1]
    chg = (adj[29] / adj[28] - 1) * 100
    spy_chg = (spy[29]["close"] / spy[28]["close"] - 1) * 100
    assert last["change_pct"] == round(chg, 2)
    assert last["spy_change_pct"] == round(spy_chg, 2)
    assert last["net_of_market_pct"] == round(chg - 1.5 * spy_chg, 2)
    sig = vol_bands.realized_vol(adj[:29], 20) / math.sqrt(252)
    assert last["z"] == round(chg / sig, 2)
    # no SPY bar or no beta → the column is left empty, not guessed
    assert vol_bands.recent_sessions(bars, adj, None, 1.5)[-1]["net_of_market_pct"] is None
    assert vol_bands.recent_sessions(bars[:20], adj[:20], spy, 1.5) == []


def test_snapshot_lists_recent_sessions_and_the_task_uses_them(tmp_db, monkeypatch):
    a = _analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")
    rs = a["recent_sessions"]
    assert len(rs) == 10 and rs[-1]["date"] == a["price_basis"]["session_date"]
    assert rs[-1]["z"] == a["move"]["z"]               # the same σ as the day move
    p = _prompt(a)
    snap = _block(p, "snapshot")
    assert "Recent sessions (official close, move, SPY's move, net of market" in snap
    assert "- Fri 2026-11-20: the Day move row above" in snap     # stated once
    assert "- Thu 2026-11-19: $145.98, −4.60% (−0.97σ); SPY −0.90%; net " in snap
    assert ("Daily closes and SPY's move for the last sessions are in <snapshot> "
            "(Recent sessions) — use those rows rather than searching for them.") in p
    step4 = p[p.index("4. Analyze"):p.index("5. Lay out the options")]
    assert "Use the net figure in <snapshot> Recent sessions" in step4


def test_prompt_reads_the_users_own_notes_in_the_working_folder(tmp_db, monkeypatch):
    p = _prompt(_analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA"))
    pre = p[p.index("## Your task, in order"):p.index("1. Research first")]
    assert ("If your working folder holds the user's own notes or rules (for example a "
            "CLAUDE.md or a notes file), read them first") in pre


def test_recall_means_a_product_recall_not_the_verb():
    assert not nr.REG_RE.search("Trump Recalls Helping Elon Musk When He Was 'In Big Trouble'")
    for t in ("Ford recalls 300,000 vehicles", "Product recall hits sales",
              "Company announces recall of two product lines"):
        assert nr.REG_RE.search(t), t


# ── 5. from the 00:27 note: no quarter from memory, no untested trigger ──────

def test_c3_gives_the_filed_quarters_and_asks_for_the_release_balance_sheet(tmp_db, monkeypatch):
    # statements one quarter behind the Nov 17 release, as on Nov 21–22
    a = _analyze(monkeypatch, rf.nvda_providers(fresh_statements=False), rf.NVDA_NOW, "NVDA")
    fq = a["filed_quarters"]
    assert fq and all(q["end"] <= "2026-07-26" for q in fq)     # only periods the filing covers
    assert [q["end"] for q in fq] == sorted((q["end"] for q in fq), reverse=True)
    p = _prompt(a)
    snap = _block(p, "snapshot")
    assert ("- Free cash flow by filed quarter (newest first): 2026-07-26 $21.4B; "
            "2026-04-26 $48.6B; 2026-01-25 $34.9B; 2025-10-26 $22.1B") in snap
    step2 = p[p.index("2. Reconcile the data"):p.index("3. Set and check")]
    assert ("the latest quarter from the release plus the three most recent filed quarters "
            "in <snapshot> (Free cash flow by filed quarter) — no quarter from memory or "
            "from an earlier note") in step2
    assert "Restate net cash and enterprise value from the release's balance sheet" in step2


def test_every_trigger_is_searched_before_it_is_marked(tmp_db, monkeypatch):
    p = _prompt(_analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA"))
    step3 = p[p.index("3. Set and check"):p.index("4. Analyze")]  # any fixture
    assert ("Check every trigger, yours and the user's, with a search or a filing before "
            "you mark it, and name that search; UNKNOWN means the search could not settle "
            "it, not that it was skipped.") in step3


# ── 6. speed: parallel research, filing index, length cap ────────────────────

def test_step1_offers_parallel_subagents_and_the_filing_index_json(tmp_db, monkeypatch):
    a = _analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")
    p = _prompt(a)
    step1 = p[p.index("1. Research first"):p.index("2. Reconcile the data")]
    assert ("if you can start subagents (for example Claude Code's Task tool), run step 1 "
            "as three in parallel and collect all three results") in step1
    assert "keep steps 2 to 8 yourself" in step1
    if a.get("sec_cik"):
        assert f"https://data.sec.gov/submissions/CIK{a['sec_cik']}.json" in step1
    assert "stay under 1,100" in p
