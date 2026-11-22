"""
Data-correctness fixes of 2026-11-22 (one test group per fix). Fixtures use
illustrative numbers and fictional ZZ tickers.

 1. options: the ATM straddle is one call + one put of the nearest expiry
 2. trailing yield: a regular payer's 13th ex-date in 365 days is not counted
 3. forward yield: not computed for variable / irregular payers
 4. one-time income-tax benefit flagged on EPS, P/E, net margin
 5. new listings: balance sheet before the listing; share-count flags
 6. `security_kind` on the payload: stock | fund | bank | new_listing
 7. funds: no company-fundamentals rows; fund rows; a group on every field
 8. banks: EV / net cash / FCF rows suppressed; P/TBV and ROE
 9. beta not meaningful when the correlation is near zero
10. gross margin derived from revenue − cost of revenue (SEC path)
11. value lens: CAGR names its fiscal years; acquisitions row relabeled
12. analyst actions: initiations counted apart; an empty feed is not zero
13. earnings 8-K: the EX-99.1 exhibit URL
"""
import datetime as dt

import pytest

from analysis import market_clock as mc
from analysis import report_view, statements, value_lens
from tests import replay_fixtures as rf

NOW = mc.to_ny(dt.datetime(2031, 11, 22, 12, 0, tzinfo=dt.timezone.utc))
B, M = 1e9, 1e6


def _view(**inp):
    base = {"symbol": "ZZAA", "now": NOW,
            "basis": {"price": 50.0, "price_kind": "close", "session_date": "2031-11-21",
                      "prior_date": "2031-11-20", "price_label": "Official close"}}
    base.update(inp)
    v = report_view.build(base)
    report_view.apply_checks(v)
    return v


def _div(events, **kw):
    return {"available": True, "source": "test", "as_of": "2031-11-22",
            "events": [{"date": d, "amount": a} for d, a in events], **kw}


@pytest.fixture()
def client(tmp_db, mock_mode, tmp_discussions, monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "DISCUSSIONS_DIR", str(tmp_discussions))
    from fastapi.testclient import TestClient
    from app import app
    with TestClient(app) as c:
        yield c


# ── 1. options: one expiry, one strike, both legs ────────────────────────────

def _c(strike, dte, mark, iv=40.0, vol=10, **kw):
    return {"strikePrice": strike, "daysToExpiration": dte, "mark": mark,
            "volatility": iv, "totalVolume": vol, **kw}


def _chain(calls, puts, spot=101.0):
    def side(rows):
        out = {}
        for exp, dte, strike, mark, iv in rows:
            out.setdefault(f"{exp}:{dte}", {})[f"{strike:.1f}"] = [_c(strike, dte, mark, iv)]
        return out
    return {"status": "SUCCESS", "underlyingPrice": spot,
            "callExpDateMap": side(calls), "putExpDateMap": side(puts)}


def test_straddle_uses_nearest_expiry_and_one_strike_for_both_legs():
    """The monthly has a strike right at spot ($101); the weekly only $100
    and $105. Picking each leg by strike distance first paired the monthly's
    legs (26 days) — the implied move must come from the weekly."""
    from providers.schwab import summarize_chain
    chain = _chain(
        calls=[("2031-11-28", 5, 100.0, 2.60, 38.0), ("2031-11-28", 5, 105.0, 0.90, 37.0),
               ("2031-12-19", 26, 101.0, 4.80, 41.0)],
        puts=[("2031-11-28", 5, 100.0, 1.70, 42.0), ("2031-11-28", 5, 95.0, 0.50, 45.0),
              ("2031-12-19", 26, 101.0, 4.70, 43.0)])
    o = summarize_chain(chain)
    assert o["expiry_date"] == "2031-11-28" and o["implied_move_dte"] == 5
    assert o["atm_strike"] == 100.0
    assert o["implied_move_pct"] == pytest.approx((2.60 + 1.70) / 101.0 * 100, abs=0.01)
    assert o["atm_iv_pct"] == pytest.approx(40.0)          # (38 + 42) / 2, same pair


def test_straddle_strike_must_exist_on_both_sides():
    """Weekly: the call nearest spot is $101, but there is no $101 put — the
    pair is the nearest strike BOTH legs have ($100), never a $101 call with
    a $100 put."""
    from providers.schwab import summarize_chain
    chain = _chain(
        calls=[("2031-11-28", 5, 101.0, 2.00, 38.0), ("2031-11-28", 5, 100.0, 2.60, 38.0)],
        puts=[("2031-11-28", 5, 100.0, 1.70, 42.0)])
    o = summarize_chain(chain)
    assert o["atm_strike"] == 100.0
    assert o["implied_move_pct"] == pytest.approx(4.30 / 101.0 * 100, abs=0.01)


def test_straddle_skips_an_expiry_missing_a_leg_and_never_mixes_expiries():
    from providers.schwab import summarize_chain
    # the weekly has calls only → the first expiry with both legs is used
    o = summarize_chain(_chain(
        calls=[("2031-11-28", 5, 100.0, 2.60, 38.0), ("2031-12-19", 26, 100.0, 5.0, 41.0)],
        puts=[("2031-12-19", 26, 100.0, 4.5, 43.0)]))
    assert o["expiry_date"] == "2031-12-19" and o["implied_move_dte"] == 26
    assert o["implied_move_pct"] == pytest.approx(9.5 / 101.0 * 100, abs=0.01)
    # calls in one expiry, puts in another → no straddle at all
    o = summarize_chain(_chain(calls=[("2031-11-28", 5, 100.0, 2.60, 38.0)],
                               puts=[("2031-12-19", 26, 100.0, 4.5, 43.0)]))
    assert o["available"] is True and o["implied_move_pct"] is None
    assert o["expiry_date"] is None and o["atm_iv_pct"] is None
    # a put with no mark does not count as a leg; nothing past 45 days is used
    o = summarize_chain(_chain(
        calls=[("2031-11-28", 5, 100.0, 2.60, 38.0), ("2032-02-20", 90, 100.0, 9.0, 40.0)],
        puts=[("2031-11-28", 5, 100.0, 0.0, 42.0), ("2032-02-20", 90, 100.0, 8.0, 40.0)]))
    assert o["implied_move_pct"] is None


def test_mock_options_carry_expiry_and_strike():
    from providers import mock
    o = mock.fetch_options_summary("ZZAA")
    assert o["expiry_date"] and o["atm_strike"] > 0
    assert (dt.date.fromisoformat(o["expiry_date"]) - mock._ny_today()).days == o["implied_move_dte"]


def test_implied_move_row_names_the_strike():
    v = _view(options={"available": True, "put_call_ratio": 0.5, "atm_iv_pct": 40.0,
                       "implied_move_pct": 4.26, "implied_move_dte": 5,
                       "expiry_date": "2031-11-28", "atm_strike": 100.0,
                       "as_of": "2031-11-21T20:30:00+00:00"},
              band={"half_width_pct": 2.0, "low": 49, "high": 51})
    f = v["fields"]["implied_move"]
    assert "100 strike" in f["basis"] and "expiry " in f["basis"]


# ── 2. trailing yield: 12 monthly payments, not 13 ───────────────────────────

