"""
analysis/research_quality.py — is a stored research note still usable?

prior_note_status: OK / STALE / INVALID per note (each rule), judged against
every completed ≥2σ session, not only the last one.
stance_flags: each warning for the stance shown beside a ticker.
All figures are illustrative.
"""
import datetime as dt

from analysis import market_clock as mc
from analysis import news_relevance as nr
from analysis import research_quality as rq

BIG = {"z": -2.97, "move_pct": -14.01}
CALM = {"z": 0.4, "move_pct": 0.6}
BASIS = {"session_date": "2026-11-20", "price_kind": "official_close",
         "move_word": "Fri Nov 20"}


def note(ts, **kw):
    meta = kw.pop("meta", {})
    return {"created_ts": ts, "summary": "s", "research_meta": meta, **kw}


# the same Friday seen from later days: it is no longer the report's last
# session, only a ≥2σ session in the daily bars
FRI = {"session_date": "2026-11-20", "z": -2.97, "move_pct": -14.01}
MON = {"session_date": "2026-11-23", "price_kind": "official_close",
       "move_word": "Mon Nov 23"}


def status(notes, move=BIG, basis=BASIS, sessions=None):
    return [(s["status"], s["reason"])
            for s in rq.prior_note_status(notes, move, basis, sessions)]


# ── prior_note_status ────────────────────────────────────────────────────────

def test_note_ok_when_nothing_is_wrong():
    got = rq.prior_note_status([note("2026-11-21T09:00:00")], BIG, BASIS)
    assert got == [{"created_ts": "2026-11-21T09:00:00", "status": "OK", "reason": ""}]
    # a calm market never makes an old note stale
    assert status([note("2026-11-10T09:00:00")], CALM) == [("OK", "")]
    assert status([note("2026-11-10T09:00:00")], None, None) == [("OK", "")]


def test_note_stale_when_written_before_the_big_session_closed():
    (st, why), = status([note("2026-11-13T09:00:00")])
    assert st == "STALE" and why == "written before the ≥2σ session of 2026-11-20 closed"
    # 15:59 on the session day is before the close; 16:30 is after it
    assert status([note("2026-11-20T15:59:00")])[0][0] == "STALE"
    assert status([note("2026-11-20T16:30:00")])[0][0] == "OK"


def test_note_not_stale_while_that_session_is_still_trading():
    live = {**BASIS, "price_kind": "live"}
    assert status([note("2026-11-20T10:00:00")], BIG, live) == [("OK", "")]


def test_note_invalid_when_it_reports_no_news_after_a_big_session():
    (st, why), = status([note("2026-11-21T10:05:00", news_view="neutral",
                              news_note="No material NVDA news today")])
    assert st == "INVALID" and "its news check failed" in why and "2026-11-20" in why
    # the same words BEFORE the session are merely stale — it could not know
    assert status([note("2026-11-19T10:00:00", news_note="no material news")])[0][0] == "STALE"
    # and with no ≥2σ session in the window they are a legitimate finding: here
    # the Friday before the note was calm
    assert status([note("2026-11-21T10:05:00", news_note="no material news")],
                  CALM, BASIS) == [("OK", "")]
    # …or the ≥2σ Friday is more than 5 sessions back when the note is written
    later = {"session_date": "2026-12-01", "price_kind": "official_close"}
    assert status([note("2026-12-02T10:05:00", news_note="no material news")],
                  CALM, later, [FRI]) == [("OK", "")]


def test_failed_news_check_stays_invalid_after_a_later_calm_session():
    """The verdict used to be judged against the LAST session only, so the
    failed Nov 21 note read OK again once Monday was an ordinary day."""
    failed = note("2026-11-21T10:05:00", news_view="neutral",
                  news_note="No material NVDA news today")
    assert status([failed])[0][0] == "INVALID"                     # seen on Sat Nov 21
    (st, why), = status([failed], CALM, MON, [FRI])                # seen after Mon Nov 23
    assert st == "INVALID"
    assert why == ("its news check failed — it reports no material news after the "
                   "≥2σ session of 2026-11-20")
    got = rq.prior_note_status([failed], {"z": 0.4}, {"session_date": "2026-11-23"}, [FRI])
    assert got[0]["status"] == "INVALID"
    # …and a week of calm sessions later
    later = {"session_date": "2026-12-01", "price_kind": "official_close"}
    assert status([failed], CALM, later, [FRI])[0][0] == "INVALID"


def test_stale_persists_and_counts_every_big_session_since_the_note():
    old = note("2026-11-13T09:00:00")
    assert status([old], CALM, MON, [FRI]) == [
        ("STALE", "written before the ≥2σ session of 2026-11-20 closed")]
    two = [{"session_date": "2026-11-17", "z": 2.4, "move_pct": 6.1}, FRI]
    assert status([old], CALM, MON, two) == [
        ("STALE", "written before 2 ≥2σ sessions closed (latest 2026-11-20)")]
    # a note written between them is stale against the later one only
    assert status([note("2026-11-18T09:00:00")], CALM, MON, two) == [
        ("STALE", "written before the ≥2σ session of 2026-11-20 closed")]
    # a ≥2σ session BEFORE the note does not make it stale
    assert status([note("2026-11-21T09:00:00")], CALM, MON, two) == [("OK", "")]


