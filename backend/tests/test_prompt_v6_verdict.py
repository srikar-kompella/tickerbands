"""
Research prompt v6 — the verdict (docs/VERDICT_SPEC.md, api/research_prompt.py).

The headline is an ownership-independent verdict set by the session from the
rulebook the prompt carries: value grade from `valuation_range` (or named
peers), risk items sorted hard / soft / watch-only, the security kind's own
default rules, fixed steps to the verdict. These tests pin the version, the
template keys, the three examples (strict YAML, the real parser, the rulebook
arithmetic), the per-kind rule lists, the watch-only wording, the absence of
stance words, the "Valuation vs own history" group with and without the
payload key, the trust labels in <prior_notes>, the style guards on every
variant and the snapshot length. Tickers, dates and figures are fictional or
illustrative.
"""
import json
import re

import frontmatter
import pytest

from api import research_prompt as rp
from tests import replay_fixtures as rf
from tests.test_prompt_fixes_1122 import _guards
from tests.test_research_prompt_v5 import (V4_SNAPSHOT_CHARS, V6_KEYS, V6_TEMPLATE_KEYS,
                                           _analyze, _block, _examples, _prompt, _template)


@pytest.fixture()
def base(tmp_db, monkeypatch):
    """NVDA replay (Sat 2026-11-21), owned, fresh statements, no notes."""
    return _analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")


def _no_range(a):
    return {k: v for k, v in a.items() if k != "valuation_range"}


VR = {"measure": "p_fcf", "label": "Price to free cash flow", "now": 31.4,
      "now_basis": "report price; TTM through 2026-10-26",
      "points": [{"fy_end": "2026-01-25", "value": 38.2}, {"fy_end": "2025-01-26", "value": 45.1},
                 {"fy_end": "2024-01-28", "value": 71.0}, {"fy_end": "2023-01-29", "value": 30.2},
                 {"fy_end": "2022-01-30", "value": 52.6}],
      "median": 45.1, "low": 30.2, "high": 71.0, "grade_hint": "cheap",
      "normalized": False, "warnings": [], "status": "ok"}


def _task(p):
    return p[p.index("## Your task, in order"):p.index("<examples>")]


def _step5(p):
    return p[p.index("5. Set the verdict"):p.index("6. Lay out the options")]


# ── version, template, headings ──────────────────────────────────────────────

def test_v6_version_template_keys_and_headings(base):
    p = _prompt(base)
    assert rp.PROMPT_VERSION == 6
    meta = frontmatter.loads(_template(p)).metadata           # strict YAML
    assert meta["prompt_version"] == 6
    assert set(meta) == V6_TEMPLATE_KEYS
    assert "stance" not in meta and "stance_note" not in meta
    assert meta["verdict"] == "<attractive|fair|stretched|avoid|unrated>"
    assert meta["value_grade"] == "<cheap|fair|expensive|paused>"
    assert meta["risk_grade"] == "<clean|watch|red_flag>"
    assert rp.BODY_HEADINGS[6] == "## Verdict & what would change it"
    assert "## Your options" in rp.BODY_HEADINGS
    out = p[p.index("## Output contract"):p.index("```markdown")]
    assert "`## Verdict & what would change it`" in out and "Stance &" not in p
    assert "first the verdict as the app shows it, `Verdict (Value · Risk)`" in out


# ── the three examples ───────────────────────────────────────────────────────

SOFT_PERIODS = re.compile(r"(Q\d \d{4}.*Q\d \d{4}|10-Q|10-K|8-K|release|filing)")


def _meta(ex):
    return frontmatter.loads(ex).metadata


