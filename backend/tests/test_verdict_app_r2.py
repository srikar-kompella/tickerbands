"""
Verdict round 2 (docs/VERDICT_SPEC.md): the less-favorable value grade,
two-step departures, price to sales for loss-makers, the inverted yield
rule, rule order, listed-under-a-year cap, rule_inputs arithmetic and its
check, owner_line / owner_note split, discussions rows with verdict fields,
SEC rule quarters and combined share-count tags. Fictional ZZ tickers only.
"""
import datetime as dt

import pytest

from analysis import report_view
from analysis import research_quality as rq
from analysis import value_lens as vl
from providers import sec_xbrl
from tests.conftest import write_md

TODAY = dt.date(2026, 11, 22)


def _bars(start, end, price_fn=lambda d: 50.0):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append({"date": d.isoformat(), "close": price_fn(d)})
        d += dt.timedelta(days=1)
    return out


def _row(meta, **kw):
    base = {"verdict": "fair", "value_grade": "fair", "risk_grade": "clean",
            "value_basis": "P/FCF 22 vs median 21", "research_status": "complete",
            "price_used": 50.0, "stance_confidence": "medium", "prompt_version": 6,
            "upgrade_if": ["u"], "downgrade_if": ["d"], "review_by": "2027-03-01",
            "stance_derived": True}
    base.update(meta)
    return {"stance": rq.VERDICT_TO_STANCE.get(base.get("verdict"), "hold"),
            "created_ts": "2026-11-21T10:00:00", "research_meta": base, **kw}


# ── S1: the less favorable grade; two-step departures ─────────────────────────

@pytest.mark.parametrize("a,b,want", [("cheap", "fair", "fair"), ("fair", "expensive", "expensive"),
                                      ("cheap", "expensive", "expensive"), ("fair", None, "fair"),
                                      (None, None, None)])
def test_combined_grade_is_the_less_favorable(a, b, want):
    assert rq.combined_grade(a, b) == want


def test_two_step_departure_is_always_flagged():
    vr = {"grade_hint": "expensive", "grade": "expensive", "now": 60.0, "median": 30.0,
          "high": 45.0, "label": "Price to free cash flow"}
    r = _row({"value_grade": "cheap", "verdict": "attractive",
              "value_basis": "normalized on peers instead of its own history"})
    f = rq.value_grade_flags(r, vr)
    assert len(f) == 1 and "two steps" in f[0] and "not allowed" in f[0]
    # one step with a departure word passes; without one it is flagged
    r = _row({"value_grade": "fair", "value_basis": "peer median 40x rather than own history"})
    assert rq.value_grade_flags(r, vr) == []
    r = _row({"value_grade": "fair", "value_basis": "adjusted for cyclical averages"})
    assert "gives no departure reason" in rq.value_grade_flags(r, vr)[0]


def test_summarize_range_sets_grade():
    vr = vl.summarize_range("p_fcf", 10.0, "b", [{"fy_end": f"202{i}-12-31", "value": v}
                                                 for i, v in enumerate((25, 25, 25))], [],
                            ttm_metric=5e9, annual_metric=[2e9, 2e9, 2e9])
    assert vr["grade_hint"] == "cheap" and vr["normalized_grade_hint"] == "fair"
    assert vr["grade"] == "fair" and vr["direction"] == "lower_is_cheaper"


# ── S3: yield-type measures are inverted ──────────────────────────────────────

@pytest.mark.parametrize("now,want", [(11.5, "cheap"), (11.4, "fair"), (7.0, "fair"),
                                      (6.6, "expensive"), (5.9, "expensive")])
def test_distribution_rate_grade_is_inverted(now, want):
    # median 10, low 6 → cheap ≥ 11.5; expensive < 6 or < 6.67
    assert vl.grade_hint(now, 10.0, 14.0, 6.0, inverted=True) == want