def test_v5_note_that_found_the_cause_may_log_empty_searches():
    """`move_cause: found` is trusted over the wording: logging searches that
    found nothing is what the prompt asks for."""
    text = ("Nov 16–21: accounting probe reported (Example Wire 11/20; Sample Journal "
            "11/20); searched DOJ, state AGs: no relevant results; no material news "
            "on export controls")
    v5 = {"prompt_version": 5, "research_status": "complete", "move_cause": "found"}
    assert status([note("2026-11-21T10:05:00", news_view="bearish", news_note=text,
                        meta=v5)]) == [("OK", "")]
    assert status([note("2026-11-21T10:05:00", news_view="bearish", news_note=text,
                        meta=v5)], CALM, MON, [FRI]) == [("OK", "")]
    # the structured field decides the other way too
    (st, why), = status([note("2026-11-21T10:05:00", news_note=text,
                              meta={**v5, "move_cause": "not_found"})])
    assert st == "INVALID" and "move_cause: not_found" in why
    # a note that does not say the cause was found is judged on its wording…
    assert status([note("2026-11-21T10:05:00", news_note=text)])[0][0] == "INVALID"
    assert status([note("2026-11-21T10:05:00", news_note=text,
                        meta={"prompt_version": 5, "move_cause": "na"})])[0][0] == "INVALID"
    # …and one empty search is not "no material news"
    for one_search in ("searched DOJ, SEC: no relevant results",
                       "searched class actions: nothing found",
                       "no relevant results for subpoena; no major filings"):
        assert status([note("2026-11-21T10:05:00", news_note=one_search)]) == [("OK", "")], \
            one_search


def test_no_news_wording_the_regex_accepts_and_rejects():
    yes = ["No material NVDA news today", "no material news", "no company-specific catalyst",
           "nothing material", "No news", "no significant company developments"]
    no = ["searched DOJ: no relevant results", "no relevant results for subpoena",
          "no major filings", "recall reported (two sources)"]
    assert all(nr._NO_NEWS.search(t) for t in yes)
    assert not any(nr._NO_NEWS.search(t) for t in no)


def _bars_with_one_drop(extra_calm=0):
    """25 alternating ±1% weekdays, one −14% session, then calm days."""
    out, px, d = [], 100.0, dt.date(2026, 10, 12)
    moves = [0.01 if i % 2 else -0.01 for i in range(25)] + [-0.14] \
        + [0.004 if i % 2 else -0.004 for i in range(extra_calm)]
    for m in moves:
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        px = round(px * (1 + m), 4)
        out.append({"date": d.isoformat(), "close": px})
        d += dt.timedelta(days=1)
    return out


def test_big_move_sessions_come_from_the_bars_and_keep_their_verdict():
    bars = _bars_with_one_drop()
    got = rq.big_move_sessions(bars)
    assert [g["session_date"] for g in got] == [bars[-1]["date"]]
    assert got[0]["z"] <= -2 and got[0]["move_pct"] == -14.0
    # five calm sessions later the same session is still there with the same z
    later = rq.big_move_sessions(_bars_with_one_drop(extra_calm=5))
    assert later == got
    assert rq.big_move_sessions(bars[:20]) == [] and rq.big_move_sessions(None) == []


def test_report_session_overrides_the_bars_and_a_live_session_is_left_out():
    bar_fri = {"session_date": "2026-11-20", "z": -2.9, "move_pct": -14.0}
    # the report's own z for its last session is the one shown, so it wins
    got = nr.big_sessions(BIG, BASIS, [bar_fri])
    assert got == [{"session_date": "2026-11-20", "z": -2.97, "move_pct": -14.01,
                    "label": "Fri Nov 20"}]
    # the report says that session was under 2σ → it is not a big session
    assert nr.big_sessions(CALM, BASIS, [bar_fri]) == []
    # a session that is still trading never counts
    live = {**BASIS, "price_kind": "live"}
    assert nr.big_sessions(BIG, live, []) == []
    # older sessions come through, oldest first; sub-2σ entries are ignored
    older = [{"session_date": "2026-11-17", "z": 2.4}, {"session_date": "2026-11-18", "z": 1.2}]
    assert [s["session_date"] for s in nr.big_sessions(BIG, BASIS, older)] == \
        ["2026-11-17", "2026-11-20"]


def test_naive_created_ts_is_machine_local_time_not_new_york(monkeypatch):
    """created_ts is written with datetime.now() on the user's machine. A
    note stamped 13:30 in Los Angeles was written at 16:30 ET — after the
    close, so it is not stale against that session."""
    from zoneinfo import ZoneInfo
    assert status([note("2026-11-20T13:30:00")])[0][0] == "STALE"      # local = New York
    monkeypatch.setattr(mc, "LOCAL_TZ", ZoneInfo("America/Los_Angeles"))
    assert status([note("2026-11-20T13:30:00")]) == [("OK", "")]
    assert status([note("2026-11-20T12:30:00")])[0][0] == "STALE"      # 15:30 ET
    # an explicit offset is used as written, whatever the machine zone
    assert status([note("2026-11-20T15:30:00-05:00")])[0][0] == "STALE"
    assert status([note("2026-11-20T21:30:00Z")]) == [("OK", "")]


# ── research_flags (news check + stance warnings) use the same sessions ──────

def test_research_flags_persist_after_a_later_calm_session():
    news = {"news_view": "neutral", "news_note": "No material NVDA news today",
            "created_ts": "2026-11-21T10:05:00"}
    stance = {"stance": "hold", **news}
    on_sat = nr.research_flags(news, stance, BIG, BASIS)
    assert on_sat["news"] == ["says no material news after a −14.01% (−3.0σ) Fri Nov 20 "
                              "session — the move's reported cause was not found; treat "
                              "this check as failed, not neutral"]
    assert on_sat["stance"] == ["stance came from a discussion whose news check missed "
                                "the cause of the −14.01% (−3.0σ) Fri Nov 20 session — "
                                "re-run research before acting"]
    # after Monday's ordinary session both warnings are still there
    on_mon = nr.research_flags(news, stance, CALM, MON, sessions=[FRI])
    assert on_mon == on_sat
    # with no ≥2σ session before the note there is nothing to flag
    assert nr.research_flags(news, stance, CALM, MON, sessions=[]) == {"news": [], "stance": []}


