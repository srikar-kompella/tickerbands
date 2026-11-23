"""
Verdict (prompt v6) — app logic: parser keys and the derived stance, the
rulebook (expected verdict / risk), the v6 checks, the verdict label, the
owner line, trust labels, and the valuation_range arithmetic.
Contract: docs/VERDICT_SPEC.md. Fictional ZZ tickers only.
"""
import datetime as dt
import json

import pytest

from analysis import research_quality as rq
from analysis import value_lens as vl
from tests.conftest import write_md


# ── parser ─────────────────────────────────────────────────────────────────────

V6_LINES = [
    'ticker: "ZZVA"', "created_ts: 2026-11-21T10:00:00", "prompt_version: 6",
    'summary: "Stretched on cash flow; two soft signals"',
    'research_status: "complete"', 'move_cause: "na"', 'news_window: "2026-11-14..2026-11-21"',
    'verdict: "stretched"', 'value_grade: "Expensive"', 'risk_grade: "Watch"',
    'value_basis: "P/FCF 48.0 vs five-year median 30.1 (high 41.0): above the high"',
    'verdict_note: "Above its own range with two confirmed soft signals"',
    'hard_flags: []',
    'soft_signals: ["receivables +35% vs revenue +9% (10-Q filed 2026-11-04)", '
    '"operating margin down 4.1 pts for two quarters (10-Q 2026-08-05, 2026-11-04)", '
    '"<source and date>"]',
    'watch_items: ["lock-up expiry 2026-12-15 (S-1)"]',
    'owner_note: "your rule — sell only on a confirmed finding — is not met: hold"',
    'stance_horizon: "6-12mo"', 'stance_confidence: "medium"', 'price_used: 101.50',
    'upgrade_if: ["by 2027-02-28: P/FCF back under 35"]',
    'downgrade_if: ["by 2027-02-28: a third soft signal confirmed"]',
    'review_by: "2027-02-28"',
]


def _parse(tmp_path, lines, ticker="ZZVA"):
    from discussions_svc.parser import parse_discussion_file
    path = write_md(tmp_path, ticker, "2026-11-21T10-00-00.md", lines)
    row = parse_discussion_file(path)
    row["research_meta"] = json.loads(row["research_meta"] or "{}")
    return row


def test_v6_note_round_trip_with_derived_stance(tmp_path):
    row = _parse(tmp_path, V6_LINES)
    m = row["research_meta"]
    assert m["verdict"] == "stretched" and m["value_grade"] == "expensive"
    assert m["risk_grade"] == "watch"
    assert m["value_basis"].startswith("P/FCF 48.0")
    # the "<source and date>" template slot is dropped, real items kept
    assert len(m["soft_signals"]) == 2 and "hard_flags" not in m
    assert m["watch_items"] == ["lock-up expiry 2026-12-15 (S-1)"]
    assert m["owner_note"].startswith("your rule")
    # legacy stance derived: stretched → trim, stance_note from verdict_note
    assert row["stance"] == "trim" and m["stance_derived"] is True
    assert row["stance_note"] == "Above its own range with two confirmed soft signals"
    assert "unreadable" not in m


@pytest.mark.parametrize("verdict,stance", [("attractive", "buy"), ("fair", "hold"),
                                            ("stretched", "trim"), ("avoid", "sell"),
                                            ("unrated", "watch")])
def test_every_verdict_maps_to_its_legacy_stance(tmp_path, verdict, stance):
    lines = [x for x in V6_LINES if not x.startswith("verdict:")] + [f'verdict: "{verdict}"']
    assert _parse(tmp_path, lines)["stance"] == stance


def test_unreadable_v6_enums_are_recorded_not_dropped(tmp_path):
    lines = [x for x in V6_LINES if not x.startswith(("verdict:", "value_grade:", "risk_grade:"))]
    lines += ['verdict: "bargain"', 'value_grade: "cheapish"', 'risk_grade: "Red flag"']
    row = _parse(tmp_path, lines)
    u = row["research_meta"]["unreadable"]
    assert u["verdict"] == "bargain" and u["value_grade"] == "cheapish"
    assert row["research_meta"]["risk_grade"] == "red_flag"      # "Red flag" is readable
    assert row["stance"] is None                                  # nothing derived


def test_note_before_v6_is_untouched(tmp_path):
    row = _parse(tmp_path, ['ticker: "ZZVB"', "created_ts: 2026-11-20T10:00:00",
                            "prompt_version: 5", 'stance: "hold"', 'stance_note: "fine"',
                            'research_status: "complete"'], ticker="ZZVB")
    assert row["stance"] == "hold" and row["stance_note"] == "fine"
    assert "stance_derived" not in row["research_meta"]
    row = _parse(tmp_path, ['ticker: "ZZVB"', "created_ts: 2026-11-20T10:00:00",
                            "prompt_version: 5", 'research_status: "complete"'], ticker="ZZVB")
    assert row["stance"] is None