MONTHLY_13 = [("2030-11-26", 0.63), ("2030-12-24", 0.64), ("2031-01-21", 0.64),
              ("2031-02-18", 0.61), ("2031-03-18", 0.61), ("2031-04-22", 0.63),
              ("2031-05-20", 0.66), ("2031-06-16", 0.66), ("2031-07-22", 0.64),
              ("2031-08-19", 0.65), ("2031-09-16", 0.63), ("2031-10-21", 0.63),
              ("2031-11-18", 0.58)]


def test_trailing_yield_counts_one_years_worth_of_a_monthly_schedule():
    """Ex-dates drift inside the month: 13 of them fall in the 365 days to
    2031-11-22. The yield is on the latest 12."""
    v = _view(dividends=_div([("2030-09-24", 0.64), ("2030-10-22", 0.64)] + MONTHLY_13))
    f = v["fields"]["div_yield_trailing"]
    paid12 = sum(a for _, a in MONTHLY_13[1:])
    assert f["payments"] == 12
    assert f["value"] == pytest.approx(paid12 / 50.0 * 100, abs=0.005)
    assert "12 payments" in f["basis"] and "not counted" in f["basis"]
    # the old figure (all 13) would have been ~8% higher
    assert f["value"] < sum(a for _, a in MONTHLY_13) / 50.0 * 100 - 1.0


def test_trailing_window_start_is_exclusive_and_end_inclusive():
    kept, drift = report_view.trailing_distributions(
        [{"date": "2030-11-22", "amount": 1.0},      # exactly 365 days back: out
         {"date": "2030-11-23", "amount": 2.0},
         {"date": "2031-11-22", "amount": 4.0},      # today: in
         {"date": "2031-11-23", "amount": 8.0}],     # tomorrow: out
        dt.date(2031, 11, 22))
    assert sum(e["amount"] for e in kept) == 6.0 and drift == 0


def test_trailing_keeps_a_special_paid_between_regular_dividends():
    """Five ex-dates in the window, but one is a special 7 days after a
    regular payment — that is money paid, not calendar drift."""
    ev = [("2030-12-10", 0.25), ("2031-03-11", 0.25), ("2031-06-10", 0.25),
          ("2031-09-09", 0.25), ("2031-09-16", 1.00)]
    kept, drift = report_view.trailing_distributions(
        [{"date": d, "amount": a} for d, a in ev], dt.date(2031, 11, 22), 4)
    assert len(kept) == 5 and drift == 0


def test_payload_ttm_yield_matches_the_report_row(tmp_db, monkeypatch):
    from analysis import composer
    p = rf.nvda_providers()
    p["dividend_history"] = lambda s: _div(
        [(d.replace("2031", "2026").replace("2030", "2025"), a) for d, a in MONTHLY_13])
    rf.install(monkeypatch, p)
    a = composer.analyze("NVDA", now=rf.NVDA_NOW)
    row = a["report_view"]["fields"]["div_yield_trailing"]
    assert row["payments"] == 12
    assert a["fundamentals_ttm"]["yield_ttm"]["value"] == pytest.approx(row["value"], abs=0.01)


# ── 3. forward yield: only for a steady payer ────────────────────────────────

def test_forward_yield_withheld_for_a_variable_irregular_payer():
    """ZZUF: $0.004 after payments of $0.06–0.09 on an uneven schedule. A
    "0.02% forward yield" is wrong; blank with the reason is right."""
    ev = [("2030-03-27", 0.012), ("2030-06-27", 0.075), ("2030-12-29", 0.09),
          ("2031-06-27", 0.058), ("2031-09-29", 0.004)]
    v = _view(dividends=_div(ev))
    fwd, trl = v["fields"]["div_yield_forward"], v["fields"]["div_yield_trailing"]
    assert fwd["value"] is None and fwd["display"] == "—" and fwd["status"] == "withheld"
    assert any("forward yield not computed" in w and "irregular" in w and "92–185 days" in w
               for w in fwd["warnings"])
    assert trl["value"] == pytest.approx((0.09 + 0.058 + 0.004) / 50.0 * 100, abs=0.001)
    assert trl["status"] == "ok"


def test_forward_yield_withheld_when_the_latest_payment_is_far_from_the_median():
    ev = [(f"2031-{m:02d}-15", 0.50) for m in range(1, 11)] + [("2031-11-14", 0.05)]
    fwd = _view(dividends=_div(ev))["fields"]["div_yield_forward"]
    assert fwd["value"] is None
    assert any("variable distributions" in w and "90% below the median" in w
               and "$0.5" in w for w in fwd["warnings"])


def test_forward_yield_computed_for_steady_and_mildly_variable_payers():
    q = [("2030-12-10", 0.278), ("2031-03-25", 0.257), ("2031-06-24", 0.253),
         ("2031-09-23", 0.267)]
    fwd = _view(dividends=_div(q))["fields"]["div_yield_forward"]
    assert fwd["value"] == pytest.approx(0.267 * 4 / 50.0 * 100, abs=0.005)
    assert fwd["warnings"] == [] and "× 4/yr" in fwd["basis"]
    # monthly, amounts within ~10% of each other: computed, no warning
    fwd = _view(dividends=_div(MONTHLY_13))["fields"]["div_yield_forward"]
    assert fwd["value"] == pytest.approx(0.58 * 12 / 50.0 * 100, abs=0.005)
    assert fwd["warnings"] == []


def test_forward_yield_follows_a_raise_once_it_has_been_paid_twice():
    raised = [("2030-12-04", 0.01), ("2031-03-11", 0.01), ("2031-06-10", 0.25),
              ("2031-09-10", 0.25)]
    fwd = _view(dividends=_div(raised))["fields"]["div_yield_forward"]
    assert fwd["value"] == pytest.approx(0.25 * 4 / 50.0 * 100, abs=0.005)
    # the first payment at a new level is not yet a schedule
    first = raised[:3]
    fd = report_view.forward_distribution([{"date": d, "amount": a} for d, a in first])
    assert fd["ok"] is False and "has not repeated" in fd["why"]


def test_non_payer_forward_yield_is_a_real_zero():
    fwd = _view(dividends=_div([]))["fields"]["div_yield_forward"]
    assert fwd["value"] == 0.0 and fwd["display"] == "0.00%" and fwd["status"] == "ok"


# ── 4. one-time tax benefit ──────────────────────────────────────────────────

def _stmts(**over):
    ttm = {"revenue": 5.8 * B, "gross_profit": 2.8 * B, "operating_income": 0.40 * B,
           "pretax_income": 0.33 * B, "net_income": 1.27 * B, "tax": -1.0 * B,
           "eps_diluted": 7.96, "fcf": 1.1 * B, "ebit": 0.40 * B}
    ttm.update(over.pop("ttm", {}))
    s = {"available": True, "ttm": ttm, "annual": [], "quarters": [],
         "balance": {"total_debt": 1.0 * B, "cash": 2.9 * B, "cash_st": 2.9 * B,
                     "equity": 8.0 * B, "as_of_date": "2031-09-30"},
         "periods": {"income": "2031-09-30", "cashflow": "2031-09-30",
                     "balance": "2031-09-30"},
         "latest_quarter_end": "2031-09-30", "source": "test statements",
         "as_of": "2031-11-22"}
    s.update(over)
    return s