def test_distribution_points_dedupe_ex_dates_like_the_rate_now():
    bars = _bars(dt.date(2019, 1, 1), TODAY)
    # monthly payer whose ex-date drifts: 13 ex-dates within 365 days to 2025-12-31
    divs, d = [], dt.date(2024, 12, 2)
    while d <= dt.date(2025, 12, 31):
        divs.append({"date": d.isoformat(), "amount": 0.5})
        d += dt.timedelta(days=30)
    pts, _ = vl.distribution_points(bars, divs, TODAY)
    p25 = next(p for p in pts if p["fy_end"] == "2025-12-31")
    assert p25["paid"] == 6.0                          # 12 × 0.5, not 13 × 0.5


# ── S2: price to sales for a loss-maker ───────────────────────────────────────

def _annual_rev(ni, n=5):
    return [{"end": dt.date(2025 - i, 12, 31).isoformat(), "revenue": 10e9, "fcf": -1e9,
             "net_income": ni[i], "shares_diluted": 1e9,
             "shares_filed": dt.date(2026 - i, 2, 1).isoformat()} for i in range(n)]


def _fields(mc=50e9, price=50.0):
    return {"market_cap": {"value": mc, "status": "ok"}, "price": {"value": price, "status": "ok"}}


def test_loss_maker_is_measured_on_price_to_sales_not_paused():
    lh = {"available": True, "bars": _bars(dt.date(2020, 1, 1), TODAY), "splits": [],
          "dividends": []}
    stmts = {"available": True, "ttm": {"fcf": -1e9, "net_income": -2e9, "revenue": 11e9},
             "annual": _annual_rev([-2e9] * 5), "periods": {"income": "2026-09-30"}}
    vr = vl.build_valuation_range("stock", _fields(), stmts, lh, None, "USD", TODAY)
    assert vr["measure"] == "p_sales" and vr["status"] == "ok"
    assert vr["now"] == 4.55                           # 50B ÷ 11B
    assert vr["median"] == 5.0                         # 50 ÷ (10B / 1B)
    assert vr["grade_hint"] == "fair" and vr["grade"] == "fair"
    assert any("measured on price to sales" in w for w in vr["warnings"])


def test_negative_measure_years_fall_back_to_price_to_sales():
    lh = {"available": True, "bars": _bars(dt.date(2020, 1, 1), TODAY), "splits": [],
          "dividends": []}
    ann = _annual_rev([1e9, -1e9, -1e9, -1e9, 1e9])
    stmts = {"available": True, "ttm": {"fcf": -1e9, "net_income": 1e9, "revenue": 10e9},
             "annual": ann}
    f = {**_fields(), "pe_ttm": {"value": 50.0, "status": "ok", "basis": "b"}}
    vr = vl.build_valuation_range("stock", f, stmts, lh, None, "USD", TODAY)
    assert vr["measure"] == "p_sales" and len(vr["points"]) == 5
    assert any("fewer than 3 usable year-ends on price to earnings" in w for w in vr["warnings"])


def test_repeated_year_warnings_are_merged():
    w = vl.merge_year_warnings([
        "FY ended 2021-12-31: no price within 10 days of the year-end — left out",
        "FY ended 2020-12-31: no price within 10 days of the year-end — left out",
        "other note"])
    assert w == ["FY ended 2021-12-31, 2020-12-31: no price within 10 days of the year-end "
                 "— left out", "other note"]


# ── S6: rule order; S5: the cap by kind and listing age ───────────────────────

def test_failed_research_with_a_hard_flag_is_avoid():
    m = {"value_grade": "cheap", "research_status": "failed", "hard_flags": ["NT 10-Q"]}
    assert rq.expected_verdict(m) == "avoid"
    assert rq.expected_verdict({"value_grade": "cheap", "research_status": "failed"}) == "unrated"
    r = _row({"verdict": "avoid", "research_status": "failed", "hard_flags": ["NT 10-Q"],
              "risk_grade": "red_flag"})
    assert not any("rulebook" in x for x in rq.verdict_flags(r))