def test_verdict_without_prompt_version_still_derives(tmp_path):
    lines = [x for x in V6_LINES if not x.startswith("prompt_version")]
    assert _parse(tmp_path, lines)["stance"] == "trim"


def test_written_stance_wins_over_derivation_and_is_flagged(tmp_path):
    row = _parse(tmp_path, V6_LINES + ['stance: "buy"'])
    assert row["stance"] == "buy" and "stance_derived" not in row["research_meta"]
    flags = rq.verdict_flags(row)
    assert any("writes stance buy beside verdict stretched" in f for f in flags)


# ── the rulebook ───────────────────────────────────────────────────────────────

def _m(vg="fair", hard=0, soft=0, watch=0, **kw):
    m = {"value_grade": vg, "research_status": "complete"}
    if hard:
        m["hard_flags"] = [f"h{i}" for i in range(hard)]
    if soft:
        m["soft_signals"] = [f"s{i}" for i in range(soft)]
    if watch:
        m["watch_items"] = [f"w{i}" for i in range(watch)]
    m.update(kw)
    return m


@pytest.mark.parametrize("vg,soft,want", [
    ("cheap", 0, "attractive"), ("fair", 0, "fair"), ("expensive", 0, "stretched"),
    ("cheap", 1, "attractive"), ("fair", 1, "fair"), ("expensive", 1, "stretched"),
    ("cheap", 2, "fair"), ("fair", 2, "stretched"), ("expensive", 2, "avoid"),
    ("cheap", 3, "avoid"), ("fair", 4, "avoid"), ("expensive", 3, "avoid"),
])
def test_expected_verdict_soft_signal_table(vg, soft, want):
    assert rq.expected_verdict(_m(vg, soft=soft)) == want


@pytest.mark.parametrize("vg", ["cheap", "fair", "expensive"])
def test_any_hard_flag_is_avoid(vg):
    assert rq.expected_verdict(_m(vg, hard=1)) == "avoid"


@pytest.mark.parametrize("watch", [1, 3, 10])
def test_watch_items_never_change_the_verdict(watch):
    for vg, want in (("cheap", "attractive"), ("fair", "fair"), ("expensive", "stretched")):
        assert rq.expected_verdict(_m(vg, watch=watch)) == want
    assert rq.expected_verdict(_m("cheap", soft=1, watch=watch)) == "attractive"


def test_new_listing_cap_applies_last():
    assert rq.expected_verdict(_m("cheap"), is_new_listing=True) == "fair"
    assert rq.expected_verdict(_m("expensive"), is_new_listing=True) == "stretched"
    assert rq.expected_verdict(_m("cheap", soft=2), is_new_listing=True) == "fair"
    assert rq.expected_verdict(_m("cheap", hard=1), is_new_listing=True) == "avoid"


def test_paused_or_failed_is_unrated():
    assert rq.expected_verdict(_m("paused")) == "unrated"
    assert rq.expected_verdict(_m("cheap", research_status="failed")) == "unrated"
    # S6: any hard flag → avoid, even when research failed
    assert rq.expected_verdict(_m("cheap", hard=1, research_status="failed")) == "avoid"
    assert rq.expected_verdict({"research_status": "complete"}) == "unrated"   # no grade


@pytest.mark.parametrize("hard,soft,watch,want", [
    (1, 0, 0, "red_flag"), (0, 3, 0, "red_flag"), (0, 2, 0, "watch"),
    (0, 1, 1, "watch"), (0, 0, 2, "watch"), (0, 1, 0, "clean"), (0, 0, 0, "clean"),
])
def test_expected_risk(hard, soft, watch, want):
    assert rq.expected_risk(_m(hard=hard, soft=soft, watch=watch)) == want


# ── v6 checks ──────────────────────────────────────────────────────────────────

def _row(meta, **kw):
    base = {"verdict": "fair", "value_grade": "fair", "risk_grade": "clean",
            "value_basis": "P/FCF 22 vs median 21", "research_status": "complete",
            "price_used": 50.0, "stance_confidence": "medium", "prompt_version": 6,
            "upgrade_if": ["u"], "downgrade_if": ["d"], "review_by": "2027-03-01"}
    base.update(meta)
    base["stance_derived"] = True
    stance = {"attractive": "buy", "fair": "hold", "stretched": "trim", "avoid": "sell",
              "unrated": "watch"}.get(base.get("verdict"), "hold")
    return {"stance": stance, "created_ts": "2026-11-21T10:00:00", "research_meta": base,
            **kw}


def test_clean_v6_note_has_no_verdict_flags():
    assert rq.verdict_flags(_row({})) == []


def test_verdict_off_the_rulebook_is_flagged_with_expected_and_why():
    f = rq.verdict_flags(_row({"verdict": "attractive", "soft_signals": ["a", "b"],
                               "risk_grade": "watch"}))
    assert ("verdict does not follow the rulebook: expected stretched from value_grade "
            "fair, 0 hard flag(s), 2 soft signal(s) — the note says attractive") in f