def test_research_flags_written_before_a_big_session_and_structured_cause():
    old = {"news_view": "neutral", "news_note": "quiet week", "created_ts": "2026-11-13T09:00:00"}
    got = nr.research_flags(old, {"stance": "hold", **old}, CALM, MON, sessions=[FRI])
    assert got["news"] == ["news check written 2026-11-13, before the −14.01% (−3.0σ) "
                           "Fri Nov 20 session — it could not cover that move"]
    assert got["stance"] == ["stance written 2026-11-13, before the −14.01% (−3.0σ) "
                             "Fri Nov 20 session — re-run research before acting"]
    # move_cause: found outranks negative-search wording
    found = {"news_view": "bearish", "created_ts": "2026-11-21T10:05:00",
             "news_note": "probe reported; searched DOJ: no material news there",
             "move_cause": "found"}
    assert nr.research_flags(found, None, BIG, BASIS) == {"news": [], "stance": []}
    nf = {**found, "move_cause": "not_found"}
    assert nr.research_flags(nf, None, BIG, BASIS)["news"] == [
        "did not find the cause of the −14.01% (−3.0σ) Fri Nov 20 session (move_cause: "
        "not_found) — treat this check as failed, not neutral"]
    # the stance is judged on its OWN discussion, whichever note the news came from
    stance = {"stance": "hold", "created_ts": "2026-11-21T08:00:00",
              "news_note": "no material news", "research_meta": {}}
    assert len(nr.research_flags(found, stance, BIG, BASIS)["stance"]) == 1


def test_note_invalid_when_research_was_partial_or_failed():
    for rs in ("partial", "failed"):
        (st, why), = status([note("2026-11-21T10:00:00", meta={"research_status": rs})], CALM)
        assert st == "INVALID" and f"research_status: {rs}" in why
    assert status([note("2026-11-21T10:00:00", meta={"research_status": "complete"})],
                  CALM) == [("OK", "")]


def test_note_invalid_when_the_move_cause_was_not_found():
    (st, why), = status([note("2026-11-21T10:00:00", meta={"move_cause": "not_found"})], CALM)
    assert st == "INVALID" and "move_cause: not_found" in why


def test_note_invalid_when_a_later_note_supersedes_it():
    notes = [note("2026-11-22T09:00:00", meta={"supersedes": ["2026-11-21T10:05:00"]}),
             note("2026-11-21T10:05:00"),
             note("2026-11-15T08:00:00")]
    got = status(notes, CALM)
    assert got[0] == ("OK", "")
    # replaced already → SUPERSEDED (its own status), so the next session is
    # not told to supersede it again
    assert got[1] == ("SUPERSEDED", "replaced by the note of 2026-11-22T09:00")
    assert got[2] == ("OK", "")
    # minute precision and a space separator both match
    notes[0]["research_meta"]["supersedes"] = ["2026-11-21 10:05"]
    assert status(notes, CALM)[1][0] == "SUPERSEDED"


def test_note_cannot_supersede_a_later_note_or_itself():
    notes = [note("2026-11-22T09:00:00"),
             note("2026-11-21T10:05:00",
                  meta={"supersedes": ["2026-11-22T09:00:00", "2026-11-21T10:05:00"]})]
    assert status(notes, CALM) == [("OK", ""), ("OK", "")]


def test_invalid_outranks_stale():
    (st, _), = status([note("2026-11-13T09:00:00", meta={"research_status": "failed"})])
    assert st == "INVALID"


# ── stance_flags ─────────────────────────────────────────────────────────────

GOOD = {"upgrade_if": ["by 2031-05-05: revenue growth back above 10%"],
        "downgrade_if": ["by 2031-03-15: recall cost above $5M"],
        "review_by": "2031-05-06", "stance_confidence": "medium",
        "research_status": "complete", "prompt_version": 5, "price_used": 12.34}
TODAY = dt.date(2031, 2, 3)


def stance(word="hold", ts="2031-02-03T10:15:00", **meta):
    return {"stance": word, "created_ts": ts, "research_meta": {**GOOD, **meta}}


def bars(*rows):
    return [{"date": d, "close": c, "low": lo, "high": hi} for d, c, lo, hi in rows]


def test_complete_stance_has_no_flags():
    assert rq.stance_flags(stance(), today=TODAY) == []
    assert rq.stance_flags(None) == [] and rq.stance_flags({"stance": None}) == []


def test_flag_research_incomplete():
    for rs in ("partial", "failed"):
        w = rq.stance_flags(stance("watch", research_status=rs), today=TODAY)
        assert len(w) == 1 and w[0].startswith(f"research incomplete (research_status: {rs})")


def test_flag_hold_or_watch_without_both_conditions():
    for word in ("hold", "watch"):
        w = rq.stance_flags(stance(word, upgrade_if=[], downgrade_if=[]), today=TODAY)
        assert w == [f"{word} with no conditions — treat as no stance "
                     "(missing: upgrade_if, downgrade_if)"]
    w = rq.stance_flags(stance("hold", downgrade_if=[]), today=TODAY)
    assert w == ["hold with no conditions — treat as no stance (missing: downgrade_if)"]
    # a note from before prompt v5 has no research_meta at all
    w = rq.stance_flags({"stance": "watch", "created_ts": "2026-10-01T09:00:00"}, today=TODAY)
    assert any("no conditions — treat as no stance" in x for x in w)
    # buy / trim / sell are actions, not waiting positions
    for word in ("buy", "trim", "sell"):
        assert rq.stance_flags(stance(word, upgrade_if=[], downgrade_if=[]), today=TODAY) == []


def test_flag_review_by_passed():
    w = rq.stance_flags(stance(review_by="2031-02-02"), today=TODAY)
    assert w == ["expired — review_by 2031-02-02 has passed; re-run research"]
    assert rq.stance_flags(stance(review_by="2031-02-03"), today=TODAY) == []   # due today
    # a date the parser of an older build stored as written is still read
    assert rq.stance_flags(stance(review_by="Feb 2, 2031"), today=TODAY) == \
        ["expired — review_by 2031-02-02 has passed; re-run research"]
    assert rq.stance_flags(stance(review_by="May 6, 2031"), today=TODAY) == []


def test_flag_review_by_unreadable_instead_of_dropping_it():
    w = rq.stance_flags(stance(review_by="soon"), today=TODAY)
    assert w == ["review_by unreadable: 'soon' — the stance has no review date"]
    # as the parser stores it: no review_by, the raw text under `unreadable`
    w = rq.stance_flags(stance(review_by=None, unreadable={"review_by": "after earnings"}),
                        today=TODAY)
    assert w == ["review_by unreadable: 'after earnings' — the stance has no review date"]