def test_tax_benefit_is_flagged_on_eps_pe_and_net_margin():
    """ZZTW: net margin 21.9% beside an operating margin of 6.9% — a $1.0B
    deferred-tax benefit in the tax line."""
    F = _view(statements=_stmts(), shares_out_m=150.0)["fields"]
    for key in ("eps_ttm", "pe_ttm", "net_margin"):
        w = [x for x in F[key]["warnings"] if "income-tax benefit" in x]
        assert w, key
        assert "$1.0B" in w[0] and "pretax income of $330.0M" in w[0] and "one-time" in w[0]
        assert "about $260.7M" in w[0]                       # 0.33B × (1 − 21%)
    assert F["pe_ttm"]["value"] == pytest.approx(50.0 / 7.96, abs=0.01)   # still shown
    assert not any("income-tax benefit" in x for x in F["operating_margin"]["warnings"])


def test_tax_benefit_detected_without_a_tax_line_and_absent_for_a_normal_taxpayer():
    # no tax line from the source: net income above pretax income is a tax
    # benefit OR discontinued operations — no statutory-rate figure is claimed
    F = _view(statements=_stmts(ttm={"tax": None}), shares_out_m=150.0)["fields"]
    w = [x for x in F["net_margin"]["warnings"]
         if "net income exceeds pretax income by $940.0M" in x]
    assert w and "(a tax benefit or discontinued operations)" in w[0]
    assert "Taxed at" not in w[0] and "21%" not in w[0]
    # an ordinary year: tax expense, net income below operating income
    F = _view(statements=_stmts(ttm={"pretax_income": 0.42 * B, "tax": 0.09 * B,
                                     "net_income": 0.33 * B}), shares_out_m=150.0)["fields"]
    for key in ("eps_ttm", "pe_ttm", "net_margin"):
        assert not any("tax" in w or "exceeds operating income" in w for w in F[key]["warnings"])


def test_net_income_above_operating_income_is_flagged_when_nothing_else_explains_it():
    F = _view(statements=_stmts(ttm={"pretax_income": None, "tax": None,
                                     "net_income": 0.9 * B}), shares_out_m=150.0)["fields"]
    assert any("exceeds operating income" in w for w in F["net_margin"]["warnings"])
    assert any("exceeds operating income" in w for w in F["eps_ttm"]["warnings"])


# ── 5. new listings ──────────────────────────────────────────────────────────

NEW = {"kind": "new_listing", "is_fund": False, "is_bank": False, "new_listing": True,
       "sessions_listed": 2, "first_trade_date": "2031-11-20",
       "profile": {"shares_outstanding": 7_700 * M, "implied_shares_outstanding": 13_400 * M}}


def test_balance_sheet_before_the_listing_flags_net_cash_and_withholds_ev():
    F = _view(statements=_stmts(), shares_out_m=13_000.0, security=NEW,
              shares_source="vendor profile")["fields"]
    need = "balance sheet predates the listing — IPO proceeds not included"
    assert any(need in w and "2031-09-30" in w and "2031-11-20" in w
               for w in F["net_cash"]["warnings"])
    assert F["net_cash"]["status"] == "ok" and F["net_cash"]["value"] == pytest.approx(1.9 * B)
    assert F["ev"]["status"] == "withheld" and F["ev"]["display"] == "—"
    assert any(need in w for w in F["ev"]["warnings"])
    # a balance sheet dated after the first trading day is left alone
    later = dict(NEW, first_trade_date="2031-09-15", sessions_listed=48)
    F = _view(statements=_stmts(), shares_out_m=13_000.0, security=later)["fields"]
    assert F["ev"]["status"] == "ok" and not F["net_cash"]["warnings"]


def test_market_cap_states_the_share_count_and_flags_a_young_listing():
    F = _view(statements=_stmts(), shares_out_m=13_000.0, security=NEW,
              shares_source="vendor profile")["fields"]
    mc_ = F["market_cap"]
    assert mc_["value"] == pytest.approx(50.0 * 13_000 * M)
    assert "13,000M shares outstanding (vendor profile)" in mc_["basis"]
    w = [x for x in mc_["warnings"] if x.startswith("listed 2 sessions ago")]
    assert w and "first trading day 2031-11-20" in w[0]
    assert "7,700M for the listed class" in w[0] and "13,400M across share classes" in w[0]
    # 60+ sessions and agreeing sources: no share-count warning
    old = {"kind": "stock", "sessions_listed": None,
           "profile": {"implied_shares_outstanding": 13_100 * M}}
    assert _view(statements=_stmts(), shares_out_m=13_000.0,
                 security=old)["fields"]["market_cap"]["warnings"] == []


def test_market_cap_flags_share_count_sources_that_disagree():
    sec = {"kind": "stock", "profile": {"implied_shares_outstanding": 15_000 * M}}
    w = _view(statements=_stmts(), shares_out_m=13_000.0,
              security=sec)["fields"]["market_cap"]["warnings"]
    assert any("share-count sources disagree" in x and "13,000M" in x and "15,000M" in x
               and "$750.0B" in x for x in w)


# ── 6. security_kind ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("quote,n,has_stmts,profile,sic,bank_like,kind", [
    ({"name": "ZZ Corp", "asset_type": "EQUITY", "asset_sub_type": "COE"}, 130, True, None, None, False, "stock"),
    # the broker files ETFs as EQUITY; the sub-type says what they are
    ({"name": "ZZ Income", "asset_type": "EQUITY", "asset_sub_type": "ETF"}, 130, False, None, None, False, "fund"),
    ({"name": "ZZ Gold", "asset_type": "ETF"}, 130, True, None, None, False, "fund"),
    ({"name": "ZZ Thing", "asset_type": "EQUITY"}, 130, False, {"quote_type": "ETF"}, None, False, "fund"),
    # name fallback only without a sub-type AND without statements
    ({"name": "ZZ High Income ETF", "asset_type": "EQUITY"}, 130, False, None, None, False, "fund"),
    ({"name": "ZZ Trust Corp", "asset_type": "EQUITY"}, 130, True, None, None, False, "stock"),
    ({"name": "ZZ Trust Corp", "asset_type": "EQUITY", "asset_sub_type": "COE"}, 130, False, None, None, False, "stock"),
    ({"name": "ZZ Bancorp", "asset_type": "EQUITY"}, 130, True, None, "6022", False, "bank"),
    ({"name": "ZZ Bancorp", "asset_type": "EQUITY"}, 130, True, {"industry": "Banks - Regional"}, None, False, "bank"),
    ({"name": "ZZ Bancorp", "asset_type": "EQUITY"}, 130, True, None, None, True, "bank"),
    ({"name": "ZZ Software", "asset_type": "EQUITY"}, 130, True, None, "7372", False, "stock"),
    # fund, then bank, then fewer than 21 completed sessions (is_new_listing
    # carries the listing age whatever the kind)
    ({"name": "ZZ New", "asset_type": "EQUITY"}, 2, True, None, None, False, "new_listing"),
    ({"name": "ZZ New Bank", "asset_type": "EQUITY"}, 20, True, None, "6021", False, "bank"),
    ({"name": "ZZ New ETF", "asset_type": "EQUITY", "asset_sub_type": "ETF"}, 0, False, None, None, False, "fund"),
    ({"name": "ZZ Old", "asset_type": "EQUITY"}, 21, True, None, None, False, "stock"),
    # no price history at all: nothing is claimed about the listing
    ({"name": "ZZ Unknown", "asset_type": "EQUITY"}, None, True, None, None, False, "stock"),
])
def test_classify_security(quote, n, has_stmts, profile, sic, bank_like, kind):
    s = report_view.classify_security(quote, n, has_stmts, profile, sic, bank_like)
    assert s["kind"] == kind and s["kind"] in report_view.SECURITY_KINDS
    assert s["is_new_listing"] == s["new_listing"] == (n is not None and n < 21)