def test_new_listing_cap_flag_uses_the_row():
    r = _row({"verdict": "attractive", "value_grade": "cheap"}, is_new_listing=True)
    assert any("expected fair" in x and "listed under a year" in x for x in rq.verdict_flags(r))
    r = _row({"verdict": "attractive", "value_grade": "cheap"})
    assert rq.verdict_flags(r) == []


def test_paused_with_hard_flag_is_avoid():
    # S6 order: a hard flag gives avoid before a paused value grade
    r = _row({"verdict": "avoid", "value_grade": "paused", "hard_flags": ["8-K 4.02"],
              "risk_grade": "red_flag"})
    assert not any("rulebook" in x for x in rq.verdict_flags(r))
    r = _row({"verdict": "unrated", "value_grade": "paused", "hard_flags": ["8-K 4.02"],
              "risk_grade": "red_flag", "verdict_note": "value paused: ADR ratio"})
    assert any("expected avoid" in x for x in rq.verdict_flags(r))


def test_unrated_for_incomplete_research_is_accepted_only_when_the_note_says_so():
    r = _row({"verdict": "unrated", "research_status": "partial"})
    assert not any("rulebook" in x for x in rq.verdict_flags(r))
    # "research incomplete" only when research is incomplete (S6)
    r = _row({"verdict": "unrated", "verdict_note": "Research incomplete: EDGAR down"})
    f = rq.verdict_flags(r)
    assert any("expected fair" in x for x in f)
    assert any(x.startswith('verdict_note starts "research incomplete" but research_status')
               for x in f)
    r = _row({"verdict": "unrated", "verdict_note": "Not sure"})
    assert any("expected fair" in x for x in rq.verdict_flags(r))


def test_risk_grade_mismatch_is_flagged():
    f = rq.verdict_flags(_row({"watch_items": ["probe reported"], "risk_grade": "clean"}))
    assert any(x.startswith("risk_grade does not match the lists: expected watch") for x in f)


def test_missing_and_unreadable_v6_fields():
    r = _row({})
    del r["research_meta"]["value_basis"], r["research_meta"]["risk_grade"]
    f = rq.verdict_flags(r)
    assert ("missing required field(s): risk_grade, value_basis — the note does not "
            "follow prompt v6") in f
    r = _row({"unreadable": {"value_grade": "cheapish"}})
    del r["research_meta"]["value_grade"]
    f = rq.stance_flags(r)
    assert "unreadable field(s): value_grade 'cheapish'" in f
    assert not any("rulebook" in x for x in f)          # cannot judge without the grade


def test_conditions_required_unless_unrated():
    r = _row({})
    for k in ("upgrade_if", "review_by"):
        del r["research_meta"][k]
    f = rq.verdict_flags(r)
    assert any(x.startswith("fair with no conditions") and "upgrade_if, review_by" in x
               for x in f)
    r = _row({"verdict": "unrated", "value_grade": "paused",
              "verdict_note": "value paused: ADR ratio unreconciled"}, stance="watch")
    for k in ("upgrade_if", "downgrade_if", "review_by"):
        del r["research_meta"][k]
    assert rq.verdict_flags(r) == []
    r["research_meta"]["verdict_note"] = "no grade"
    assert any('should start "value paused"' in x for x in rq.verdict_flags(r))
    # the v5 "watch with no conditions" rule does not apply to a v6 unrated
    assert not any("no conditions" in x for x in rq.stance_flags(r, today=dt.date(2026, 11, 22)))


def test_v5_checks_stay_for_v6_notes():
    r = _row({"research_status": "partial", "review_by": "2026-11-01",
              "plan_triggers_met": ["stop at $40 hit"]})
    f = rq.stance_flags(r, today=dt.date(2026, 11, 22))
    assert any(x.startswith("research incomplete (research_status: partial)") for x in f)
    assert any(x.startswith("expired — review_by 2026-11-01") for x in f)
    assert "trigger(s) met: stop at $40 hit" in f


def test_value_grade_vs_grade_hint():
    vr = {"grade_hint": "fair", "now": 22.0, "median": 21.0, "high": 30.0,
          "label": "Price to free cash flow"}
    r = _row({"value_grade": "cheap", "verdict": "attractive",
              "value_basis": "P/FCF 22 vs median 21"})
    f = rq.value_grade_flags(r, vr)
    assert f == ["value_grade cheap differs from the app's valuation range (grade fair: "
                 "Price to free cash flow now 22.0 vs five-year median 21.0, high 30.0) and "
                 "value_basis gives no departure reason"]
    r["research_meta"]["value_basis"] = ("P/FCF 22 on TTM inflated by a one-time tax refund; "
                                         "normalized 15 vs median 21")
    assert rq.value_grade_flags(r, vr) == []
    assert rq.value_grade_flags(_row({}), vr) == []                     # agrees
    assert rq.value_grade_flags(r, {"grade_hint": None}) == []          # no hint, no check
    # attached to the row, verdict_flags runs it too
    r2 = _row({"value_grade": "cheap", "verdict": "attractive"}, valuation_range=vr)
    assert any(x.startswith("value_grade cheap differs") for x in rq.verdict_flags(r2))