def test_parse_date_common_formats():
    want = dt.date(2027, 3, 9)
    for txt in ("2027-03-09", "2027-03-09T10:00:00", "Mar 9, 2027", "March 9, 2027",
                "Mar. 9, 2027", "March 9th, 2027", "9 Mar 2027", "9 March 2027",
                "03/09/2027", "2027/03/09", ' "Mar 9 2027" '):
        assert rq.parse_date(txt) == want, txt
    assert rq.parse_date(dt.datetime(2027, 3, 9, 10)) == want and rq.parse_date(want) == want
    for bad in ("soon", "", None, "Q1 2027", "after earnings", "13/40/2027"):
        assert rq.parse_date(bad) is None, bad


def test_flag_move_cause_not_found():
    w = rq.stance_flags(stance(move_cause="not_found"), today=TODAY)
    assert len(w) == 1 and "move_cause: not_found" in w[0]
    assert rq.stance_flags(stance(move_cause="found"), today=TODAY) == []
    assert rq.stance_flags(stance(move_cause="na"), today=TODAY) == []


def test_flag_price_used_off_the_session_close():
    # note written on a Saturday: the report price was Friday's close
    b = bars(("2031-01-30", 14.00, 13.80, 14.20), ("2031-01-31", 12.00, 11.90, 12.60))
    sat = "2031-02-01T11:00:00"
    assert rq.stance_flags(stance(ts=sat, price_used=12.00), b, TODAY) == []
    assert rq.stance_flags(stance(ts=sat, price_used=12.05), b, TODAY) == []       # +0.4%
    w = rq.stance_flags(stance(ts=sat, price_used=12.43), b, TODAY)                # +3.6%
    assert len(w) == 1
    assert "price_used $12.43 is 3.6% away from the 2031-01-31 session close $12.00" in w[0]
    # no price, no bars, or bars that start after the note → nothing to compare
    legacy = {"stance": "hold", "created_ts": sat,
              "research_meta": {k: GOOD[k] for k in ("upgrade_if", "downgrade_if", "review_by")}}
    assert rq.stance_flags(legacy, b, TODAY) == []
    assert rq.stance_flags(stance(ts=sat, price_used=99.0), None, TODAY) == []
    assert rq.stance_flags(stance(ts="2031-01-02T11:00:00", price_used=99.0), b, TODAY) == []


def test_price_used_intraday_note_may_use_a_live_price():
    b = bars(("2031-01-30", 14.00, 13.80, 14.20), ("2031-01-31", 12.00, 11.90, 13.60))
    during = "2031-01-31T10:30:00"            # Friday, market open
    # inside that day's range, or near the prior close (written pre-drop)
    assert rq.stance_flags(stance(ts=during, price_used=13.40), b, TODAY) == []
    assert rq.stance_flags(stance(ts=during, price_used=14.05), b, TODAY) == []
    assert len(rq.stance_flags(stance(ts=during, price_used=16.00), b, TODAY)) == 1
    # after the close that day, only the close counts
    assert len(rq.stance_flags(stance(ts="2031-01-31T17:00:00", price_used=13.40), b, TODAY)) == 1
    # written intraday on a day whose bar is not published yet → cannot judge
    only_thu = bars(("2031-01-30", 14.00, 13.80, 14.20))
    assert rq.stance_flags(stance(ts=during, price_used=12.10), only_thu, TODAY) == []


def test_after_hours_price_inside_3pct_is_flagged_when_written_off_hours():
    """With the market closed the report price IS the official close, so the
    tolerance is 0.5%: an after-hours quote 2.7% above the close used to pass
    the 3% check."""
    b = bars(("2031-01-30", 14.00, 13.80, 14.20), ("2031-01-31", 12.00, 11.90, 12.60))
    for when in ("2031-02-01T11:00:00",          # Saturday
                 "2031-01-31T18:30:00",          # Friday, after the close
                 "2031-02-03T08:00:00"):         # Monday, before the open
        w = rq.stance_flags(stance(ts=when, price_used=12.32), b, TODAY)
        assert len(w) == 1, when
        assert "price_used $12.32 is 2.7% away from the 2031-01-31 session close $12.00" in w[0]
        assert rq.stance_flags(stance(ts=when, price_used=12.04), b, TODAY) == [], when
    # a holiday is a closed market too (2026-11-26, Thanksgiving)
    hol = bars(("2026-11-24", 50.00, 49.50, 50.50), ("2026-11-25", 51.00, 50.20, 51.40))
    assert len(rq.stance_flags(stance(ts="2026-11-26T12:00:00", price_used=52.20),
                               hol, dt.date(2026, 11, 26))) == 1
    # before the open on a day whose bar exists, the report price was the
    # PREVIOUS close — not that day's close or range
    assert rq.stance_flags(stance(ts="2031-01-31T08:00:00", price_used=14.02), b, TODAY) == []
    w = rq.stance_flags(stance(ts="2031-01-31T08:00:00", price_used=12.00), b, TODAY)
    assert len(w) == 1 and "2031-01-30 session close $14.00" in w[0]
    # written while the session traded: the live-range allowance stays (3%)
    assert rq.stance_flags(stance(ts="2031-01-31T10:30:00", price_used=12.32), b, TODAY) == []


def test_flag_price_used_extended_hours_or_unreadable():
    w = rq.stance_flags(stance(price_used=12.34, price_used_note="extended-hours"), today=TODAY)
    assert w == ["price_used is an extended-hours price — the report price is the official close"]
    w = rq.stance_flags(stance(price_used=None, unreadable={"price_used": "Friday's close"}),
                        today=TODAY)
    assert w == ["price_used unreadable: 'Friday's close'"]