def test_cap_applies_to_stocks_and_banks_listed_under_a_year_not_funds():
    meta = {"verdict": "attractive", "value_grade": "cheap"}
    for kind in ("stock", "bank", "new_listing"):
        r = _row(meta, listed_under_a_year=True, security_kind=kind)
        assert any("expected fair" in x for x in rq.verdict_flags(r)), kind
    assert not any("rulebook" in x for x in rq.verdict_flags(
        _row(meta, listed_under_a_year=True, security_kind="fund")))
    # a public quarter filed lifts the cap
    assert not any("rulebook" in x for x in rq.verdict_flags(
        _row(meta, listed_under_a_year=True, security_kind="stock", public_quarter_filed=True)))
    assert rq.verdict_fields(_row(meta, listed_under_a_year=True))["expected_verdict"] == "fair"


def test_listing_facts():
    hist = _bars(dt.date(2026, 9, 1), dt.date(2026, 11, 20))
    f = report_view.listing_facts(hist, True, None, 6, TODAY,
                                  {"form": "10-Q", "filed": "2026-11-10"})
    assert f["sessions_listed"] == len(hist) and f["listed_under_a_year"] is True
    assert f["first_trade_date"] == "2026-09-01" and f["public_quarter_filed"] is True
    long_new = _bars(dt.date(2026, 2, 2), dt.date(2026, 11, 20))
    f = report_view.listing_facts(None, False, long_new, 6, TODAY)
    assert f["listed_under_a_year"] is True and f["sessions_listed"] == len(long_new) < 252
    long_old = _bars(dt.date(2020, 11, 20), dt.date(2026, 11, 20))
    f = report_view.listing_facts(None, False, long_old, 6, TODAY)
    assert f["listed_under_a_year"] is False and f["sessions_listed"] is None
    assert report_view.listing_facts(None, False, None, 6, TODAY)["listed_under_a_year"] is None


# ── rule_inputs arithmetic ────────────────────────────────────────────────────

def _q(end, **kw):
    base = {"end": end, "revenue": 100.0, "operating_income": 20.0, "pretax_income": 20.0,
            "net_income": 16.0, "tax": 4.0, "fcf": 15.0, "interest_expense": 1.0,
            "d_and_a": 5.0, "shares_diluted": 100.0, "receivables": 50.0, "inventory": 30.0,
            "cash_st": 100.0, "debt": 50.0}
    base.update(kw)
    return base


ENDS = ["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30",
        "2025-03-31", "2024-12-31", "2024-09-30"]


def _rules(qs, kind="stock", **kw):
    out = vl.build_rule_inputs(kind, {"rule_quarters": qs,
                                      "rule_filing": {"form": "10-Q", "filed": "2026-10-30"}},
                               [], TODAY, **kw)
    return {r["key"]: r for r in out["rules"]}, out


def test_quiet_company_fires_nothing():
    rules, out = _rules([_q(e) for e in ENDS])
    assert out["status"] == "ok" and out["as_of"] == "2026-09-30" and out["filed"] == "2026-10-30"
    assert {k for k, r in rules.items() if r["fires"]} == set()
    assert rules["interest_coverage"]["values"][0]["coverage"] == 20.0
    assert rules["net_debt_ebitda"]["fires"] is False                 # net cash


def test_revenue_margin_and_working_capital_rules_fire_in_both_quarters():
    qs = [_q(e) for e in ENDS]
    for i in (0, 1):
        qs[i] = _q(ENDS[i], revenue=90.0, operating_income=12.0, receivables=70.0,
                   inventory=45.0)
    rules, _ = _rules(qs)
    assert rules["revenue_decline"]["fires"] is True
    assert rules["revenue_decline"]["values"][0]["growth_pct"] == -10.0
    assert rules["op_margin_decline"]["fires"] is True                # 13.3% vs 20%
    assert rules["receivables_vs_revenue"]["fires"] is True           # +40% vs −10%
    assert rules["inventory_vs_revenue"]["fires"] is True
    # only one quarter → does not fire
    qs[1] = _q(ENDS[1])
    rules, _ = _rules(qs)
    assert rules["revenue_decline"]["fires"] is False