def test_value_grade_is_the_less_favorable_when_normalized():
    """S1: two hints (reported and five-year average) → the grade is the
    less favorable; the other needs a departure reason."""
    vr = {"grade_hint": "cheap", "normalized_grade_hint": "fair", "now": 23.8,
          "now_normalized": 77.0, "median": 58.1, "high": 134.1,
          "label": "Price to free cash flow"}
    assert rq.value_grade_flags(_row({"value_grade": "fair", "verdict": "fair",
                                      "value_basis": "P/FCF 23.8 vs median 58.1"}), vr) == []
    f = rq.value_grade_flags(_row({"value_grade": "cheap", "verdict": "attractive",
                                   "value_basis": "P/FCF 23.8 vs median 58.1"}), vr)
    assert f == ["value_grade cheap differs from the app's valuation range (grade fair "
                 "(reported cheap, normalized fair): Price to free cash flow now 23.8 vs "
                 "five-year median 58.1, high 134.1; on the five-year average 77.0) and "
                 "value_basis gives no departure reason"]
    assert rq.value_grade_flags(_row({"value_grade": "cheap", "verdict": "attractive",
                                      "value_basis": "reported P/FCF rather than the average: "
                                                     "the business has changed"}), vr) == []
    f = rq.value_grade_flags(_row({"value_grade": "expensive", "verdict": "stretched",
                                   "value_basis": "P/FCF 23.8 vs median 58.1"}), vr)
    assert len(f) == 1 and "gives no departure reason" in f[0]


def test_unreadable_verdict_blocks_the_older_stance_with_a_flag():
    old = {"id": 1, "stance": "hold", "created_ts": "2026-11-10T10:00:00",
           "research_meta": {"prompt_version": 5}}
    new = {"id": 2, "stance": None, "created_ts": "2026-11-21T10:00:00",
           "research_meta": {"prompt_version": 6, "unreadable": {"verdict": "bargain"}}}
    st, _ = rq.pick_stance([new, old])
    assert st["newer_unreadable"]["value"] == "bargain"


# ── label, owner line, trust ───────────────────────────────────────────────────

def test_verdict_label():
    assert rq.verdict_label(_row({"verdict": "stretched", "value_grade": "expensive",
                                  "risk_grade": "watch"})) == "Stretched (Expensive · Watch)"
    assert rq.verdict_label(_row({"verdict": "unrated", "value_grade": "paused",
                                  "risk_grade": "red_flag"})) == \
        "Unrated (Value paused · Red flag)"
    r = _row({})
    del r["research_meta"]["risk_grade"]
    assert rq.verdict_label(r) == "Fair (Fair)"
    assert rq.verdict_label({"research_meta": {}}) is None


@pytest.mark.parametrize("verdict,own,not_own", [
    ("attractive", "room to add", "candidate to buy"), ("fair", "hold", "wait"),
    ("stretched", "consider trimming", "pass"), ("avoid", "consider exiting", "pass"),
    ("unrated", "do not act on it", "do not act on it")])
def test_owner_line_owned_not_owned_unknown(verdict, own, not_own):
    r = _row({"verdict": verdict})
    assert rq.owner_line(r, True) == f"You own it: {own}"
    assert rq.owner_line(r, False) == f"Not owned: {not_own}"
    assert rq.owner_line(r) == f"If you own it: {own} · if not: {not_own}"


def test_owner_line_ownership_sources_and_owner_note():
    r = _row({"verdict": "fair", "owned": "no",
              "owner_note": "your rule — trim above 10% of the account — is not met"}, account_owned=True)
    # the account wins over the note's `owned`; the note travels separately
    assert rq.owner_line(r) == "You own it: hold"
    assert rq.verdict_fields(r)["owner_note"] == "your rule — trim above 10% of the account — is not met"
    del r["account_owned"]
    assert rq.owner_line(r).startswith("Not owned: wait")
    assert rq.owner_line({"research_meta": {}}) is None


def test_trust_mapping():
    assert rq.trust(rq.OK) == "current"
    assert rq.trust(rq.STALE) == "out_of_date"
    assert rq.trust(rq.INVALID) == "dont_use"
    assert rq.trust(rq.SUPERSEDED) == "replaced"
    assert rq.trust(None) is None