def test_flag_superseded_and_newer_unreadable_stance():
    s = {**stance(), "superseded_by": "2031-02-04T09:00:00"}
    assert rq.stance_flags(s, today=TODAY) == [
        "superseded by the note of 2031-02-04T09:00, which carries no readable stance — "
        "re-run research"]
    s = {**stance(), "newer_unreadable": {"created_ts": "2031-02-04T09:00:00",
                                         "value": "accumulate on dips"}}
    assert rq.stance_flags(s, today=TODAY) == [
        "stance unreadable: 'accumulate on dips' in the note of 2031-02-04T09:00 — the "
        "stance shown is from an older note"]


def test_flag_v5_note_missing_or_unreadable_required_fields():
    full = {"prompt_version": 5, "price_used": 12.0}
    assert rq.stance_flags(stance(**full), today=TODAY) == []
    bare = {"stance": "buy", "created_ts": "2031-02-03T10:15:00",
            "research_meta": {"prompt_version": 5}}
    assert rq.stance_flags(bare, today=TODAY) == [
        "missing required field(s): research_status, price_used, review_by, "
        "stance_confidence — the note does not follow prompt v5"]
    # an unreadable value is reported as unreadable, not as missing
    w = rq.stance_flags(stance(**full, research_status=None, stance_confidence=None,
                               unreadable={"research_status": "done",
                                           "stance_confidence": "very high"}), today=TODAY)
    assert w == ["unreadable field(s): research_status 'done', stance_confidence 'very high'"]
    # notes from before v5 are not held to the v5 contract
    legacy = {"stance": "buy", "created_ts": "2026-10-01T09:00:00"}
    assert rq.stance_flags(legacy, today=TODAY) == []


def test_avoid_label_for_sell_on_a_name_not_owned():
    assert rq.stance_label({"stance": "sell", "research_meta": {"owned": "no"}}) == "AVOID"
    assert rq.stance_label({"stance": "sell", "research_meta": {"owned": "yes"}}) == "SELL"
    assert rq.stance_label({"stance": "sell"}) == "SELL"
    assert rq.stance_label({"stance": "watch", "research_meta": {"owned": "no"}}) == "WATCH"
    # the stored value stays `sell`, which the scorecard scores as a bearish call
    from analysis.scorecard import _EXPECT
    assert _EXPECT["sell"] == -1


def test_flag_own_trigger_met():
    w = rq.stance_flags(stance(plan_triggers_met=["net debt above 2.5x EBITDA", "CFO left"]),
                        today=TODAY)
    assert w == ["your own trigger(s) met: net debt above 2.5x EBITDA; CFO left"]


def test_flags_accumulate_in_a_stable_order():
    w = rq.stance_flags(stance("watch", research_status="partial", upgrade_if=[],
                               review_by="2031-01-01", move_cause="not_found",
                               plan_triggers_met=["x"]), today=TODAY)
    assert [x.split(" ")[0] for x in w] == ["research", "watch", "expired", "the", "your"]


def test_stance_conditions_for_display():
    c = rq.stance_conditions(stance(plan_triggers_met=["x"]))
    assert c["upgrade_if"] == GOOD["upgrade_if"] and c["downgrade_if"] == GOOD["downgrade_if"]
    assert c["review_by"] == "2031-05-06" and c["confidence"] == "medium"
    assert c["plan_triggers_met"] == ["x"]
    empty = rq.stance_conditions({"stance": "hold"})
    assert empty["upgrade_if"] == [] and empty["review_by"] is None


# ── every ≥2σ session inside the note's news window ──────────────────────────

V5 = {"prompt_version": 5, "research_status": "complete"}
TUE = {"session_date": "2026-11-17", "z": 2.4, "move_pct": 6.1}


def test_sessions_to_explain_cover_the_window_not_only_the_last_session():
    t = nr._parse_ts("2026-11-23T18:30:00")          # Monday, after the close
    big = [TUE, FRI]
    # the last 5 sessions that had closed: Nov 17, 18, 19, 20, 23
    assert nr.sessions_to_explain(t, big) == [TUE, FRI]
    # one session later Tuesday Nov 17 has rolled out of the 5…
    t2 = nr._parse_ts("2026-11-24T18:30:00")
    assert nr.sessions_to_explain(t2, big) == [FRI]
    # …unless the note's own news_window reaches back to it
    assert nr.sessions_to_explain(t2, big, "2026-11-16..2026-11-24") == [TUE, FRI]
    # a stated window can widen the check and cannot narrow it
    assert nr.sessions_to_explain(t, big, "2026-11-23..2026-11-23") == [TUE, FRI]
    assert nr.sessions_to_explain(t, big, "not a window") == [TUE, FRI]
    # a session that had not closed when the note was written is not in it
    assert nr.sessions_to_explain(nr._parse_ts("2026-11-20T15:00:00"), big) == [TUE]
    # no readable time: the note is taken as current
    assert nr.sessions_to_explain(None, big, None, "2026-11-23") == [TUE, FRI]
    assert nr.sessions_to_explain(None, big) == []
    assert nr.parse_window("2026-11-17..2026-11-23") == ("2026-11-17", "2026-11-23")
    assert nr.parse_window("2026-11-17 → 2026-11-23") == ("2026-11-17", "2026-11-23")
    assert nr.parse_window("2026-11-23..2026-11-17") is None
    assert nr.parse_window(None) is None and nr.parse_window("last week") is None