def test_v6_examples_are_strict_yaml_fictional_and_follow_the_rulebook(base):
    p = _prompt(base)
    exs = _examples(p)
    assert len(exs) == 3
    for ex in exs:
        m = _meta(ex)                                  # strict loader — no salvage path
        assert m["ticker"].startswith("ZZ") and str(m["created_ts"]).startswith("2031-")
        assert m["prompt_version"] == 6 and "Fictional" in m["summary"]
        assert "stance" not in m and "stance_note" not in m
        assert {"verdict", "value_grade", "risk_grade", "value_basis", "verdict_note"} <= set(m)
        assert set(m) - V6_TEMPLATE_KEYS == set(), set(m) - V6_TEMPLATE_KEYS
        assert "<" not in ex.split("---")[1]
        hard, soft, watch = (m.get(k) or [] for k in ("hard_flags", "soft_signals", "watch_items"))
        for k in ("hard_flags", "soft_signals", "watch_items"):
            assert k not in m or m[k], f"empty {k} left in"          # deleted when empty
        # the fixed steps of §4 and the risk grade of §8
        assert m["verdict"] == rp.expected_verdict(m["value_grade"], len(hard), len(soft),
                                                   m["research_status"]), m["ticker"]
        assert m["risk_grade"] == rp.expected_risk(len(hard), len(soft), len(watch)), m["ticker"]
        for s in soft:
            assert SOFT_PERIODS.search(s), s                    # names its periods or filing
        if m["verdict"] != "unrated":
            assert m["upgrade_if"] and m["downgrade_if"] and m["review_by"]
    owned, not_owned, failed = (_meta(x) for x in exs)
    # owned: two soft signals drop a fair value grade one step
    assert owned["owned"] == "yes" and owned["value_grade"] == "fair"
    assert len(owned["soft_signals"]) == 2 and owned["verdict"] == "stretched"
    assert "hard_flags" not in owned and owned["owner_note"]
    assert owned["plan_triggers_met"]
    assert exs[0].split("---", 2)[2].strip().startswith("## TL;DR\nStretched (Fair · Watch).")
    # not owned: a watch-only item does not lower the verdict
    assert not_owned["owned"] == "no" and not_owned["value_grade"] == "fair"
    assert not_owned["watch_items"] and "soft_signals" not in not_owned
    assert not_owned["verdict"] == "fair" and not_owned["risk_grade"] == "watch"
    assert "unnamed sources" in not_owned["watch_items"][0]
    assert "does not lower the verdict" in not_owned["verdict_note"]
    # failed research: unrated, verdict_note starting "research incomplete"
    assert failed["research_status"] == "failed" and failed["verdict"] == "unrated"
    assert failed["verdict_note"].startswith("research incomplete")
    assert failed["sources_count"] == 0
    intro = p[p.index("Three filled examples"):p.index("<examples>")]
    assert ('research_status "failed", verdict "unrated", verdict_note starting '
            '"research incomplete"') in intro
    assert "two soft signals drop a fair value grade one step" in intro
    assert "a watch-only item leaves the verdict at fair" in intro


def test_rulebook_arithmetic_helpers():
    ev = rp.expected_verdict
    assert [ev(g, 0, 0) for g in ("cheap", "fair", "expensive")] == \
        ["attractive", "fair", "stretched"]
    assert ev("cheap", 0, 1) == "attractive"                        # one soft: shown only
    assert [ev(g, 0, 2) for g in ("cheap", "fair", "expensive")] == \
        ["fair", "stretched", "avoid"]
    assert ev("cheap", 0, 3) == "avoid" and ev("cheap", 1, 0) == "avoid"
    assert ev("paused", 0, 0) == "unrated" and ev("cheap", 0, 0, "failed") == "unrated"
    assert ev("cheap", 0, 0, new_listing_cap=True) == "fair"
    er = rp.expected_risk
    assert (er(1, 0, 0), er(0, 3, 0), er(0, 2, 0), er(0, 1, 1), er(0, 0, 2), er(0, 1, 0)) == \
        ("red_flag", "red_flag", "watch", "watch", "watch", "clean")


def _parse(tmp_path, ex):
    from discussions_svc.parser import parse_discussion_file
    m = _meta(ex)
    d = tmp_path / m["ticker"]
    d.mkdir(exist_ok=True)
    f = d / "example.md"
    f.write_text(ex)
    row = parse_discussion_file(str(f))
    return row, json.loads(row["research_meta"])


def _v(row, meta, key):
    return row.get(key) if row.get(key) is not None else meta.get(key)