def test_verdict_fields_formats():
    assert rq.verdict_fields({"stance": "hold", "research_meta": {"prompt_version": 5}}) == \
        {"format": "earlier"}
    vf = rq.verdict_fields(_row({"verdict": "stretched", "value_grade": "expensive",
                                 "risk_grade": "watch", "soft_signals": ["a", "b"]}))
    assert vf["format"] == "v6" and vf["verdict_label"] == "Stretched (Expensive · Watch)"
    assert vf["soft_signals"] == ["a", "b"] and vf["hard_flags"] == []
    assert vf["expected_verdict"] == "avoid" and vf["expected_risk"] == "watch"
    assert set(vf) >= {"verdict", "value_grade", "risk_grade", "value_basis", "verdict_note",
                       "hard_flags", "soft_signals", "watch_items", "owner_line",
                       "owner_note"}


# ── valuation_range arithmetic ─────────────────────────────────────────────────

@pytest.mark.parametrize("now,want", [(17.0, "cheap"), (17.1, "fair"), (29.0, "fair"),
                                      (30.1, "expensive"), (31.0, "expensive")])
def test_grade_hint_multiple(now, want):
    # median 20: cheap ≤ 17.0; expensive above the high 30 or above 30 (1.5×)
    assert vl.grade_hint(now, 20.0, 30.0) == want


def test_grade_hint_above_the_high_is_expensive_before_1_5x():
    assert vl.grade_hint(26.0, 20.0, 25.0) == "expensive"


def test_grade_hint_distribution_rate_is_inverted():
    # median 4%: a higher rate is cheaper — ≥ 4/0.85 = 4.71% cheap; below the
    # low (3%) or below 4/1.5 = 2.67% expensive
    assert vl.grade_hint(4.8, 4.0, 5.0, 3.0, inverted=True) == "cheap"
    assert vl.grade_hint(4.2, 4.0, 5.0, 3.0, inverted=True) == "fair"
    assert vl.grade_hint(2.9, 4.0, 5.0, 3.0, inverted=True) == "expensive"


def _bars(start: dt.date, end: dt.date, price_fn):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append({"date": d.isoformat(), "close": price_fn(d)})
        d += dt.timedelta(days=1)
    return out


TODAY = dt.date(2026, 11, 22)


def _annual(n=5, shares=1e9, filed_after_split=True, fcf=None):
    years = []
    for i in range(n):
        end = dt.date(2025 - i, 12, 31)
        years.append({"end": end.isoformat(), "fcf": (fcf or [2e9] * n)[i],
                      "net_income": 1.5e9, "shares_diluted": shares,
                      "shares_filed": (end + dt.timedelta(days=60)).isoformat()})
    return years


def _fields(now=25.0, mc=50e9, price=50.0, key="p_fcf"):
    return {key: {"value": now, "status": "ok", "basis": "report price; TTM = 4 quarters"},
            "market_cap": {"value": mc, "status": "ok"},
            "price": {"value": price, "status": "ok"}}


def test_stock_range_points_median_and_hint():
    bars = _bars(dt.date(2020, 1, 1), TODAY, lambda d: 40.0 + (d.year - 2020) * 2)
    lh = {"available": True, "bars": bars, "splits": [], "dividends": []}
    stmts = {"available": True, "ttm": {"fcf": 2e9, "net_income": 1.5e9},
             "annual": _annual()}
    vr = vl.build_valuation_range("stock", _fields(), stmts, lh, None, "USD", TODAY)
    assert vr["status"] == "ok" and vr["measure"] == "p_fcf"
    # price at 2025-12-31 is 50 (Dec 31 2025 is a Wednesday); FCF/share = 2.0
    assert vr["points"][0] == {"fy_end": "2025-12-31", "value": 25.0, "price": 50.0}
    assert [p["value"] for p in vr["points"]] == [25.0, 24.0, 23.0, 22.0, 21.0]
    assert (vr["median"], vr["low"], vr["high"]) == (23.0, 21.0, 25.0)
    assert vr["now"] == 25.0 and vr["grade_hint"] == "fair"
    assert vr["normalized"] is False


def test_split_after_the_filing_is_applied():
    # a 10:1 split on 2024-06-10: counts filed before it are ×10, prices are
    # split-adjusted already
    bars = _bars(dt.date(2020, 1, 1), TODAY, lambda d: 50.0)
    lh = {"available": True, "bars": bars, "splits": [{"date": "2024-06-10", "ratio": 10.0}],
          "dividends": []}
    ann = _annual(shares=1e9)
    for a in ann:
        if a["shares_filed"] < "2024-06-10":
            a["shares_diluted"] = 1e8                     # filed pre-split
    stmts = {"available": True, "ttm": {"fcf": 2e9}, "annual": ann}
    vr = vl.build_valuation_range("stock", _fields(), stmts, lh, None, "USD", TODAY)
    assert [p["value"] for p in vr["points"]] == [25.0] * 5


def test_unreconciled_share_jump_leaves_the_year_out():
    bars = _bars(dt.date(2020, 1, 1), TODAY, lambda d: 50.0)
    lh = {"available": True, "bars": bars, "splits": [], "dividends": []}
    ann = _annual()
    ann[3]["shares_diluted"] = 1e8                        # 10× off, no split on record
    stmts = {"available": True, "ttm": {"fcf": 2e9}, "annual": ann}
    vr = vl.build_valuation_range("stock", _fields(), stmts, lh, None, "USD", TODAY)
    assert len(vr["points"]) == 4
    assert any("2022-12-31" in w and "cannot reconcile" in w for w in vr["warnings"])