def test_v5_note_needs_move_cause_found_for_a_big_session_in_its_window():
    """Anything other than `found` fails a v5 note when a ≥2σ session sits in
    its window — whatever the wording of news_note."""
    mon_eve = "2026-11-23T18:30:00"
    for cause, shown in (("na", "move_cause: na"), (None, "move_cause absent")):
        meta = {**V5, **({"move_cause": cause} if cause else {})}
        for text in ("Nov 17–23: no material company news",
                     "broad tech selloff, no obvious company catalyst identified",
                     "Nov 17–23: probe reported by two outlets"):
            (st, why), = status([note(mon_eve, news_view="neutral", news_note=text, meta=meta)],
                                CALM, MON, [FRI])
            assert st == "INVALID", (cause, text)
            assert why == ("its news check does not state the cause of the ≥2σ session of "
                           f"2026-11-20 ({shown}) — a v5 note needs move_cause: found for "
                           "every ≥2σ session in its window")
    # two sessions in the window are both named
    (st, why), = status([note(mon_eve, meta={**V5, "move_cause": "na"})], CALM, MON, [TUE, FRI])
    assert "≥2σ sessions of 2026-11-17 and 2026-11-20 (move_cause: na)" in why
    # found passes; not_found keeps its own reason
    assert status([note(mon_eve, meta={**V5, "move_cause": "found"})], CALM, MON, [FRI]) == \
        [("OK", "")]
    (st, why), = status([note(mon_eve, meta={**V5, "move_cause": "not_found"})], CALM, MON, [FRI])
    assert st == "INVALID" and "move_cause: not_found" in why
    # with no ≥2σ session in the window, "na" is the right answer
    assert status([note(mon_eve, meta={**V5, "move_cause": "na"})], CALM, MON, []) == [("OK", "")]
    assert status([note("2026-12-02T10:00:00", meta={**V5, "move_cause": "na"})], CALM,
                  {"session_date": "2026-12-01", "price_kind": "official_close"}, [FRI]) == \
        [("OK", "")]


def test_no_news_wording_is_used_only_for_notes_from_before_v5():
    selloff = "broad tech selloff, no obvious company catalyst identified"
    # before v5 there is no move_cause, so the wording is all there is
    assert status([note("2026-11-23T18:30:00", news_note="no material news")],
                  CALM, MON, [FRI])[0][0] == "INVALID"
    assert status([note("2026-11-23T18:30:00", news_note=selloff)], CALM, MON, [FRI]) == \
        [("OK", "")]
    # the same text in a v5 note fails on the structured field
    assert status([note("2026-11-23T18:30:00", news_note=selloff,
                        meta={**V5, "move_cause": "na"})], CALM, MON, [FRI])[0][0] == "INVALID"


def test_v5_note_with_unreadable_research_status_or_move_cause_is_invalid():
    (st, why), = status([note("2026-11-21T10:00:00",
                              meta={"prompt_version": 5,
                                    "unreadable": {"research_status": "mostly done"}})], CALM)
    assert st == "INVALID"
    assert why == ("its research_status could not be read ('mostly done') — the research "
                   "cannot be taken as complete")
    (st, why), = status([note("2026-11-21T10:00:00",
                              meta={**V5, "unreadable": {"move_cause": "probably macro"}})], CALM)
    assert st == "INVALID"
    assert why == ("its move_cause could not be read ('probably macro') — the news check "
                   "cannot be taken as done")
    # deleting the prompt_version line is not a way out: a note that carries
    # research_status or move_cause — readable or not — is a v5 note
    (st, why), = status([note("2026-11-21T10:00:00",
                              meta={"unreadable": {"research_status": "x"}})], CALM)
    assert st == "INVALID" and why.startswith("its research_status could not be read ('x')")
    (st, why), = status([note("2026-11-21T10:00:00",
                              meta={"unreadable": {"move_cause": "probably macro"}})], CALM)
    assert st == "INVALID" and why.startswith("its move_cause could not be read")
    # a note from before v5 carries none of those keys and is not held to them
    assert status([note("2026-11-21T10:00:00", meta={"unreadable": {"stance": "x"}})],
                  CALM) == [("OK", "")]
    # and the stance carries the flag
    w = rq.stance_flags(stance(research_status=None, prompt_version=5, price_used=12.0,
                               unreadable={"research_status": "mostly done"}), today=TODAY)
    assert w == ["unreadable field(s): research_status 'mostly done'"]


def test_session_explained_by_an_earlier_sound_note_is_not_asked_of_a_later_one():
    sat = note("2026-11-21T10:05:00", meta={**V5, "move_cause": "found"})
    mon = note("2026-11-23T18:30:00", meta={**V5, "move_cause": "na"})
    # newest first, as the app lists them
    assert status([mon, sat], CALM, MON, [FRI]) == [("OK", ""), ("OK", "")]
    got = rq.explained_sessions([mon, sat], CALM, MON, [FRI])
    assert list(got) == ["2026-11-20"] and got["2026-11-20"] == nr._parse_ts(sat["created_ts"])
    # without Saturday's note, Monday's is on its own
    assert status([mon], CALM, MON, [FRI])[0][0] == "INVALID"
    # an INVALID note explains nothing
    bad = note("2026-11-21T10:05:00",
               meta={**V5, "move_cause": "found", "research_status": "partial"})
    assert [s for s, _ in status([mon, bad], CALM, MON, [FRI])] == ["INVALID", "INVALID"]
    # …and neither does one that was unsound before it was superseded
    redo = note("2026-11-22T09:00:00", meta={"supersedes": ["2026-11-21T10:05:00"]})
    assert [s for s, _ in status([mon, redo, bad], CALM, MON, [FRI])] == \
        ["INVALID", "OK", "SUPERSEDED"]
    assert rq.explained_sessions([mon, redo, bad], CALM, MON, [FRI]) == {}
    # a sound note that was superseded keeps the sessions it explained
    assert [s for s, _ in status([mon, redo, sat], CALM, MON, [FRI])] == \
        ["OK", "OK", "SUPERSEDED"]
    assert rq.explained_sessions([mon, redo, sat], CALM, MON, [FRI]) == \
        {"2026-11-20": nr._parse_ts(sat["created_ts"])}
    # a note cannot be excused by one written AFTER it
    early = note("2026-11-21T09:00:00", meta={**V5, "move_cause": "na"})
    assert [s for s, _ in status([sat, early], BIG, BASIS)] == ["OK", "INVALID"]
    # STALE is about a later session: the note still explains the earlier one
    tue_move = {"z": 2.6, "move_pct": 7.0}
    wed = note("2026-11-25T10:00:00", meta={**V5, "move_cause": "found"})
    basis = {"session_date": "2026-11-24", "price_kind": "official_close"}
    got = status([wed, mon, sat], tue_move, basis, [FRI])
    assert [s for s, _ in got] == ["OK", "STALE", "STALE"]
    assert set(rq.explained_sessions([wed, mon, sat], tue_move, basis, [FRI])) == \
        {"2026-11-20", "2026-11-24"}