def test_v6_examples_go_through_the_real_parser(base, tmp_path):
    """Depends on the parser reading the v6 keys (VERDICT_SPEC §7): each
    example parses strictly, nothing is unreadable, the verdict keys are
    stored, and the legacy stance is derived (attractive → buy, fair → hold,
    stretched → trim, avoid → sell, unrated → watch)."""
    derived = {"attractive": "buy", "fair": "hold", "stretched": "trim", "avoid": "sell",
               "unrated": "watch"}
    for ex in _examples(_prompt(base)):
        m = _meta(ex)
        row, meta = _parse(tmp_path, ex)
        assert "parse_note" not in meta and "unreadable" not in meta, meta
        for k in ("verdict", "value_grade", "risk_grade", "value_basis", "verdict_note"):
            assert _v(row, meta, k) == m[k], k
        for k in ("hard_flags", "soft_signals", "watch_items"):
            assert (_v(row, meta, k) or []) == (m.get(k) or []), k
        if m.get("owner_note"):
            assert _v(row, meta, "owner_note") == m["owner_note"]
        assert row["stance"] == derived[m["verdict"]], m["ticker"]
        assert meta["review_by"] == m["review_by"]


def test_v6_unfilled_template_leaves_no_placeholder(base, tmp_path):
    """Depends on the parser dropping `<…>` slots for the v6 keys as it does
    for the v5 ones."""
    from discussions_svc.parser import parse_discussion_file
    (tmp_path / "NVDA").mkdir()
    f = tmp_path / "NVDA" / "template.md"
    f.write_text(_template(_prompt(base)))
    row = parse_discussion_file(str(f))
    meta = json.loads(row["research_meta"])
    assert "parse_note" not in meta and meta["prompt_version"] == 6
    for k in V6_KEYS:
        assert _v(row, meta, k) in (None, [], ""), k
    assert row["stance"] is None
    assert "<" not in json.dumps({k: v for k, v in row.items() if k != "response_markdown"})


# ── the rulebook in step 5 ───────────────────────────────────────────────────

SOFT_MARK = {"stock": "`receivables_vs_revenue` receivables growth year on year more than 20 points",
             "fund": "`nav_vs_index` NAV total return trailing its index",
             "bank": "`npl_rise` nonperforming loans up more than 50%"}
HARD_MARK = {"fund": "the fund's closure, liquidation or merger",
             "bank": "capital below \"well capitalized\"",
             "stock": "for a REIT, a dividend cut"}


@pytest.mark.parametrize("kind", ["stock", "fund", "bank"])
def test_each_kind_shows_only_its_own_rule_list(base, kind):
    s5 = _step5(_prompt({**base, "security_kind": kind}))
    assert rp.HARD_COMMON in s5                          # the universal hard list, always
    for k, mark in SOFT_MARK.items():
        assert (mark in s5) == (k == kind), (kind, mark)
    for k, mark in HARD_MARK.items():
        assert (mark in s5) == (k == kind), (kind, mark)
    assert f"Default rules for a {kind} — soft signals, each with its rule key: " in s5
    assert f"Main measure: {rp.VALUE_MEASURE[kind]}." in s5
    assert ("holding period" in s5) == (kind == "fund")
    assert "no better than fair" not in s5               # not a new listing


def test_new_listing_caps_the_verdict_and_grades_on_peers(base):
    s5 = _step5(_prompt({**base, "security_kind": "stock", "is_new_listing": True,
                         "valuation_range": VR}))
    # a usable own-history range is graded on (spec §3: peers with < 3 year-ends)
    assert "<snapshot> gives it under Valuation vs own history, with the app's grade" in s5
    assert ("(6) last, as a listing under a year old, no better than fair until one quarter "
            "of results as a public company is filed") in s5
    thin = _step5(_prompt({**base, "security_kind": "stock", "is_new_listing": True}))
    assert "This is a new listing with too little history of its own: grade against named peers" in thin
    assert "a lock-up expiry within 30 days and the first earnings date are watch items" in s5
    # a new fund keeps the fund rules and gets no results cap
    fund = _step5(_prompt({**base, "security_kind": "fund", "is_new_listing": True}))
    assert SOFT_MARK["fund"] in fund and "no better than fair" not in fund