def test_fcf_negative_skipped_for_a_fast_grower_and_missing_is_null():
    qs = [_q(e) for e in ENDS]
    qs[0] = _q(ENDS[0], fcf=-5.0, revenue=140.0)
    qs[1] = _q(ENDS[1], fcf=-5.0)
    rules, _ = _rules(qs)
    assert rules["fcf_negative"]["fires"] is False
    assert any("skipped" in w for w in rules["fcf_negative"]["warnings"])
    qs[0] = _q(ENDS[0], fcf=None)
    rules, _ = _rules(qs)
    assert rules["fcf_negative"]["fires"] is None and rules["ni_over_fcf"]["fires"] is None


def test_ni_over_fcf_uses_the_adjusted_net_income():
    qs = [_q(e) for e in ENDS]
    qs[0] = _q(ENDS[0], net_income=30.0, fcf=10.0)
    qs[1] = _q(ENDS[1], net_income=30.0, fcf=10.0)
    rules, _ = _rules(qs)
    assert rules["ni_over_fcf"]["fires"] is True
    # a tax benefit of 14 on pretax 20 → taxed at 21%: 15.8 vs fcf 15 → not met
    qs[0] = _q(ENDS[0], net_income=34.0, tax=-14.0, fcf=15.0)
    qs[1] = _q(ENDS[1], net_income=34.0, tax=-14.0, fcf=15.0)
    rules, _ = _rules(qs)
    v = rules["ni_over_fcf"]["values"][0]
    assert v["net_income"] == 34.0 and v["net_income_adj"] == pytest.approx(15.8)
    assert rules["ni_over_fcf"]["fires"] is False
    assert any("tax benefit" in w for w in rules["ni_over_fcf"]["warnings"])


def test_leverage_coverage_and_dilution():
    qs = [_q(e, debt=400.0, cash_st=10.0, interest_expense=8.0) for e in ENDS]
    for i in range(4, 9):
        qs[i] = _q(ENDS[i], debt=300.0, cash_st=10.0, interest_expense=8.0, shares_diluted=90.0)
    rules, _ = _rules(qs)
    # net debt 390 vs TTM EBITDA 100 → 3.9x, higher than 290 a year earlier
    assert rules["net_debt_ebitda"]["fires"] is True
    assert rules["net_debt_ebitda"]["values"][0]["ratio"] == 3.9
    assert rules["interest_coverage"]["fires"] is True               # 80 / 32 = 2.5x
    assert rules["dilution"]["fires"] is True                        # +11.1%
    rules, _ = _rules(qs, sessions_listed=120)
    assert rules["dilution"]["fires"] is False


def test_bank_rules_and_nulls():
    qs = [_q(e, revenue=None, net_interest_income=60.0, noninterest_income=40.0,
             noninterest_expense=75.0, deposits=1000.0) for e in ENDS]
    qs[0]["deposits"] = 900.0
    rules, out = _rules(qs, kind="bank")
    assert out["kind"] == "bank"
    assert rules["efficiency_ratio"]["fires"] is True                # 75%
    assert rules["deposit_decline"]["fires"] is True                 # −10%
    assert rules["npl_rise"]["fires"] is None
    assert "op_margin_decline" not in rules and "net_debt_ebitda" not in rules


def test_fund_distribution_cut():
    ev = [{"date": f"2026-{m:02d}-15", "amount": 0.5} for m in range(1, 11)]
    ev.append({"date": "2026-11-14", "amount": 0.4})
    out = vl.build_rule_inputs("fund", None, ev, TODAY)
    r = {x["key"]: x for x in out["rules"]}["distribution_cut"]
    assert r["fires"] is True and r["values"][0]["months"] == 3
    assert r["values"][0]["change_pct"] == -20.0