def test_new_fund_or_bank_keeps_its_kind_and_flags_the_new_listing():
    s = report_view.classify_security({"asset_type": "EQUITY", "asset_sub_type": "ETF"},
                                      3, False)
    assert s["kind"] == "fund" and s["is_fund"] and not s["is_bank"] and s["is_new_listing"]
    s = report_view.classify_security({"asset_type": "EQUITY"}, 3, True, None, "6035")
    assert s["kind"] == "bank" and s["is_bank"] and s["is_new_listing"]


def test_payload_carries_security_kind(client):
    a = client.get("/api/analysis/ZZAA").json()
    assert a["security_kind"] == "stock" and a["report_view"]["security_kind"] == "stock"
    assert client.get("/api/analysis/SPY").json()["security_kind"] == "fund"


def test_payload_new_listing_from_a_short_history(tmp_db, monkeypatch):
    from analysis import composer
    p = rf.nvda_providers()
    hist = p["daily_history"]("NVDA")[-3:]
    p["daily_history"] = lambda s, *a: rf.spy_bars(rf.NVDA_FRI) if s == "SPY" else hist
    rf.install(monkeypatch, p)
    a = composer.analyze("NVDA", now=rf.NVDA_NOW)
    assert a["security_kind"] == "new_listing"
    assert a["security"]["sessions_listed"] == 3
    assert a["security"]["first_trade_date"] == hist[0]["date"]
    F = a["report_view"]["fields"]
    # the replay balance sheet (2026-10-25) is older than that first bar
    assert F["ev"]["status"] == "withheld"
    assert any("predates the listing" in w for w in F["net_cash"]["warnings"])
    assert any(w.startswith("listed 3 sessions ago") for w in F["market_cap"]["warnings"])
    assert a["value_lens"]["ev"]["value"] is None
    assert any("predates the listing" in w for w in a["value_lens"]["warnings"])


def test_schwab_quote_exposes_the_asset_sub_type(monkeypatch):
    from providers import schwab

    class Resp:
        def raise_for_status(self): pass
        def json(self):
            return {"ZZQI": {"assetMainType": "EQUITY", "assetSubType": "ETF",
                             "quote": {"lastPrice": 45.0, "closePrice": 45.5},
                             "reference": {"description": "ZZ High Income ETF"}}}

    class Client:
        def get_quotes(self, syms): return Resp()
    monkeypatch.setattr(schwab, "_make_client", lambda: Client())
    q = schwab.fetch_quote("ZZQI")
    assert q["asset_type"] == "EQUITY" and q["asset_sub_type"] == "ETF"
    assert schwab.fetch_quotes_batch(["ZZQI"])["ZZQI"]["asset_sub_type"] == "ETF"
    assert report_view.classify_security(q, 130, False)["kind"] == "fund"


# ── 7. funds ─────────────────────────────────────────────────────────────────

FUND = {"kind": "fund", "is_fund": True, "is_bank": False,
        "profile": {"expense_ratio_pct": 0.68, "total_assets": 9.4 * B, "nav": 50.12,
                    "category": "Derivative Income", "fund_family": "ZZ Funds",
                    "source": "test profile", "as_of": "2031-11-22"}}


def test_fund_has_no_company_fundamentals_rows_but_fund_rows():
    v = _view(dividends=_div(MONTHLY_13), security=FUND, shares_out_m=200.0,
              statements=None, vendor={"market_cap_m": 9400.0})
    F = v["fields"]
    assert v["security_kind"] == "fund"
    for k in report_view.COMPANY_ONLY:
        assert k not in F, k
    assert F["div_yield_trailing"]["label"] == "Distribution yield (trailing 12 mo)"
    assert F["div_yield_forward"]["group"] == "fund"
    assert F["fund_schedule"]["display"].startswith("monthly (12/yr) · latest $0.58, ex 2031-11-18")
    d = F["fund_distributions"]
    assert d["label"] == "Distributions (last 12)" and len(d["items"]) == 12
    assert d["items"][0] == {"date": "2031-11-18", "amount": 0.58}
    assert d["display"].startswith("2031-11-18 $0.58 · 2031-10-21 $0.63")
    assert F["fund_expense_ratio"]["display"] == "0.68%"
    assert F["fund_aum"]["display"] == "$9.4B"
    assert F["fund_nav"]["display"] == "$50.12" and "not compared" in F["fund_nav"]["basis"]
    assert F["fund_category"]["display"] == "Derivative Income · ZZ Funds"


def test_fund_rows_exist_only_where_the_source_has_data():
    sec = {"kind": "fund", "is_fund": True, "profile": {"expense_ratio_pct": None}}
    F = _view(dividends=_div([]), security=sec)["fields"]
    for k in ("fund_expense_ratio", "fund_aum", "fund_nav", "fund_category",
              "fund_schedule", "fund_distributions"):
        assert k not in F
    irregular = [("2030-03-27", 0.012), ("2030-06-27", 0.075), ("2030-12-29", 0.09),
                 ("2031-06-27", 0.058), ("2031-09-29", 0.004)]
    F = _view(dividends=_div(irregular), security=sec)["fields"]
    assert F["fund_schedule"]["display"].startswith("irregular — 92–185 days")
    assert F["fund_schedule"]["value"] is None


def test_every_field_has_a_known_group(client):
    for sym in ("ZZAA", "SPY"):
        a = client.get(f"/api/analysis/{sym}").json()
        fields = a["report_view"]["fields"]
        assert fields
        for k, f in fields.items():
            assert f.get("group") in report_view.GROUPS, (sym, k)
        assert a["report_view"]["groups"] == list(report_view.GROUPS)
    assert fields["price"]["group"] == "price" and fields["beta"]["group"] == "context"
    assert fields["claude_stance"]["group"] == "research"
    assert fields["headlines"]["group"] == "news"
    stock = client.get("/api/analysis/ZZAA").json()["report_view"]["fields"]
    assert stock["market_cap"]["group"] == "fundamentals"
    assert stock["div_yield_forward"]["group"] == "fundamentals"
    assert stock["implied_move"]["group"] == "options"


def test_fund_report_html_has_a_fund_card_and_no_fundamentals(client):
    a = client.get("/api/analysis/SPY").json()
    F = a["report_view"]["fields"]
    assert "market_cap" not in F and "pe_ttm" not in F and "fund_expense_ratio" in F
    assert a["value_lens"] is None
    html = client.get("/api/analysis/SPY/report").text
    assert "Fund (at the report price)" in html and "Expense ratio" in html
    assert "Fundamentals (at the report price)" not in html
    assert "Market cap" not in html and "P/E (TTM)" not in html
    stock = client.get("/api/analysis/ZZAA/report").text
    assert "Fundamentals (at the report price)" in stock and "Market cap" in stock