def test_yahoo_counts_without_filing_date_pick_the_closer_basis():
    bars = _bars(dt.date(2020, 1, 1), TODAY, lambda d: 50.0)
    lh = {"available": True, "bars": bars, "splits": [{"date": "2024-06-10", "ratio": 4.0}],
          "dividends": []}
    ann = _annual()
    for a in ann:
        a["shares_filed"] = None
    ann[2]["shares_diluted"] = 2.5e8                      # not restated
    stmts = {"available": True, "ttm": {"fcf": 2e9}, "annual": ann}
    vr = vl.build_valuation_range("stock", _fields(), stmts, lh, None, "USD", TODAY)
    assert [p["value"] for p in vr["points"]] == [25.0] * 5


def test_negative_fcf_year_is_left_out_and_negative_ttm_falls_back_to_pe():
    bars = _bars(dt.date(2020, 1, 1), TODAY, lambda d: 50.0)
    lh = {"available": True, "bars": bars, "splits": [], "dividends": []}
    ann = _annual(fcf=[2e9, -1e9, 2e9, 2e9, 2e9])
    vr = vl.build_valuation_range("stock", _fields(), {"available": True, "ttm": {"fcf": 2e9},
                                                       "annual": ann}, lh, None, "USD", TODAY)
    assert len(vr["points"]) == 4
    assert any("2024-12-31: free cash flow ≤ 0" in w for w in vr["warnings"])
    vr = vl.build_valuation_range("stock", _fields(30.0, key="pe_ttm"),
                                  {"available": True, "ttm": {"fcf": -5e8, "net_income": 1.5e9},
                                   "annual": ann}, lh, None, "USD", TODAY)
    assert vr["measure"] == "pe" and vr["now"] == 30.0
    # 50 / (1.5e9 / 1e9) = 33.33
    assert vr["points"][0]["value"] == 33.33 and vr["grade_hint"] == "fair"


def test_normalized_when_ttm_is_more_than_double_the_average():
    bars = _bars(dt.date(2020, 1, 1), TODAY, lambda d: 50.0)
    lh = {"available": True, "bars": bars, "splits": [], "dividends": []}
    stmts = {"available": True, "ttm": {"fcf": 5e9}, "annual": _annual()}   # avg 2e9
    vr = vl.build_valuation_range("stock", _fields(10.0), stmts, lh, None, "USD", TODAY)
    assert vr["normalized"] is True and vr["now_normalized"] == 25.0
    # two grades: the reported figure (10 vs median 25 → cheap) and the
    # five-year average (25 → fair); the research session picks one
    assert vr["grade_hint"] == "cheap" and vr["normalized_grade_hint"] == "fair"
    w = [x for x in vr["warnings"] if "average" in x]
    assert w == ["current free cash flow is 2.5× its five-year average; on that average the "
                 "measure is 25.0x instead of 10.0x — reported grade cheap, normalized grade "
                 "fair; the value grade is the less favorable of the two"]
    assert vr["grade"] == "fair"


def test_no_normalization_after_a_negative_year_turnaround():
    """A turnaround (TWLO): earlier cash flow negative → the five-year
    average means nothing; the reported grade stands, no normalized fields."""
    bars = _bars(dt.date(2020, 1, 1), TODAY, lambda d: 50.0)
    lh = {"available": True, "bars": bars, "splits": [], "dividends": []}
    ann = _annual(fcf=[2e9, 2e9, 2e9, -1e9, -1e9])
    vr = vl.build_valuation_range("stock", _fields(20.0), {"available": True,
                                  "ttm": {"fcf": 5e9}, "annual": ann}, lh, None, "USD", TODAY)
    assert vr["grade_hint"] == "cheap"                   # 20 vs median 25
    assert vr["normalized"] is False
    assert vr["now_normalized"] is None and vr["normalized_grade_hint"] is None
    assert not any("average" in w for w in vr["warnings"])


def test_normalization_same_grade_both_ways_says_so():
    bars = _bars(dt.date(2020, 1, 1), TODAY, lambda d: 50.0)
    lh = {"available": True, "bars": bars, "splits": [], "dividends": []}
    stmts = {"available": True, "ttm": {"fcf": 4.5e9}, "annual": _annual()}   # 2.25× avg
    vr = vl.build_valuation_range("stock", _fields(10.0), stmts, lh, None, "USD", TODAY)
    # 10 → cheap; on the average 22.5 > 0.85 × 25 = 21.25 → fair
    assert vr["grade_hint"] == "cheap" and vr["normalized_grade_hint"] == "fair"
    stmts = {"available": True, "ttm": {"fcf": 4.2e9}, "annual": _annual()}   # 2.1× avg
    vr = vl.build_valuation_range("stock", _fields(10.0), stmts, lh, None, "USD", TODAY)
    assert vr["now_normalized"] == 21.0 and vr["normalized_grade_hint"] == "cheap"
    assert any(w.endswith("the grade is cheap either way") for w in vr["warnings"])