def test_no_filed_quarters_gives_no_rules():
    out = vl.build_rule_inputs("stock", {"available": True}, [], TODAY)
    assert out["rules"] == [] and out["status"] == "no_data" and out["warnings"]


# ── the soft-signal check ─────────────────────────────────────────────────────

def test_soft_signals_checked_against_rule_inputs():
    ri = {"filed": "2026-10-30", "rules": [
        {"key": "ni_over_fcf", "fires": True, "threshold": "> 1.25", "summary": "ratio 2.0"},
        {"key": "revenue_decline", "fires": False, "threshold": "< 0", "summary": "+5%"},
        {"key": "dilution", "fires": None}]}
    r = _row({"soft_signals": ["[revenue_decline] revenue down (10-Q)"]}, rule_inputs=ri)
    f = rq.rule_input_flags(r, ri)
    assert any(x.startswith("soft signal revenue_decline does not match the app's arithmetic")
               and "not met" in x for x in f)
    assert any(x.startswith("soft signal ni_over_fcf does not match") and "no [ni_over_fcf]" in x
               for x in f)
    assert len(f) == 2
    ok = _row({"soft_signals": ["[ni_over_fcf] NI 2× FCF (10-Q)"], "verdict": "fair"})
    assert rq.rule_input_flags(ok, ri) == []
    # statements filed after the note → not checked
    assert rq.rule_input_flags(r, {**ri, "filed": "2026-11-30"}) == []
    # verdict_flags runs it when attached to the row
    assert any("does not match the app's arithmetic" in x for x in rq.verdict_flags(r))


# ── owner line / note, discussions rows ───────────────────────────────────────

def test_owner_line_excludes_the_note():
    r = _row({"verdict": "stretched", "owner_note": "your rule — trim above 10% of the account "
                                                    "— is not met: hold"}, account_owned=True)
    vf = rq.verdict_fields(r)
    assert vf["owner_line"] == "You own it: consider trimming"
    assert vf["owner_note"].startswith("your rule")


V6 = [
    'ticker: "ZZRV"', "created_ts: 2026-11-21T10:00:00", "prompt_version: 6",
    'summary: "Fair"', 'research_status: "complete"', 'move_cause: "na"',
    'verdict: "fair"', 'value_grade: "fair"', 'risk_grade: "clean"',
    'value_basis: "P/FCF 24 vs median 23"', 'verdict_note: "Fair on its own history"',
    'owner_note: "your rule — trim above 10% of the account — is not met: hold"',
    'stance_confidence: "medium"', 'price_used: 101.5',
    'upgrade_if: ["x"]', 'downgrade_if: ["y"]', 'review_by: "2027-03-01"',
]


@pytest.fixture()
def client(tmp_db, mock_mode, tmp_discussions, monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "DISCUSSIONS_DIR", str(tmp_discussions))
    from fastapi.testclient import TestClient
    from app import app
    with TestClient(app) as c:
        yield c