def test_watch_only_items_cannot_lower_the_verdict_and_the_gate_is_stated(base):
    s5 = _step5(_prompt(base))
    assert ("Unconfirmed reports and open investigations are watch-only: they cannot lower "
            "the verdict and do not count toward a red flag.") in s5
    assert "(even confirmed ones — an investigation is not a finding)" in s5
    assert "it describes the security, not the user's position" in s5
    assert "the user's own rules do not change it" in s5
    # data-quality gate and persistence
    assert "A rule cannot fire on a figure the snapshot withholds or flags" in s5
    assert "fewer than four quarters of history" in s5
    assert ("A soft signal from financial data must hold for two consecutive quarters; from "
            "market data, five sessions.") in s5
    # the fixed steps
    assert ("(1) research_status \"failed\" or a required search not run (a hard flag could be "
            "missing), with no hard flag → unrated; (2) any hard flag → avoid, even when the "
            "research failed or the value grade is paused; (3) three or more soft signals → "
            "avoid; (4) value grade paused, or too little clean data → unrated; (5) the "
            "baseline") in s5
    assert "exactly two soft signals → one step below it." in s5
    assert ("`risk_grade`: red_flag with any hard flag or three or more soft signals; watch "
            "with two soft signals, or with one or none and at least one watch item; clean "
            "otherwise.") in s5
    # the user's rules → owner_note, never the verdict
    assert "write `owner_note`: one line saying what those rules imply and whether they " \
           "override the default meaning of the verdict" in s5
    assert ("Where the user's notes give a rule for the same thing, theirs is the one reported "
            "in `owner_note`; the default still drives the verdict.") in s5
    s7 = _task(_prompt(base))
    s7 = s7[s7.index("7. Check your work"):s7.index("8. Write the file")]
    assert ("`verdict` follows the fixed steps of 5d from `value_grade` and the counts of "
            "`hard_flags` and `soft_signals`") in s7
    assert ("no unconfirmed report or open investigation sits in `hard_flags` or "
            "`soft_signals`, and each soft signal names its two periods or its filing, and "
            "starts with its rule key in square brackets") in s7


@pytest.mark.parametrize("branch", ["owned", "not_owned", "unknown"])
def test_no_stance_words_left_in_the_task_text(base, branch):
    a = dict(base)
    if branch == "not_owned":
        a["position"] = {"owned": False}
    elif branch == "unknown":
        a["position"] = {}
    text = _task(_prompt(a))
    low = text.replace("stance_horizon", "").replace("stance_confidence", "").lower()
    assert "stance" not in low, low[max(0, low.find("stance") - 80):low.find("stance") + 80]
    for gone in ("Buy / Watch / Pass", "watch only when", "sell means avoid", "buy (= add)"):
        assert gone not in text, gone
    s6 = text[text.index("6. Lay out the options"):text.index("7. Check your work")]
    assert "\"verdict supports it\"" in s6 and "\"your rules\"" in s6
    assert ("Attractive: Add / Buy; Fair: Hold / Wait; Stretched: Trim / Pass; Avoid: Exit / "
            "Pass (owned / not owned); Unrated: do not act on it") in s6
    rows = {"owned": "the rows are Hold / Add / Trim / Exit.",
            "not_owned": "the rows are Buy / Wait / Pass.",
            "unknown": "if owned: Hold / Add / Trim / Exit; if not owned: Buy / Wait / Pass"}
    assert rows[branch] in s6


# ── Valuation vs own history ─────────────────────────────────────────────────

def _group(p):
    s = _block(p, "snapshot")
    if "\nValuation vs own history — " not in s:
        return None
    g = s[s.index("\nValuation vs own history — ") + 1:]
    return g.split("\n\n")[0]


def test_valuation_group_renders_from_the_payload(base):
    p = _prompt({**base, "valuation_range": VR})
    g = _group(p)
    assert g == ("Valuation vs own history — Price to free cash flow\n"
                 "- now 31.4x (TTM through 2026-10-26); year-ends, newest "
                 "first: 2026-01-25 38.2x, 2025-01-26 45.1x, 2024-01-28 71.0x, 2023-01-29 30.2x, "
                 "2022-01-30 52.6x\n"
                 "- median 45.1x, low 30.2x, high 71.0x; app grade: cheap"), g
    assert "Valuation vs own history" not in _block(p, "snapshot").splitlines()[-1]
    s5 = _step5(p)
    assert "<snapshot> gives it under Valuation vs own history, with the app's grade" in s5
    assert "grade against named peers" not in s5