def test_follow_up_that_supersedes_the_sound_note_it_relied_on_stays_ok():
    """The exact sequence: Saturday's note is sound and states the cause of
    Friday's ≥2σ session. Monday's prompt therefore lists nothing in step 1,
    so the follow-up says `move_cause: na` as told — and lists Saturday's
    note in `supersedes`. Saturday's note used to stop counting as the
    explanation the moment it was superseded, which made the compliant
    follow-up INVALID for replacing the note it relied on."""
    sat = note("2026-11-21T10:05:00", meta={**V5, "move_cause": "found"})
    mon = note("2026-11-23T18:30:00",
               meta={**V5, "move_cause": "na", "supersedes": ["2026-11-21T10:05:00"]})
    assert status([mon, sat], CALM, MON, [FRI]) == [
        ("OK", ""), ("SUPERSEDED", "replaced by the note of 2026-11-23T18:30")]
    assert rq.explained_sessions([mon, sat], CALM, MON, [FRI]) == \
        {"2026-11-20": nr._parse_ts(sat["created_ts"])}
    # the flags on the follow-up's news check and stance agree
    row = {**mon, "news_view": "neutral", "news_note": "nothing new since Saturday"}
    assert nr.research_flags(row, {"stance": "hold", **row}, CALM, MON, sessions=[FRI],
                             explained=rq.explained_sessions([mon, sat], CALM, MON, [FRI])) \
        == {"news": [], "stance": []}
    # a third note written later is not asked for Friday's cause either
    tue = note("2026-11-24T18:30:00", meta={**V5, "move_cause": "na"})
    tue_basis = {"session_date": "2026-11-24", "price_kind": "official_close"}
    assert [s for s, _ in status([tue, mon, sat], CALM, tue_basis, [FRI])] == \
        ["OK", "OK", "SUPERSEDED"]
    # superseding does not rescue a follow-up when the first note was unsound:
    # it never explained Friday, so `na` is still the wrong answer
    partial = note("2026-11-21T10:05:00",
                   meta={**V5, "move_cause": "found", "research_status": "partial"})
    assert [s for s, _ in status([mon, partial], CALM, MON, [FRI])] == ["INVALID", "SUPERSEDED"]
    # nor when the first note never claimed a cause
    no_cause = note("2026-11-21T10:05:00", meta={"prompt_version": 5})
    assert [s for s, _ in status([mon, no_cause], CALM, MON, [FRI])] == \
        ["INVALID", "SUPERSEDED"]


def test_v5_is_recognized_without_the_prompt_version_line():
    """`prompt_version` used to be the only thing that made a note v5, so a
    note that dropped that line was judged by the wording rule for older
    notes: "broad tech selloff" with `move_cause: na` passed again."""
    selloff = "broad tech selloff, no obvious company catalyst identified"
    for meta in ({"move_cause": "na"}, {"research_status": "complete"},
                 {"news_window": "2026-11-17..2026-11-23"},
                 {"unreadable": {"move_cause": "N.A. (macro)"}},
                 {"unreadable": {"research_status": "done-ish"}}):
        n = note("2026-11-23T18:30:00", news_view="neutral", news_note=selloff, meta=meta)
        assert nr.is_v5(n), meta
        assert status([n], CALM, MON, [FRI])[0][0] == "INVALID", meta
    (st, why), = status([note("2026-11-23T18:30:00", news_note=selloff,
                              meta={"research_status": "complete"})], CALM, MON, [FRI])
    assert why == ("its news check does not state the cause of the ≥2σ session of 2026-11-20 "
                   "(move_cause absent) — a v5 note needs move_cause: found for every ≥2σ "
                   "session in its window")
    # prompt_version alone still counts, as a number or as text
    assert nr.is_v5(note("t", meta={"prompt_version": 5}))
    assert nr.is_v5(note("t", meta={"prompt_version": "6"}))
    # none of the v5 keys → a note from before v5
    for meta in ({}, {"prompt_version": 4}, {"prompt_version": "four"},
                 {"review_by": "2031-05-06", "upgrade_if": ["x"], "supersedes": ["y"]},
                 {"unreadable": {"stance": "accumulate"}}):
        assert not nr.is_v5(note("t", meta=meta)), meta
    assert not nr.is_v5(None) and not nr.is_v5({"research_meta": "not a dict"})
    # the stance checks follow: a note with research_status and no
    # prompt_version is held to the required v5 fields
    assert rq.stance_flags({"stance": "buy", "created_ts": "2031-02-03T10:15:00",
                            "research_meta": {"research_status": "complete"}}, today=TODAY) == [
        "missing required field(s): price_used, review_by, stance_confidence — the note "
        "does not follow prompt v5"]


def test_research_flags_for_a_v5_check_that_does_not_state_the_cause():
    row = {"news_view": "neutral", "news_note": "Nov 17–23: no material company news",
           "created_ts": "2026-11-23T18:30:00",
           "research_meta": {**V5, "move_cause": "na",
                             "news_window": "2026-11-17..2026-11-23"}}
    got = nr.research_flags(row, {"stance": "hold", **row}, CALM, MON, sessions=[FRI])
    assert got["news"] == [
        "does not state the cause of the −14.01% (−3.0σ) Fri Nov 20 session (move_cause: "
        "na) — a ≥2σ session in its window needs move_cause: found; treat this check as "
        "failed, not neutral"]
    assert got["stance"] == [
        "stance came from a discussion whose news check does not state the cause of the "
        "−14.01% (−3.0σ) Fri Nov 20 session (move_cause: na) — re-run research before acting"]
    # an earlier sound note already stated the cause → nothing to flag
    explained = {"2026-11-20": nr._parse_ts("2026-11-21T10:05:00")}
    assert nr.research_flags(row, {"stance": "hold", **row}, CALM, MON, sessions=[FRI],
                             explained=explained) == {"news": [], "stance": []}
    # two sessions: the latest in full, the earlier one by date
    got = nr.research_flags(row, None, CALM, MON, sessions=[TUE, FRI])
    assert "Fri Nov 20 session (and the ≥2σ session of 2026-11-17) (move_cause: na)" \
        in got["news"][0]