def test_yahoo_profile_units_and_missing_values():
    from providers.yfinance import security_profile_from_info
    p = security_profile_from_info({"quoteType": "ETF", "netExpenseRatio": 0.06,
                                    "totalAssets": 1.2e11, "navPrice": 33.93,
                                    "category": "Large Value", "fundFamily": "ZZ ETFs"})
    assert p["is_fund"] and p["expense_ratio_pct"] == 0.06 and p["total_assets"] == 1.2e11
    # the older key is a fraction; an absurd figure is dropped, not shown
    p = security_profile_from_info({"quoteType": "ETF", "annualReportExpenseRatio": 0.0068})
    assert p["expense_ratio_pct"] == pytest.approx(0.68) and p["nav"] is None
    assert security_profile_from_info({"quoteType": "ETF", "netExpenseRatio": 68})["expense_ratio_pct"] is None
    p = security_profile_from_info({"quoteType": "EQUITY", "industry": "Banks - Regional",
                                    "sharesOutstanding": 13.75e6,
                                    "impliedSharesOutstanding": 13.75e6})
    assert p["is_fund"] is False and p["industry"] == "Banks - Regional"
    assert p["implied_shares_outstanding"] == 13.75e6


# ── 8. banks ─────────────────────────────────────────────────────────────────

BANK = {"kind": "bank", "is_fund": False, "is_bank": True, "why": ["SEC SIC code 6022"]}


def _bank_stmts():
    return _stmts(
        ttm={"revenue": 140 * M, "gross_profit": None, "operating_income": None,
             "pretax_income": 51 * M, "tax": 9 * M, "net_income": 42 * M,
             "eps_diluted": 2.63, "fcf": 29 * M},
        balance={"total_debt": 224 * M, "cash": 57 * M, "cash_st": 57 * M,
                 "equity": 414 * M, "preferred_equity": 30 * M,
                 "goodwill_intangibles": 73 * M, "as_of_date": "2031-09-30"},
        shares={"diluted": 15.72 * M, "basic": 13.77 * M, "period": "2031-09-30",
                "span": "quarter", "source": "test"})


def test_bank_suppresses_ev_net_cash_fcf_and_gross_margin_with_one_note():
    v = _view(statements=_bank_stmts(), shares_out_m=13.75, security=BANK,
              basis={"price": 26.70, "price_kind": "close", "session_date": "2031-11-21",
                     "prior_date": "2031-11-20", "price_label": "Official close"})
    F = v["fields"]
    for k in report_view.BANK_SUPPRESSED | {"operating_margin"}:
        assert k not in F, k
    note = F["bank_note"]
    assert note["group"] == "bank" and note["status"] == "ok"
    assert "not shown for a bank" in note["display"] and "\n" not in note["display"]
    assert {"market_cap", "pe_ttm", "eps_ttm", "net_margin"} <= set(F)


def test_bank_price_to_tangible_book_and_roe():
    F = _view(statements=_bank_stmts(), shares_out_m=13.75, security=BANK,
              basis={"price": 26.70, "price_kind": "close", "session_date": "2031-11-21",
                     "prior_date": "2031-11-20", "price_label": "Official close"})["fields"]
    tce = (414 - 30 - 73) * M
    assert F["p_tbv"]["value"] == pytest.approx(26.70 * 13.75 * M / tce, rel=1e-6)
    assert F["p_tbv"]["display"] == "1.18x" and F["p_tbv"]["group"] == "bank"
    assert "equity $414.0M − preferred $30.0M − goodwill & intangibles $73.0M = $311.0M" \
        in F["p_tbv"]["basis"]
    assert F["roe"]["value"] == pytest.approx(42 / 414 * 100, abs=0.01)
    assert F["roe"]["display"] == "10.1%" and "period end" in F["roe"]["basis"]


def test_bank_without_goodwill_data_gets_no_ratio_but_says_why():
    s = _bank_stmts()
    s["balance"]["goodwill_intangibles"] = None
    F = _view(statements=s, shares_out_m=13.75, security=BANK)["fields"]
    assert F["p_tbv"]["value"] is None and F["p_tbv"]["display"] == "—"
    assert any("tangible book not computed" in w for w in F["p_tbv"]["warnings"])


def test_bank_market_cap_flags_a_materially_larger_diluted_count():
    """13.75M shares outstanding vs 15.72M diluted (convertible preferred)."""
    F = _view(statements=_bank_stmts(), shares_out_m=13.75, security=BANK,
              basis={"price": 26.70, "price_kind": "close", "session_date": "2031-11-21",
                     "prior_date": "2031-11-20", "price_label": "Official close"})["fields"]
    w = [x for x in F["market_cap"]["warnings"] if x.startswith("diluted share count")]
    assert w and "15.7M" in w[0] and "14% above the 13.8M" in w[0]
    assert "convertible" in w[0] and "$419.7M" in w[0]
    # ordinary dilution (3%) is not flagged
    s = _bank_stmts()
    s["shares"]["diluted"] = 14.1 * M
    assert not _view(statements=s, shares_out_m=13.75,
                     security=BANK)["fields"]["market_cap"]["warnings"]


def test_value_lens_for_a_bank_blanks_cash_flow_and_ev_rows():
    s = _bank_stmts()
    s["annual"] = [{"end": f"{2030 - i}-12-31", "revenue": 120 * M, "net_income": 30 * M,
                    "fcf": 25 * M, "ebit": None} for i in range(4)]
    vl = value_lens.for_bank(value_lens.build_value_lens(367 * M, s))
    for k in value_lens.NOT_FOR_BANKS:
        assert vl[k]["value"] is None, k
    assert vl["net_cash"] is False and vl["warnings"][0] == value_lens.BANK_NOTE
    assert vl["pe_avg"]["value"] is not None            # earnings rows stay