def test_valuation_group_states_both_grades_when_normalized(base):
    vr = {**VR, "now": 20.0, "grade_hint": "cheap", "normalized": True,
          "now_normalized": 52.0, "normalized_grade_hint": "fair",
          "warnings": ["current free cash flow is 2.6× its five-year average; on that average "
                       "the measure is 52.0x instead of 20.0x — if this is a cycle peak, the "
                       "normalized grade (fair) is the better guide; if the business has "
                       "changed, the reported grade (cheap) is"]}
    p = _prompt({**base, "valuation_range": vr})
    g = _group(p)
    assert ("- median 45.1x, low 30.2x, high 71.0x; app grades: cheap on the reported "
            "figure, fair on the five-year average (52.0x) → fair (the less favorable)") in g, g
    s5 = _step5(p)
    assert ("take the less favorable of the two; depart from it only with a stated reason in "
            "`value_basis`") in s5
    assert "Choose one" not in s5 and "grade on the average" not in s5


def test_valuation_now_equal_to_a_snapshot_row_is_stated_once(base):
    pf = base["report_view"]["fields"]["p_fcf"]
    g = _group(_prompt({**base, "valuation_range": {**VR, "now": pf["value"]}}))
    assert "- now: the P/FCF (TTM) row above; year-ends" in g
    assert pf["display"] not in g


def test_valuation_group_absent_withheld_or_thin_falls_back_to_peers(base):
    p = _prompt(_no_range(base))
    assert _group(p) is None
    last = _block(p, "snapshot").splitlines()[-1]
    assert last.startswith("Not available in this snapshot: ") and "Valuation vs own history" in last
    peers = ("<snapshot> has no usable range of its own history, so grade against named peers "
             "from your research (name each, with its figure and source): cheap = at least 25% "
             "below the peer median, expensive = at least 25% above it, fair otherwise, and say "
             "so in `value_basis`.")
    assert peers in _step5(p)
    # fewer than three fiscal year-ends: shown, but graded on peers
    thin = _prompt({**base, "valuation_range": {**VR, "points": VR["points"][:2]}})
    assert _group(thin) and peers in _step5(thin)
    # withheld: the reason is shown once, and paused is named for an unreconciled ADR
    w = _prompt({**base, "valuation_range": {"measure": "p_fcf", "label": "Price to free cash flow",
                                             "status": "withheld", "now": None, "points": [],
                                             "warnings": ["share count and currency unreconciled"]}})
    assert _group(w) == ("Valuation vs own history — Price to free cash flow\n"
                         "- withheld by the app: share count and currency unreconciled")
    assert "value_grade is paused" in _step5(w) and "grade against named peers" in _step5(w)
    # junk in the payload is read defensively
    for junk in ("n/a", 5, {"now": "x", "points": [{"fy_end": None}, "y"]}, {"status": "weird"}):
        p = _prompt({**base, "valuation_range": junk})
        assert peers in _step5(p)
        assert "None" not in p and not re.search(r"\bnan\b", p)


def test_fund_and_bank_ranges_use_their_units(base):
    fund = _group(_prompt({**base, "security_kind": "fund", "valuation_range": {
        **VR, "measure": "distribution_rate", "label": "Distribution rate", "now": 12.1,
        "median": 13.0, "low": 11.2, "high": 14.4, "grade_hint": "fair",
        "points": [{"fy_end": "2025-12-31", "value": 13.0}]}}))
    assert "- now 12.1% (" in fund and "median 13.0%, low 11.2%, high 14.4%" in fund


def test_snapshot_length_with_the_valuation_group(tmp_db, monkeypatch):
    """The v5 length budget holds with the new group (NVDA, statements lagging,
    the replay the budget was set on)."""
    from tests.test_research_prompt_v5 import _seed_nvda_notes
    _seed_nvda_notes()
    a = _analyze(monkeypatch, rf.nvda_providers(fresh_statements=False), rf.NVDA_NOW, "NVDA")
    p = _prompt({**a, "valuation_range": VR})
    data = len(_block(p, "data_caveats")) + len(_block(p, "snapshot"))
    assert data < 0.75 * V4_SNAPSHOT_CHARS, data


