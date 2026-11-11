"""PEAD screen: near-zero EPS estimates must not produce huge surprise-% leads.

Regression coverage for the "beat EPS estimates by 1102%" Discover lead: the
surprise percentage divides by abs(est), so a few-cents estimate turns any
beat into a huge number. The screen now skips estimates below a $0.05 floor,
and the lead text shows the dollar beat so the reader can judge it.
"""
from analysis.discovery import list_candidates, screen_pead


def _calendar(events):
    return lambda _from, _to: events


def test_near_zero_estimate_is_skipped(tmp_db):
    events = [
        # $0.12 beat on a $0.01 estimate would read as ~1200% without the floor
        {"symbol": "TINY", "epsEstimate": 0.01, "epsActual": 0.13,
         "date": "2026-01-05"},
        # a negative near-zero estimate is floored the same way
        {"symbol": "NEGY", "epsEstimate": -0.02, "epsActual": 0.10,
         "date": "2026-01-05"},
    ]
    assert screen_pead(_calendar(events)) == 0
    assert list_candidates() == []


def test_normal_estimate_lead_shows_dollar_beat(tmp_db):
    events = [
        {"symbol": "NRML", "epsEstimate": 1.00, "epsActual": 1.25,
         "date": "2026-01-05"},
    ]
    assert screen_pead(_calendar(events)) == 1
    rows = list_candidates()
    assert len(rows) == 1
    reason = rows[0]["reasons"][-1]["reason"]
    assert "25%" in reason
    assert "$1.25 vs $1.00 est" in reason