def test_news_check_from_partial_failed_or_unreadable_research_is_flagged():
    """The news row read "NEUTRAL — … nothing checked" with no warning."""
    for rs in ("partial", "failed"):
        row = {"news_view": "neutral", "news_note": "no search tool; nothing checked",
               "created_ts": "2026-11-21T10:05:00",
               "research_meta": {**V5, "research_status": rs, "move_cause": "found"}}
        assert nr.research_flags(row, None, CALM, BASIS)["news"] == [
            f"research incomplete (research_status: {rs}) — this news check was not "
            "completed; do not read it as a result"]
        # also with no price basis at all
        assert len(nr.research_flags(row, None, None, None)["news"]) == 1
    row = {"news_view": "neutral", "created_ts": "2026-11-21T10:05:00",
           "research_meta": {"prompt_version": 5,
                             "unreadable": {"research_status": "kind of",
                                            "move_cause": "unclear"}}}
    assert nr.research_flags(row, None, CALM, BASIS)["news"] == [
        "research_status unreadable: 'kind of' — this news check cannot be taken as completed",
        "move_cause unreadable: 'unclear' — the check does not say whether a cause was found"]


# ── which note is shown ──────────────────────────────────────────────────────

def _row(ts, stance=None, news_view=None, **meta):
    return {"created_ts": ts, "stance": stance, "news_view": news_view, "research_meta": meta}


def test_pick_stance_flags_a_newer_v5_note_that_carries_no_stance():
    old = _row("2031-02-01T09:00:00", "hold", **GOOD)
    got, orphan = rq.pick_stance([_row("2031-02-03T09:00:00", prompt_version=5), old])
    assert got["stance"] == "hold" and orphan == []
    assert got["newer_no_stance"] == "2031-02-03T09:00:00"
    assert rq.stance_flags(got, today=TODAY) == [
        "newer note of 2031-02-03T09:00 carries no stance — the stance shown is from an "
        "older note"]
    # a newer note from before v5 (a freeform note) is not a research note
    got, _ = rq.pick_stance([_row("2031-02-03T09:00:00"), old])
    assert "newer_no_stance" not in got and rq.stance_flags(got, today=TODAY) == []
    # an unreadable stance and a superseding note keep their own flags, once
    got, _ = rq.pick_stance([_row("2031-02-03T09:00:00", prompt_version=5,
                                  unreadable={"stance": "<hold>"}), old])
    assert "newer_no_stance" not in got and got["newer_unreadable"]["value"] == "<hold>"
    got, _ = rq.pick_stance([_row("2031-02-03T09:00:00", prompt_version=5,
                                  supersedes=["2031-02-01T09:00:00"]), old])
    assert got["superseded_by"] == "2031-02-03T09:00:00" and "newer_no_stance" not in got
    # nothing newer → nothing attached; no rows → nothing
    got, _ = rq.pick_stance([old])
    assert set(got) == {"created_ts", "stance", "news_view", "research_meta"}
    assert rq.pick_stance([]) == (None, [])


def test_pick_news_says_when_the_check_shown_is_not_the_newest_notes():
    old = _row("2031-02-01T09:00:00", news_view="bearish")
    assert rq.pick_news([old]) == (old, [])
    assert rq.pick_news([]) == (None, [])
    # the newest note's news_view could not be read
    got, flags = rq.pick_news([_row("2031-02-03T09:00:00",
                                    unreadable={"news_view": "cautiously optimistic"}), old])
    assert got["created_ts"] == "2031-02-01T09:00:00"
    assert flags == ["news_view unreadable: 'cautiously optimistic' in the note of "
                     "2031-02-03T09:00 — the news check shown is from an older note"]
    # the newest note has none at all
    got, flags = rq.pick_news([_row("2031-02-03T09:00:00"), old])
    assert got["news_view"] == "bearish"
    assert flags == ["the newest note (2031-02-03T09:00) carries no news_view — the news "
                     "check shown is from an older note"]
    # no older check to fall back on: nothing is shown, and the reason is given
    got, flags = rq.pick_news([_row("2031-02-03T09:00:00", unreadable={"news_view": "meh"})])
    assert got is None
    assert flags == ["news_view unreadable: 'meh' in the note of 2031-02-03T09:00 — no "
                     "news check is shown"]
    assert rq.pick_news([_row("2031-02-03T09:00:00")]) == (None, [])


def test_avoid_label_falls_back_to_the_account_and_a_mismatch_is_flagged():
    sell = {"stance": "sell", "created_ts": "2031-02-03T10:15:00", "research_meta": dict(GOOD)}
    # the note does not say → the account decides
    assert rq.stance_label({**sell, "account_owned": False}) == "AVOID"
    assert rq.stance_label({**sell, "account_owned": True}) == "SELL"
    assert rq.stance_label(sell) == "SELL"                       # no account data
    assert rq.stance_owned({**sell, "account_owned": False}) == "no"
    assert rq.stance_owned(sell) is None
    # the note says → the note decides the label…
    said_no = {**sell, "research_meta": {**GOOD, "owned": "no"}}
    assert rq.stance_label({**said_no, "account_owned": True}) == "AVOID"
    assert rq.stance_label({**sell, "research_meta": {**GOOD, "owned": "yes"},
                            "account_owned": False}) == "SELL"
    # …and a note that says "not owned" about a held position is flagged
    assert rq.stance_flags({**said_no, "account_owned": True}, today=TODAY) == [
        "the note says not owned (owned: no) but the account holds this position — the "
        "stance was written for a name not held; re-run research"]
    assert rq.stance_flags({**said_no, "account_owned": False}, today=TODAY) == []
    assert rq.stance_flags(said_no, today=TODAY) == []