def test_sec_balance_for_a_bank_reads_bank_cash_tags_and_tangible_book_inputs():
    from providers.sec_xbrl import normalize
    inst = lambda end, val: {"end": end, "val": val, "form": "10-Q", "filed": "2031-11-05"}  # noqa: E731
    dur = lambda s, e, v, form="10-Q": {"start": s, "end": e, "val": v, "form": form,  # noqa: E731
                                        "filed": "2031-11-05", "accn": "0000000000-31-000001"}
    ni = [dur("2030-01-01", "2030-12-31", 30 * M, "10-K"), dur("2030-01-01", "2030-09-30", 22 * M),
          dur("2031-01-01", "2031-09-30", 33 * M), dur("2031-07-01", "2031-09-30", 12 * M)]
    sh = lambda v: {"units": {"shares": [dur("2031-07-01", "2031-09-30", v)]}}  # noqa: E731
    facts = {"facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": ni}},
        "InterestIncomeExpenseNet": {"units": {"USD": [dur("2031-01-01", "2031-09-30", 100 * M)]}},
        "InterestBearingDepositLiabilities": {"units": {"USD": [inst("2031-09-30", 2500 * M)]}},
        # the generic cash tag was last filed years ago
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [inst("2021-12-31", 9 * M)]}},
        "CashCashEquivalentsAndFederalFundsSold": {"units": {"USD": [inst("2031-09-30", 57 * M)]}},
        "StockholdersEquity": {"units": {"USD": [inst("2031-09-30", 414 * M)]}},
        "Goodwill": {"units": {"USD": [inst("2031-09-30", 60 * M)]}},
        "FiniteLivedIntangibleAssetsNet": {"units": {"USD": [inst("2031-09-30", 13 * M)]}},
        "PreferredStockValue": {"units": {"USD": [inst("2031-09-30", 30 * M)]}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": sh(15.72 * M),
        "WeightedAverageNumberOfSharesOutstandingBasic": sh(13.77 * M),
    }}}
    s = normalize(facts)
    b = s["balance"]
    assert b["as_of_date"] == "2031-09-30" and b["cash"] == 57 * M
    assert b["goodwill_intangibles"] == 73 * M and b["preferred_equity"] == 30 * M
    assert s["bank_like"] is True
    assert s["shares"]["diluted"] == 15.72 * M and s["shares"]["basic"] == 13.77 * M
    assert s["shares"]["period"] == "2031-09-30" and s["shares"]["span"] == "quarter"
    # a non-bank: not bank-like, and the combined goodwill line wins when filed
    plain = {"facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": ni}},
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [inst("2031-09-30", 9 * M)]}},
        "Goodwill": {"units": {"USD": [inst("2031-09-30", 60 * M)]}},
        "IntangibleAssetsNetIncludingGoodwill": {"units": {"USD": [inst("2031-09-30", 80 * M)]}},
    }}}
    s = normalize(plain)
    assert s["bank_like"] is False and s["balance"]["goodwill_intangibles"] == 80 * M


def test_sec_filings_payload_carries_the_sic_code(monkeypatch):
    from providers import edgar, sec_xbrl
    sub = {"sic": "6022", "sicDescription": "State Commercial Banks",
           "filings": {"recent": {"form": ["8-K", "4"], "filingDate": ["2031-11-05", "2031-11-01"],
                                  "accessionNumber": ["0000000000-31-000009", "0000000000-31-000008"],
                                  "items": ["2.02,9.01", ""],
                                  "primaryDocument": ["zzbk-8k.htm", "form4.xml"]}}}
    monkeypatch.setattr(edgar, "_cik_for", lambda s: "0000012345")
    monkeypatch.setattr(edgar, "_get_json", lambda url: sub)
    out = sec_xbrl.fetch_recent_filings("ZZBK", "2031-10-01")
    assert out["sic"] == "6022" and out["is_bank"] is True
    assert out["filings"][0]["accession"] == "0000000000-31-000009"
    assert out["filings"][0]["url"].endswith("/12345/000000000031000009/zzbk-8k.htm")


# ── 9. beta ──────────────────────────────────────────────────────────────────

def test_beta_carries_a_warning_when_the_correlation_is_near_zero():
    f = _view(beta={"beta": 0.03, "correlation": 0.01, "window": 60})["fields"]["beta"]
    assert f["display"] == "0.03"
    assert f["warnings"] == ["beta not meaningful — correlation 0.01 over the same 60 "
                             "sessions (below 0.3): the stock has not been moving with SPY"]
    f = _view(beta={"beta": -0.4, "correlation": -0.29, "window": 60})["fields"]["beta"]
    assert any("correlation -0.29" in w for w in f["warnings"])
    for corr in (0.3, 0.75, -0.5):
        assert not _view(beta={"beta": 1.1, "correlation": corr,
                               "window": 60})["fields"]["beta"]["warnings"]
    assert not _view(beta={"beta": None, "correlation": None})["fields"]["beta"]["warnings"]


# ── 10. gross margin without a GrossProfit tag ───────────────────────────────

def _fact(start, end, val, form="10-Q", filed="2031-10-30"):
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed,
            "accn": "0000000000-31-000002"}


def _income_facts(extra: dict) -> dict:
    rev = [_fact("2030-01-01", "2030-12-31", 350 * B, "10-K", "2031-02-01"),
           _fact("2030-01-01", "2030-09-30", 250 * B),
           _fact("2031-01-01", "2031-09-30", 300 * B),
           _fact("2031-07-01", "2031-09-30", 110 * B)]
    us = {"RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": rev}},
          "NetIncomeLoss": {"units": {"USD": [dict(r, val=r["val"] * 0.3) for r in rev]}}}
    us.update(extra)
    return {"facts": {"us-gaap": us}}


def test_gross_profit_is_revenue_less_cost_of_revenue_when_the_filing_has_no_gross_line():
    from providers.sec_xbrl import normalize
    cost = [_fact("2030-01-01", "2030-12-31", 150 * B, "10-K", "2031-02-01"),
            _fact("2030-01-01", "2030-09-30", 108 * B),
            _fact("2031-01-01", "2031-09-30", 123 * B),
            _fact("2031-07-01", "2031-09-30", 44 * B)]
    s = normalize(_income_facts({"CostOfRevenue": {"units": {"USD": cost}}}))
    ttm_rev, ttm_cost = (300 + 350 - 250) * B, (123 + 150 - 108) * B
    assert s["ttm"]["revenue"] == pytest.approx(ttm_rev)
    assert s["ttm"]["gross_profit"] == pytest.approx(ttm_rev - ttm_cost)
    assert s["ttm"]["gross_profit_derived"] is True
    assert s["quarters"][0]["gross_profit"] == pytest.approx((110 - 44) * B)
    # through the merge and into the row, labeled as derived
    merged = statements.merge([s])
    assert merged["ttm"]["gross_profit_derived"] is True
    f = _view(statements=merged, shares_out_m=12_000.0)["fields"]["gross_margin"]
    assert f["value"] == pytest.approx((ttm_rev - ttm_cost) / ttm_rev * 100)
    assert f["display"] == "58.8%" and f["derived"] is True
    assert "revenue − cost of revenue" in f["formula"] and "no gross-profit line" in f["basis"]


def test_filed_gross_profit_is_used_as_is_and_no_cost_tag_means_no_margin():
    from providers.sec_xbrl import normalize
    gp = [_fact("2030-01-01", "2030-12-31", 210 * B, "10-K", "2031-02-01"),
          _fact("2030-01-01", "2030-09-30", 150 * B),
          _fact("2031-01-01", "2031-09-30", 186 * B)]
    cost = [_fact("2031-01-01", "2031-09-30", 1 * B)]          # must not override
    s = normalize(_income_facts({"GrossProfit": {"units": {"USD": gp}},
                                 "CostOfGoodsAndServicesSold": {"units": {"USD": cost}}}))
    assert s["ttm"]["gross_profit"] == pytest.approx((186 + 210 - 150) * B)
    assert s["ttm"]["gross_profit_derived"] is False
    s = normalize(_income_facts({}))
    assert s["ttm"]["gross_profit"] is None and s["ttm"]["gross_profit_derived"] is False
    f = _view(statements=statements.merge([s]), shares_out_m=12_000.0)["fields"]["gross_margin"]
    assert f["value"] is None and f["display"] == "—"


# ── 11. value lens ───────────────────────────────────────────────────────────