def test_extreme_year_end_multiple_is_excluded_and_named():
    """MNDY: a near-zero cash-flow year gives 1960x — left out of
    median / low / high, named in the warnings, kept in excluded_points."""
    bars = _bars(dt.date(2020, 1, 1), TODAY, lambda d: 50.0)
    lh = {"available": True, "bars": bars, "splits": [], "dividends": []}
    # 50 / (fcf / 1e9 shares): 25, 25, 25, 25, 1250
    ann = _annual(fcf=[2e9, 2e9, 2e9, 2e9, 4e7])
    vr = vl.build_valuation_range("stock", _fields(20.0), {"available": True,
                                  "ttm": {"fcf": 2e9}, "annual": ann}, lh, None, "USD", TODAY)
    assert vr["status"] == "ok" and len(vr["points"]) == 4
    assert vr["high"] == 25.0 and vr["median"] == 25.0
    assert [p["value"] for p in vr["excluded_points"]] == [1250.0]
    ex = [w for w in vr["warnings"] if "excluded" in w]
    assert len(ex) == 1 and ex[0].startswith("FY2021 (ended 2021-12-31) excluded: free cash "
                                             "flow near zero — 1250.0x")


def test_report_range_row_shows_both_grades():
    """The report field (and so the HTML export) names both hints."""
    from analysis import report_view as rv
    vr = {"measure": "p_fcf", "label": "Price to free cash flow", "status": "ok",
          "now": 23.8, "now_normalized": 77.0, "normalized": True, "median": 58.1,
          "low": 47.6, "high": 134.1, "grade_hint": "cheap", "normalized_grade_hint": "fair",
          "points": [{"fy_end": "2026-01-25", "value": 47.6}], "warnings": []}
    view: dict = {}
    rv.add_valuation_fields(view, vr)
    f = view["fields"]["valuation_range"]
    assert f["display"].endswith("· grade on the reported figure cheap · on the five-year "
                                 "average fair")
    assert f["normalized_grade_hint"] == "fair"
    view = {}
    rv.add_valuation_fields(view, {**vr, "normalized": False, "now_normalized": None,
                                   "normalized_grade_hint": None})
    assert view["fields"]["valuation_range"]["display"].endswith(
        "· grade on the reported figure cheap")


def test_warning_numbers_are_rounded_and_reader_facing():
    bars = _bars(dt.date(2024, 6, 1), TODAY, lambda d: 50.0)
    lh = {"available": True, "bars": bars, "splits": [], "dividends": []}
    stmts = {"available": True, "ttm": {"fcf": 2e9}, "annual": _annual()}
    vr = vl.build_valuation_range("stock", _fields(), stmts, lh, None, "USD", TODAY)
    assert not any("value_basis" in w or "grade against" in w for w in vr["warnings"])
    assert any("the research note compares with peers instead" in w for w in vr["warnings"])
    bars = _bars(dt.date(2020, 1, 1), TODAY, lambda d: 50.0)
    lh = {"available": True, "bars": bars, "splits": [], "dividends": []}
    stmts = {"available": True, "ttm": {"fcf": 5.123456e9}, "annual": _annual()}
    vr = vl.build_valuation_range("stock", _fields(10.0), stmts, lh, None, "USD", TODAY)
    import re
    for w in vr["warnings"]:
        assert not re.search(r"\d+\.\d{2,}", w), w


def test_fewer_than_three_points_is_no_data():
    bars = _bars(dt.date(2024, 6, 1), TODAY, lambda d: 50.0)       # young listing
    lh = {"available": True, "bars": bars, "splits": [], "dividends": []}
    stmts = {"available": True, "ttm": {"fcf": 2e9}, "annual": _annual()}
    vr = vl.build_valuation_range("stock", _fields(), stmts, lh, None, "USD", TODAY)
    assert vr["status"] == "no_data" and vr["grade_hint"] is None
    assert len(vr["points"]) == 2
    assert any("fewer than 3" in w and "peers" in w for w in vr["warnings"])
    assert any("price history starts 2024-06-03" in w for w in vr["warnings"])


def test_adr_and_non_usd_are_withheld():
    stmts = {"available": True, "ttm": {"fcf": 2e9}, "annual": _annual()}
    vr = vl.build_valuation_range("stock", _fields(), stmts, None,
                                  "broker sub-type ADR — share counts are not in ADR units",
                                  "USD", TODAY)
    assert vr["status"] == "withheld" and vr["points"] == [] and vr["grade_hint"] is None
    assert any("ADR" in w for w in vr["warnings"])
    vr = vl.build_valuation_range("stock", _fields(), stmts, None, None, "TWD", TODAY)
    assert vr["status"] == "withheld" and any("TWD" in w for w in vr["warnings"])