# ── <prior_notes>: verdict and trust labels ──────────────────────────────────

def test_prior_notes_show_the_verdict_and_the_trust_label(tmp_db, monkeypatch):
    found = {"prompt_version": 6, "research_status": "complete", "move_cause": "found",
             "price_used": 125.53, "review_by": "2027-01-15", "stance_confidence": "low",
             "verdict": "stretched", "value_grade": "fair", "risk_grade": "watch",
             "upgrade_if": ["x"], "downgrade_if": ["y"],
             "news_window": "2026-11-16..2026-11-21"}
    rf.add_discussion("NVDA", "2026-11-21T08:00:00", "First pass", stance="hold")
    rf.add_discussion("NVDA", "2026-11-21T09:00:00", "Verdict note", stance="trim",
                      research_meta={**found, "supersedes": ["2026-11-21T08:00:00"]})
    a = _analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")
    n = _block(_prompt(a), "prior_notes")
    assert ('- 2026-11-21T09:00:00 · verdict: Stretched (Fair · Watch) · "Verdict note" · '
            "trust: Current") in n
    assert ('- 2026-11-21T08:00:00 · earlier-format stance: hold · "First pass" · trust: '
            "replaced — replaced by the note of 2026-11-21T09:00") in n
    assert "STATUS" not in n and "SUPERSEDED" not in n and " OK" not in n
    assert n.splitlines()[0].startswith("Each note is true only as of its date. Its trust "
                                        "label says whether it can still be relied on")


def test_trust_label_read_from_the_status_row_with_a_fallback(tmp_db, monkeypatch):
    assert rp.trust_of({"status": "OK"}) == "current"
    assert rp.trust_of({"status": "STALE"}) == "out_of_date"
    assert rp.trust_of({"status": "INVALID"}) == "dont_use"
    assert rp.trust_of({"status": "SUPERSEDED"}) == "replaced"
    assert rp.trust_of({"status": "OK", "trust": "Out_Of_Date"}) == "out_of_date"
    assert rp.trust_of({"status": "INVALID", "trust": "bogus"}) == "dont_use"
    # the status row's own `trust` wins where the app supplies one
    from analysis import research_quality as rq
    real = rq.prior_note_status
    monkeypatch.setattr(rq, "prior_note_status",
                        lambda *a, **k: [{**s, "trust": "out_of_date"} for s in real(*a, **k)])
    rf.add_discussion("NVDA", "2026-11-21T09:00:00", "A note")
    n = _block(_prompt(_analyze(monkeypatch, rf.nvda_providers(), rf.NVDA_NOW, "NVDA")),
               "prior_notes")
    assert '"A note" · trust: Out of date' in n


def test_note_verdict_reads_columns_or_research_meta():
    assert rp.note_verdict({"verdict": "Fair", "research_meta": {"value_grade": "cheap",
                                                                 "risk_grade": "clean"}}) == \
        "verdict: Fair (Cheap · Clean)"
    assert rp.note_verdict({"research_meta": {"verdict": "unrated"}}) == "verdict: Unrated"
    assert rp.note_verdict({"stance": "hold", "research_meta": {}}) is None
    assert rp.note_verdict({"research_meta": "not a dict"}) is None


# ── style guards on every variant ────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["stock", "fund", "bank"])
@pytest.mark.parametrize("new", [False, True])
@pytest.mark.parametrize("owned", [True, False, None])
@pytest.mark.parametrize("vr", [None, "ok", "withheld"])
def test_style_guards_hold_on_every_variant(base, kind, new, owned, vr):
    a = {**_no_range(base), "security_kind": kind, "is_new_listing": new,
         "position": {"owned": owned} if owned is not None else {}}
    if vr == "ok":
        a["valuation_range"] = VR
    elif vr == "withheld":
        a["valuation_range"] = {"status": "withheld", "measure": "p_fcf",
                                "warnings": ["inputs withheld"]}
    p = _prompt(a)
    _guards(p)
    for bad in ("None", "{", "}"):
        assert bad not in p, bad
    assert not [ln for ln in p.splitlines() if ln != ln.rstrip()]