def _lens_stmts(revs, ttm_rev, ends=("2030-12-31", "2029-12-31", "2028-12-31", "2027-12-31")):
    annual = [{"end": e, "revenue": r, "net_income": 20 * M, "fcf": 15 * M,
               "ebit": None, "acquisitions": -0.2 * M} for e, r in zip(ends, revs)]
    return {"available": True, "annual": annual, "source": "test", "as_of": "2031-11-22",
            "ttm": {"revenue": ttm_rev, "net_income": 30 * M, "fcf": 25 * M},
            "balance": {"total_debt": 200 * M, "cash": 50 * M, "equity": 400 * M}}


def test_cagr_names_its_fiscal_years_and_explains_a_conflict_with_ttm():
    """ZZBK: revenue fell FY2027 → FY2030 (−5.9%/yr) while the TTM, which
    includes an acquisition closed after FY2030, is +36% on that year. Both
    are true; the lens says which years the CAGR covers."""
    vl = value_lens.build_value_lens(367 * M, _lens_stmts(
        [90.2 * M, 82.4 * M, 82.4 * M, 108.4 * M], 122.6 * M))
    c = vl["rev_cagr_3y"]
    assert c["value"] == pytest.approx(-5.94, abs=0.02)
    assert "FY ended 2027-12-31 ($108.4M) → FY ended 2030-12-31 ($90.2M)" in c["basis"]
    w = [x for x in vl["warnings"] if x.startswith("revenue CAGR is measured on fiscal years")]
    assert w and "through 2030-12-31" in w[0] and "TTM revenue $122.6M is +36%" in w[0]
    assert "not in the CAGR" in w[0]
    # TTM in line with the last fiscal year: no warning
    vl = value_lens.build_value_lens(367 * M, _lens_stmts(
        [90.2 * M, 82.4 * M, 82.4 * M, 108.4 * M], 93.0 * M))
    assert not any("CAGR" in x for x in vl["warnings"])


def test_cagr_withheld_when_fy_series_and_ttm_cannot_be_the_same_revenue_line():
    vl = value_lens.build_value_lens(367 * M, _lens_stmts(
        [9.0 * M, 8.5 * M, 8.0 * M, 11.0 * M], 122.6 * M))     # fee income vs total revenue
    assert vl["rev_cagr_3y"]["value"] is None and vl["rev_cagr_5y"]["value"] is None
    assert any(x.startswith("revenue CAGR withheld") and "different revenue definitions" in x
               for x in vl["warnings"])
    assert not any("slowing" in r or "accelerating" in r for r in vl["readings"])


def test_acquisitions_row_says_cash_paid_and_excludes_stock_deals():
    vl = value_lens.build_value_lens(367 * M, _lens_stmts(
        [90.2 * M, 82.4 * M, 82.4 * M, 108.4 * M], 93.0 * M))
    a = vl["acquisitions_total"]
    assert a["label"] == "Cash paid for acquisitions"
    assert a["value"] == pytest.approx(0.8)
    assert a["basis"].startswith("cash paid for acquisitions, net of cash acquired, 4 FYs")
    assert "excludes deals paid in stock" in a["basis"] and "sum of business purchases" not in a["basis"]


def test_merge_takes_the_fy_series_from_a_source_whose_income_statement_is_complete():
    def src(name, annual, rev):
        return {"available": True, "source": name, "annual": annual,
                "ttm": {"revenue": rev, "net_income": 1.0, "fcf": 1.0},
                "balance": {"cash_st": 1.0}, "quarters": [],
                "periods": {"income": "2031-09-30", "cashflow": "2031-09-30",
                            "balance": "2031-09-30"},
                "filing": {"form": "10-Q", "filed": "2031-11-05"}}
    sec_fee_only = src("SEC XBRL (10-Q filed 2031-11-05)", [{"revenue": 9.0}], None)
    yahoo = src("Yahoo statements", [{"revenue": 90.0}], 120.0)
    m = statements.merge([sec_fee_only, yahoo])
    assert m["annual"] == [{"revenue": 90.0}] and m["annual_source"] == "Yahoo statements"
    # a complete SEC income statement keeps the SEC series
    sec_ok = src("SEC XBRL (10-Q filed 2031-11-05)", [{"revenue": 91.0}], 121.0)
    m = statements.merge([sec_ok, yahoo])
    assert m["annual"] == [{"revenue": 91.0}] and m["annual_source"].startswith("SEC filing")


# ── 12. analyst actions ──────────────────────────────────────────────────────

def _rc(changes, **kw):
    return {"available": True, "source": "Yahoo", "changes": changes, **kw}


def test_initiations_are_counted_apart_from_reiterations():
    ch = [{"date": "2031-11-18", "firm": "ZZ Securities", "action": "init", "from": "", "to": "Buy"},
          {"date": "2031-11-17", "firm": "ZZ Capital", "action": "init", "from": "", "to": "Hold"},
          {"date": "2031-11-10", "firm": "ZZ Bank", "action": "down", "from": "Buy", "to": "Hold"},
          {"date": "2031-11-05", "firm": "ZZ Partners", "action": "main", "from": "Buy", "to": "Buy"},
          {"date": "2031-09-01", "firm": "ZZ Old", "action": "init", "from": "", "to": "Buy"}]
    f = _view(rating_changes=_rc(ch))["fields"]["analyst_actions"]
    assert f["value"] == 4
    assert f["counts"] == {"down": 1, "up": 0, "init": 2, "other": 1}
    assert f["display"].startswith("1 downgrade(s), 0 upgrade(s), 2 initiation(s), 1 other")
    assert "11-18 ZZ Securities initiates at Buy" in f["display"]
    assert "11-10 ZZ Bank Buy→Hold" in f["display"]


def test_an_empty_rating_feed_is_missing_data_not_zero_changes():
    sec = {"kind": "new_listing", "new_listing": True}
    f = _view(rating_changes=_rc([], no_feed=True), security=sec)["fields"]["analyst_actions"]
    assert f["value"] is None and f["display"] == "—" and f["status"] == "no_data"
    assert any("no rating-action history at all for ZZAA (listed days ago)" in w
               and "not a count of zero" in w for w in f["warnings"])
    # a feed with history but nothing in the last 30 days IS a real zero
    f = _view(rating_changes=_rc([]))["fields"]["analyst_actions"]
    assert f["value"] == 0 and f["status"] == "ok" and not f["warnings"]
    assert f["display"] == "0 downgrade(s), 0 upgrade(s), 0 initiation(s), 0 other"


def test_yahoo_rating_changes_marks_an_empty_frame_as_no_feed(monkeypatch):
    import pandas as pd

    from providers import yfinance as yfp

    class T:
        def __init__(self, df): self.upgrades_downgrades = df

    class YF:
        df = pd.DataFrame()
        def Ticker(self, s): return T(self.df)
    yf = YF()
    monkeypatch.setattr(yfp, "_yf", lambda: yf)
    assert yfp.fetch_rating_changes("ZZNW") == {"available": True, "source": "Yahoo",
                                                "changes": [], "no_feed": True}
    today = dt.date.today()
    yf.df = pd.DataFrame(
        {"Firm": ["ZZ Securities", "ZZ Old"], "ToGrade": ["Buy", "Hold"],
         "FromGrade": ["", "Buy"], "Action": ["init", "down"]},
        index=pd.to_datetime([today - dt.timedelta(days=3), today - dt.timedelta(days=400)]))
    out = yfp.fetch_rating_changes("ZZNW")
    assert out["no_feed"] is False
    assert [c["action"] for c in out["changes"]] == ["init"]      # kept, not dropped