def test_discussions_rows_carry_verdict_fields(client, tmp_discussions):
    from discussions_svc.watcher import catch_up_scan
    write_md(tmp_discussions, "ZZRV", "2026-11-21T10-00-00.md", V6, body="## TL;DR\nFair.")
    write_md(tmp_discussions, "ZZRV", "2026-11-10T10-00-00.md", [
        'ticker: "ZZRV"', "created_ts: 2026-11-10T10:00:00", "prompt_version: 5",
        'stance: "hold"', 'research_status: "complete"', 'move_cause: "na"'], body="old")
    catch_up_scan()
    rows = client.get("/api/discussions", params={"ticker": "ZZRV"}).json()
    new = next(r for r in rows if r["created_ts"].startswith("2026-11-21"))
    assert new["format"] == "v6" and new["verdict"] == "fair"
    assert new["value_grade"] == "fair" and new["risk_grade"] == "clean"
    assert new["verdict_label"] == "Fair (Fair · Clean)"
    old = next(r for r in rows if r["created_ts"].startswith("2026-11-10"))
    assert old["format"] == "earlier" and old["verdict"] is None
    # analysis payload: owner_line and owner_note apart; the export prints the note once
    a = client.get("/api/analysis/ZZRV").json()
    cs = a["claude_stance"]
    assert "your rule" not in (cs["owner_line"] or "") and cs["owner_note"].startswith("your rule")
    assert "listed_under_a_year" in a and "sessions_listed" in a and "rule_inputs" in a
    html = client.get("/api/analysis/ZZRV/report").text
    assert html.count("trim above 10% of the account") == 1


# ── SEC: rule quarters and combined share-count tags ──────────────────────────

def _f(start, end, val, form="10-Q", filed="2026-11-01"):
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed, "accn": "a"}


def _i(end, val, filed="2026-11-01"):
    return {"end": end, "val": val, "form": "10-Q", "filed": filed, "accn": "a"}


def test_sec_rule_quarters_and_combined_share_tags():
    qs = [("2026-07-01", "2026-09-30"), ("2026-04-01", "2026-06-30"),
          ("2025-07-01", "2025-09-30"), ("2025-04-01", "2025-06-30")]
    us = {
        "Revenues": {"units": {"USD": [_f(s, e, 100.0) for s, e in qs]
                               + [_f("2024-01-01", "2024-12-31", 380.0, "10-K"),
                                  _f("2023-01-01", "2023-12-31", 350.0, "10-K")]}},
        "NetIncomeLoss": {"units": {"USD": [_f(s, e, 10.0) for s, e in qs]}},
        "InterestExpense": {"units": {"USD": [_f(s, e, -2.0) for s, e in qs]}},
        "AccountsReceivableNetCurrent": {"units": {"USD": [_i(e, 40.0) for _, e in qs]}},
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [_i(e, 5.0) for _, e in qs]}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
            _f(s, e, 1000.0) for s, e in qs]}},
        # an older year only under the other tag
        "WeightedAverageNumberOfShareOutstandingBasicAndDiluted": {"units": {"shares": [
            _f("2023-01-01", "2023-12-31", 900.0, "10-K")]}},
    }
    out = sec_xbrl.normalize({"facts": {"us-gaap": us}})
    rq_ = out["rule_quarters"]
    assert rq_[0]["end"] == "2026-09-30" and rq_[0]["receivables"] == 40.0
    assert rq_[0]["interest_expense"] == 2.0 and rq_[0]["shares_diluted"] == 1000.0
    a23 = next(a for a in out["annual"] if a["end"] == "2023-12-31")
    assert a23["shares_diluted"] == 900.0


def test_rule_check_is_skipped_when_the_filed_quarters_are_behind_the_release():
    """Two sessions counted the quarter in the earnings release and wrote
    [ni_over_fcf]; the app's arithmetic, on filed quarters only, said not met.
    The session is ahead of the arithmetic, not contradicting it."""
    from analysis import research_quality as rq
    row = {"created_ts": "2031-03-10T10:00:00", "stance": "trim",
           "research_meta": {"prompt_version": 6, "verdict": "stretched", "value_grade": "fair",
                             "risk_grade": "watch",
                             "soft_signals": ["[ni_over_fcf] profit above cash flow in both"]}}
    rules = {"rules": [{"key": "ni_over_fcf", "fires": False, "threshold": "> 25% in both",
                        "summary": "2.5 and 0.93"}], "filed": "2031-01-20"}
    assert rq.rule_input_flags(row, rules)                               # flagged normally
    assert rq.rule_input_flags(row, {**rules, "behind_release": "2031-03-01"}) == []