def test_no_long_history_or_no_fy_shares_is_no_data():
    stmts = {"available": True, "ttm": {"fcf": 2e9}, "annual": _annual()}
    vr = vl.build_valuation_range("stock", _fields(), stmts,
                                  {"available": False, "error": "unavailable: x"},
                                  None, "USD", TODAY)
    assert vr["status"] == "no_data" and vr["now"] == 25.0
    old = [{k: v for k, v in a.items() if not k.startswith("shares")} for a in _annual()]
    vr = vl.build_valuation_range("stock", _fields(), {"available": True, "ttm": {"fcf": 2e9},
                                                       "annual": old},
                                  {"available": True, "bars": [], "splits": []},
                                  None, "USD", TODAY)
    assert vr["status"] == "no_data" and any("share counts" in w for w in vr["warnings"])


def test_bank_range_on_tangible_book():
    bars = _bars(dt.date(2020, 1, 1), TODAY, lambda d: 60.0)
    lh = {"available": True, "bars": bars, "splits": [], "dividends": []}
    ann = _annual()
    for a in ann:
        a["tangible_book"] = 30e9                       # TBV/share 30 → 2.0×
    vr = vl.build_valuation_range("bank", _fields(1.9, key="p_tbv"),
                                  {"available": True, "ttm": {}, "annual": ann}, lh,
                                  None, "USD", TODAY)
    assert vr["measure"] == "p_tbv" and vr["median"] == 2.0 and vr["grade_hint"] == "fair"
    assert vr["normalized"] is False                     # no TTM metric for a bank
    vr = vl.build_valuation_range("bank", _fields(2.4, key="p_tbv"),
                                  {"available": True, "ttm": {}, "annual": ann}, lh,
                                  None, "USD", TODAY)
    assert vr["grade_hint"] == "expensive"               # above the five-year high


def test_fund_distribution_rate_at_calendar_year_ends():
    bars = _bars(dt.date(2019, 6, 1), TODAY, lambda d: 50.0)
    divs = []
    for y in range(2019, 2027):
        for q in (3, 6, 9, 12):
            amt = 0.5 if y >= 2024 else 0.4
            divs.append({"date": f"{y}-{q:02d}-15", "amount": amt})
    lh = {"available": True, "bars": bars, "splits": [], "dividends": divs}
    f = {"div_yield_trailing": {"value": 4.0, "status": "ok", "basis": "trailing 12 mo"}}
    vr = vl.build_valuation_range("fund", f, None, lh, None, "USD", TODAY)
    assert vr["measure"] == "distribution_rate" and vr["status"] == "ok"
    assert [p["fy_end"] for p in vr["points"]] == [f"{y}-12-31" for y in range(2025, 2020, -1)]
    assert [p["value"] for p in vr["points"]] == [4.0, 4.0, 3.2, 3.2, 3.2]
    # 4.0% now vs a 3.2% median: ≥ 3.2 ÷ 0.85 = 3.76% → cheap on its own history
    assert vr["median"] == 3.2 and vr["grade_hint"] == "cheap"
    # a fund younger than a full trailing year before a year-end loses that point
    young = {**lh, "bars": _bars(dt.date(2023, 3, 1), TODAY, lambda d: 50.0)}
    vr = vl.build_valuation_range("fund", f, None, young, None, "USD", TODAY)
    assert vr["status"] == "no_data" and len(vr["points"]) == 2


def test_merge_fills_missing_fiscal_year_figures_from_the_other_source():
    from analysis import statements
    sec = {"available": True, "source": "SEC XBRL", "ttm": {"revenue": 1, "net_income": 1,
                                                            "fcf": 1},
           "periods": {"income": "2025-12-31"},
           "annual": [{"end": "2025-12-31", "revenue": 10, "fcf": None,
                       "shares_diluted": 1e9, "shares_filed": "2026-02-01"},
                      {"end": "2024-12-31", "revenue": 9, "fcf": 2.0,
                       "shares_diluted": None, "shares_filed": None}]}
    yahoo = {"available": True, "source": "Yahoo statements",
             "ttm": {"revenue": 1, "net_income": 1, "fcf": 1},
             "periods": {"income": "2025-12-31"},
             "annual": [{"end": "2025-12-31", "fcf": 3.0, "shares_diluted": 5e8},
                        {"end": "2024-12-30", "fcf": 9.0, "shares_diluted": 9e8}]}
    m = statements.merge([sec, yahoo])
    a0, a1 = m["annual"]
    assert a0["fcf"] == 3.0 and a0["filled_from"] == {"fcf": "Yahoo statements"}
    assert a0["shares_diluted"] == 1e9                   # filed figure kept
    assert a1["fcf"] == 2.0 and a1["shares_diluted"] == 9e8 and a1["shares_filed"] is None
    assert sec["annual"][0]["fcf"] is None               # inputs not mutated