# ── 13. the earnings release exhibit ─────────────────────────────────────────

INDEX_HTML = """
<table class="tableFile" summary="Document Format Files">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr><td scope="row">1</td><td scope="row">8-K</td>
 <td scope="row"><a href="/ix?doc=/Archives/edgar/data/12345/000001234531000106/zzaa-20311029.htm">zzaa-20311029.htm</a></td>
 <td scope="row">8-K</td><td scope="row">31337</td></tr>
<tr class="evenRow"><td scope="row">2</td><td scope="row">EX-99.2</td>
 <td scope="row"><a href="/Archives/edgar/data/12345/000001234531000106/q3slides.htm">q3slides.htm</a></td>
 <td scope="row">EX-99.2</td><td scope="row">90210</td></tr>
<tr><td scope="row">3</td><td scope="row">EX-99.1</td>
 <td scope="row"><a href="/Archives/edgar/data/12345/000001234531000106/q3release.htm">q3release.htm</a></td>
 <td scope="row">EX-99.1</td><td scope="row">424242</td></tr>
</table>"""


def test_exhibit_99_1_is_found_in_the_filing_index():
    from providers.sec_xbrl import exhibit_from_index
    assert exhibit_from_index(INDEX_HTML) == \
        "https://www.sec.gov/Archives/edgar/data/12345/000001234531000106/q3release.htm"
    # no EX-99.1: the first EX-99.* exhibit; no exhibit at all: None
    only_2 = INDEX_HTML.replace("EX-99.1", "EX-10.1")
    assert exhibit_from_index(only_2).endswith("/q3slides.htm")
    assert exhibit_from_index("<table><tr><td>8-K</td></tr></table>") is None
    assert exhibit_from_index("") is None


def test_fetch_release_exhibit_reads_the_index_of_that_filing(monkeypatch):
    import requests

    from providers import edgar, sec_xbrl
    seen = {}

    class R:
        status_code, text = 200, INDEX_HTML

    def get(url, **kw):
        seen["url"] = url
        return R()
    monkeypatch.setattr(edgar, "_ua", lambda: {"User-Agent": "test"})
    monkeypatch.setattr(requests, "get", get)
    out = sec_xbrl.fetch_release_exhibit(
        "https://www.sec.gov/Archives/edgar/data/12345/000001234531000106/zzaa-20311029.htm")
    assert seen["url"] == ("https://www.sec.gov/Archives/edgar/data/12345/"
                           "000001234531000106/0000012345-31-000106-index.htm")
    assert out["exhibit_url"].endswith("/q3release.htm")
    assert sec_xbrl.fetch_release_exhibit("https://example.com/x.htm") == {"exhibit_url": None}


def test_release_carries_exhibit_url_beside_the_cover_page_url(tmp_db, monkeypatch):
    from analysis import composer
    cover = "https://www.sec.gov/Archives/edgar/data/12345/000001234526000106/zz-8k.htm"
    exhibit = "https://www.sec.gov/Archives/edgar/data/12345/000001234526000106/q3release.htm"
    p = rf.nvda_providers()
    p["earnings_dates"] = lambda s: [{"date": "2026-11-17", "hour": "amc"}]
    p["sec_filings"] = lambda s, since: {"available": True, "since": since, "cik": "0000012345",
                                         "filings": [{"form": "8-K", "date": "2026-11-17",
                                                      "items": "2.02,9.01", "url": cover}]}
    p["sec_exhibit"] = lambda url: {"exhibit_url": exhibit if url == cover else None}
    rf.install(monkeypatch, p)
    rel = composer.analyze("NVDA", now=rf.NVDA_NOW)["statements_meta"]["release"]
    assert rel["url"] == cover and rel["exhibit_url"] == exhibit
    # no exhibit found (or EDGAR down): the release is still there, url only
    p["sec_exhibit"] = lambda url: {"exhibit_url": None}
    p["sec_filings"] = lambda s, since: {"available": True, "since": since, "cik": "0000012345",
                                         "filings": [{"form": "8-K", "date": "2026-11-17",
                                                      "items": "2.02,9.01",
                                                      "url": cover.replace("106", "107")}]}
    # (force: the filings list is cached for 6 hours)
    rel = composer.analyze("NVDA", force=True, now=rf.NVDA_NOW)["statements_meta"]["release"]
    assert rel["url"].endswith("zz-8k.htm") and "000107" in rel["url"]
    assert "exhibit_url" not in rel


# ── cached copies from before these fields existed are refetched ─────────────

def test_cached_statements_and_filings_without_new_fields_are_refetched(monkeypatch):
    from analysis import composer
    calls = []

    def gof(key, ttl, fn, force=False):
        calls.append((key, force))
        if not force:
            old = ({"available": True, "periods": {"income": "2031-09-30"}, "ttm": {}}
                   if key.startswith("statements") else {"available": True, "filings": []})
            return {"data": old, "source": "cache", "fetched_ts": None, "age_seconds": 60}
        return {"data": fn(), "source": "live", "fetched_ts": None, "age_seconds": 0}
    monkeypatch.setattr(composer, "get_or_fetch", gof)
    d, meta = composer._one_statement_source(
        "statements:ZZAA", lambda s: {"available": True, "periods": {}, "shares": {}},
        "ZZAA", False, None)
    assert "shares" in d and meta["refreshed"] is True
    monkeypatch.setattr(composer.registry, "resolve",
                        lambda cap: (lambda s, since: {"available": True, "filings": [],
                                                       "sic": "6022"}))
    assert composer._recent_filings("ZZBK", "2031-10-01", False)["sic"] == "6022"
    assert [f for _, f in calls] == [False, True, False, True]


def test_cached_empty_rating_feed_without_no_feed_is_refetched(monkeypatch):
    from analysis import composer
    calls = []

    def gof(key, ttl, fn, force=False):
        calls.append(force)
        if not force:
            return {"data": {"available": True, "source": "Yahoo", "changes": []},
                    "source": "cache", "fetched_ts": None, "age_seconds": 60}
        return {"data": fn(), "source": "live", "fetched_ts": None, "age_seconds": 0}
    monkeypatch.setattr(composer, "get_or_fetch", gof)
    monkeypatch.setattr(composer.registry, "resolve", lambda cap: (
        lambda s: {"available": True, "source": "Yahoo", "changes": [], "no_feed": True}))
    assert composer._rating_changes("ZZNW", False)["no_feed"] is True
    assert calls == [False, True]


# ── docs ─────────────────────────────────────────────────────────────────────

def test_new_fields_and_security_kind_are_documented():
    import os
    doc = open(os.path.join(os.path.dirname(__file__), "..", "..", "docs",
                            "REPORT_FIELDS.md"), encoding="utf-8").read()
    for key in ("security_kind", "new_listing", "fund_schedule", "fund_distributions",
                "fund_expense_ratio", "fund_aum", "fund_nav", "fund_category",
                "bank_note", "p_tbv", "roe", "exhibit_url"):
        assert f"`{key}`" in doc, key
    for g in report_view.GROUPS:
        assert f"`{g}`" in doc, g
