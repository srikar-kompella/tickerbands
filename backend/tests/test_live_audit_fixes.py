"""
Fixes found by testing the live app on Sat 2026-11-21 (after the first
report-accuracy pass). Inputs are the real values observed that day.
"""
import datetime as dt

import pytest

from analysis import market_clock as mc
from analysis import news_relevance as nr
from analysis import report_view, statements
from tests import replay_fixtures as rf


# ── 1. extended-hours price: a stale pre-market print is not "after hours" ────

def _blob(ext_px, ext_ms, last, q_ms, reg_last, reg_ms, reg_pct=-14.01):
    return ({"lastPrice": last, "quoteTime": q_ms, "tradeTime": q_ms},
            {"regularMarketLastPrice": reg_last, "regularMarketTradeTime": reg_ms,
             "regularMarketPercentChange": reg_pct},
            {"lastPrice": ext_px, "tradeTime": ext_ms})


def test_stale_premarket_extended_print_ignored_live_nvda():
    from providers.schwab import _extended_view
    # live NVDA 11/21: extended 144.12 traded Fri 3:59 AM; composite 130.03 at 7:59 PM
    q, reg, ext = _blob(144.12, rf.ms(rf.ny(2026, 11, 20, 3, 59, 58)),
                        130.0278, rf.ms(rf.ny(2026, 11, 20, 19, 59, 58)),
                        125.53, rf.ms(rf.ny(2026, 11, 20, 16, 0, 3)))
    v = _extended_view(q, reg, ext)
    assert v["ah_price"] == pytest.approx(130.0278)
    assert v["ah_source"] == "composite"
    assert v["ah_change_pct"] == pytest.approx(3.58, abs=0.01)


def test_fresh_postmarket_extended_print_used():
    from providers.schwab import _extended_view
    q, reg, ext = _blob(131.10, rf.ms(rf.ny(2026, 11, 20, 19, 59, 59)),
                        130.0, rf.ms(rf.ny(2026, 11, 20, 18, 0)),
                        125.53, rf.ms(rf.ny(2026, 11, 20, 16, 0, 3)))
    assert _extended_view(q, reg, ext)["ah_price"] == 131.10


def test_next_morning_premarket_print_used():
    from providers.schwab import _extended_view
    q, reg, ext = _blob(127.40, rf.ms(rf.ny(2026, 11, 23, 7, 55)),
                        125.53, rf.ms(rf.ny(2026, 11, 20, 16, 0, 3)),
                        125.53, rf.ms(rf.ny(2026, 11, 20, 16, 0, 3)))
    v = _extended_view(q, reg, ext)
    assert v["ah_price"] == 127.40 and v["ah_source"] == "extended"


def test_no_extended_trade_means_none():
    from providers.schwab import _extended_view
    q, reg, ext = _blob(144.12, rf.ms(rf.ny(2026, 11, 20, 3, 59)),
                        125.53, rf.ms(rf.ny(2026, 11, 20, 16, 0, 3)),
                        125.53, rf.ms(rf.ny(2026, 11, 20, 16, 0, 3)))
    assert _extended_view(q, reg, ext)["ah_price"] is None


# ── 2. SEC XBRL statements: TTM from year-to-date facts (NVDA, as filed) ─────

def _fact(start, end, val, form="10-Q", filed="2026-08-26"):
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed,
            "accn": "0001045810-26-000075"}


def _nvda_facts():
    B = 1_000_000_000
    ocf = [  # real NVDA values from data.sec.gov (companyconcept), $
        _fact("2024-01-29", "2025-01-26", 64.089 * B, "10-K", "2025-02-26"),
        _fact("2025-01-27", "2025-04-27", 27.414 * B),
        _fact("2025-01-27", "2025-07-27", 42.779 * B),
        _fact("2025-01-27", "2025-10-26", 66.530 * B, filed="2025-11-19"),
        _fact("2025-01-27", "2026-01-25", 102.718 * B, "10-K", "2026-02-25"),
        _fact("2026-01-26", "2026-04-26", 50.344 * B, filed="2026-05-20"),
        _fact("2026-01-26", "2026-07-26", 74.421 * B),
    ]
    capex = [
        _fact("2025-01-27", "2026-01-25", 6.0 * B, "10-K", "2026-02-25"),
        _fact("2025-01-27", "2025-07-27", 3.121 * B),
        _fact("2026-01-26", "2026-04-26", 1.757 * B, filed="2026-05-20"),
        _fact("2026-01-26", "2026-07-26", 4.434 * B),
    ]
    rev = [
        _fact("2025-01-27", "2026-01-25", 216.0 * B, "10-K", "2026-02-25"),
        _fact("2025-01-27", "2025-07-27", 90.8 * B),
        _fact("2025-04-28", "2025-07-27", 46.7 * B),
        _fact("2026-01-26", "2026-04-26", 81.7 * B, filed="2026-05-20"),
        _fact("2026-01-26", "2026-07-26", 177.9 * B),
        _fact("2026-04-27", "2026-07-26", 96.2 * B),
    ]
    ni = [dict(r, val=r["val"] * 0.55) for r in rev]
    inst = lambda end, val: {"end": end, "val": val, "form": "10-Q", "filed": "2026-08-26"}  # noqa: E731
    return {"facts": {"us-gaap": {
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": ocf}},
        "PaymentsToAcquireProductiveAssets": {"units": {"USD": capex}},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": rev}},
        "NetIncomeLoss": {"units": {"USD": ni}},
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [inst("2026-07-26", 22.443 * B)]}},
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent": {"units": {"USD": [inst("2026-07-26", 30.0 * B)]}},
        "EquitySecuritiesFvNiCurrent": {"units": {"USD": [inst("2026-07-26", 40.0 * B)]}},
        "LongTermDebtNoncurrent": {"units": {"USD": [inst("2026-07-26", 32.4 * B)]}},
        "LongTermDebtCurrent": {"units": {"USD": [inst("2026-07-26", 1.0 * B)]}},
        # an older tag that stopped being used must not win
        "MarketableSecuritiesCurrent": {"units": {"USD": [inst("2025-10-26", 49.122 * B)]}},
    }}}


def test_sec_ttm_from_ytd_formula():
    from providers.sec_xbrl import normalize
    s = normalize(_nvda_facts())
    B = 1e9
    # TTM OCF through 2026-07-26 = YTD 74.421 + FY 102.718 − prior YTD 42.779
    assert s["ttm"]["ocf"] == pytest.approx((74.421 + 102.718 - 42.779) * B)
    assert s["ttm"]["capex"] == pytest.approx(-(4.434 + 6.0 - 3.121) * B)
    assert s["periods"]["cashflow"] == "2026-07-26"
    q0 = s["quarters"][0]
    assert q0["end"] == "2026-07-26"
    assert q0["revenue"] == pytest.approx(96.2 * B)            # 3-month fact
    # Q2 cash flow derived: YTD Q2 − YTD Q1
    assert q0["fcf"] == pytest.approx(((74.421 - 50.344) - (4.434 - 1.757)) * B)


def test_sec_balance_uses_current_tags_and_sums_debt():
    from providers.sec_xbrl import normalize
    b = normalize(_nvda_facts())["balance"]
    B = 1e9
    assert b["as_of_date"] == "2026-07-26"
    assert b["cash_st"] == pytest.approx((22.443 + 30.0) * B)     # not the stale 49.1B tag
    assert b["equity_securities"] == pytest.approx(40.0 * B)
    assert b["total_debt"] == pytest.approx(33.4 * B)
    assert "excludes lease" in b["debt_note"]


# ── 3. per-statement merge + coverage ────────────────────────────────────────

def _src(name, inc, cf, bal, fcf=1.0):
    return {"available": True, "source": name,
            "ttm": {"revenue": 10.0, "net_income": 5.0, "fcf": fcf},
            "balance": {"cash_st": 3.0, "as_of_date": bal},
            "periods": {"income": inc, "cashflow": cf, "balance": bal},
            "quarters": [{"end": inc, "revenue": 3.0, "fcf": 0.5}]}


def test_merge_picks_newest_complete_source_per_statement():
    sec = _src("SEC XBRL (10-Q filed 2026-08-26)", "2026-07-26", "2026-07-26", "2026-07-26", fcf=134.0)
    yf = _src("Yahoo statements", "2026-10-31", "2026-07-31", "2026-07-31", fcf=127.0)
    m = statements.merge([sec, yf])
    assert m["periods"]["income"] == "2026-10-31"              # Yahoo has Q3 income
    # neither has Q3 cash flow; Yahoo's "07-31" and SEC's "07-26" are the SAME
    # quarter (live NVDA 11/21) → the filed SEC numbers keep it
    assert m["periods"]["cashflow"] == "2026-07-26"
    assert m["period_sources"]["cashflow"].startswith("SEC")
    assert m["ttm"]["fcf"] == 134.0
    assert m["latest_quarter_end"] == "2026-07-26"
    assert m["period_sources"]["income"] == "Yahoo statements"


def test_coverage_names_each_lagging_statement_and_the_release():
    st = {"periods": {"income": "2026-10-31", "cashflow": "2026-07-26",
                      "balance": "2026-07-26"}, "latest_quarter_end": "2026-07-26"}
    cov = report_view.statement_coverage(st, "2026-11-17",
                                         {"date": "2026-11-17", "url": "u"})
    assert cov["covers_latest"] is False
    assert "income" not in cov["lag"]
    assert "cash flow ends 2026-07-26" in cov["lag"]["cashflow"]
    assert "8-K filed 2026-11-17" in cov["warning"]


# ── 4. headlines: whole window, tighter relevance ─────────────────────────────

def _h(head, summary="", day=21, hour=12):
    return {"headline": head, "summary": summary, "source": "X",
            "ts": int(rf.ny(2026, 11, day, hour).timestamp())}


def test_single_summary_mention_not_relevant_but_headline_is():
    items = [_h("A $2.6 Billion Fund Just Bought These 3 Stocks", "…including Nvidia."),
             _h("Nvidia falls on SEC probe report"),
             _h("Chip stocks slide", "Nvidia led losses; Nvidia shares fell 14%")]
    v = nr.filter_headlines(items, "NVDA", "NVIDIA CORP")
    heads = [h["headline"] for h in v["all_relevant"]]
    assert "A $2.6 Billion Fund Just Bought These 3 Stocks" not in heads
    assert len(heads) == 2


def test_busy_day_cannot_crowd_out_the_window():
    items = [_h(f"Nvidia item {i}", day=21, hour=1 + i % 20) for i in range(40)]
    items.append(_h("Nvidia: Reuters says SEC enforcement examining accounting", day=20))
    v = nr.filter_headlines(items, "NVDA", "NVIDIA CORP")
    days = {dt.datetime.fromtimestamp(h["ts"]).date().day for h in v["items"]}
    assert 20 in days and v["days_covered"] == 2


def test_regulatory_scan_and_move_cause():
    rel = [_h("Exclusive: SEC's Nvidia probe examining potential accounting issues", day=20),
           _h("California AG subpoenas Nvidia over chip sales", day=19),
           _h("Nvidia unveils new GPU", day=18)]
    hits = nr.regulatory_scan(rel, "2026-11-16")
    assert len(hits) == 2
    assert "sec" in hits[0]["terms"] and "subpoenas" in hits[1]["terms"]
    assert {h["date"] for h in hits} == {"2026-11-20", "2026-11-19"}
    c = nr.move_cause(rel, [], "2026-11-20", -4.46)
    assert c["status"] == "candidates found" and len(c["found"]) == 2
    none = nr.move_cause([_h("Nvidia unveils new GPU", day=20)], [], "2026-11-20", -4.46)
    assert none["status"] == "cause not found"
    assert nr.move_cause(rel, [], "2026-11-20", -0.8) is None   # ordinary move


def test_8k_red_flags():
    assert nr.describe_8k("4.02,9.01") == ("4.02 non-reliance on prior financials (restatement)", True)
    assert nr.describe_8k("2.02,9.01")[1] is False


# ── 5. earnings: Yahoo's just-passed date is not "next" ───────────────────────

def test_yahoo_past_earnings_date_ignored_no_false_disagreement():
    v = report_view.build({"symbol": "NVDA", "basis": {},
                           "now": rf.ny(2026, 11, 21, 12),
                           "earnings": {"available": True, "next_date": "2027-02-23"},
                           "earnings_confirm": {"date": "2026-11-17", "estimate": False}})
    f = v["fields"]["earnings_next"]
    assert f["tag"] == "estimated" and not f["warnings"]


# ── 6. analyst: identical snapshots + Yahoo rating changes ────────────────────

def test_unchanged_analyst_snapshot_is_flagged_and_not_scored():
    from analysis.setup_score import c_analyst_trend
    m = {"strong_buy": 25, "buy": 41, "hold": 2, "sell": 1, "strong_sell": 0, "total": 69}
    months = [{**m, "period": "2026-11-01"}, {**m, "period": "2026-10-01"}]
    assert c_analyst_trend(months, dt.date(2026, 11, 21)) is None
    v = report_view.build({"symbol": "NVDA", "basis": {}, "now": rf.ny(2026, 11, 21, 12),
                           "recs": {"available": True, "months": months},
                           "rating_changes": {"source": "Yahoo", "changes": [
                               {"date": "2026-11-18", "firm": "Melius", "action": "down",
                                "from": "Buy", "to": "Hold"}]}})
    w = " ".join(v["fields"]["analyst_mix"]["warnings"])
    assert "identical to the 2026-10-01 snapshot" in w and "1 rating change" in w
    assert "1 downgrade" in v["fields"]["analyst_actions"]["display"]


# ── 7. portfolio day P/L recomputed (broker figure larger than the position) ──

def test_portfolio_day_pl_replaced_when_broker_disagrees(tmp_db, monkeypatch):
    from analysis import composer
    acct = {"as_of": "x", "total_value": 100000.0, "cash": 10000.0, "day_pl": -25000.0,
            "positions": [{"symbol": "GOOGL", "qty": 40.0, "market_value": 10611.60,
                           "day_pl": -25000.0}]}
    quotes = {"GOOGL": {"regular_last": 265.29, "regular_change_pct": -2.70}}
    rf.install(monkeypatch, {"quotes_batch": lambda syms: quotes})
    out = composer._sanitize_day_pl(acct, False)
    p = out["positions"][0]
    assert p["day_pl"] == pytest.approx(40 * (265.29 - 265.29 / 0.973), abs=0.01)
    assert p["day_pl_broker"] == -25000.0 and "computed" in p["day_pl_source"]
    assert out["day_pl"] == pytest.approx(p["day_pl"])


# ── 8. latest-quarter revenue growth from statements ─────────────────────────

def test_revenue_growth_latest_quarter():
    qs = [{"end": "2026-10-25", "revenue": 115.8}, {"end": "2026-07-26", "revenue": 96.2},
          {"end": "2026-04-26", "revenue": 81.7}, {"end": "2026-01-25", "revenue": 68.0},
          {"end": "2025-10-26", "revenue": 57.0}]
    v = report_view.build({"symbol": "NVDA", "basis": {}, "now": rf.ny(2026, 11, 21, 12),
                           "statements": {"available": True, "ttm": {}, "quarters": qs}})
    assert v["fields"]["revenue_growth_q"]["value"] == pytest.approx((115.8 / 57.0 - 1) * 100)


# ── 9. 2028 NYSE calendar ─────────────────────────────────────────────────────

def test_2028_calendar():
    assert not mc.is_trading_day(dt.date(2028, 4, 14))          # Good Friday
    assert mc.is_trading_day(dt.date(2027, 12, 31))             # no NY holiday (Jan 1 = Sat)
    assert mc.close_time(dt.date(2028, 11, 24)) == mc.EARLY_CLOSE
    assert mc.calendar_known(dt.date(2028, 6, 1))
    assert mc.calendar_expiring(dt.date(2028, 10, 2))


def test_eps_uses_agreeing_vendors_when_filed_income_statement_lags():
    """Live NVDA 11/21: filed statements through Q2 sum to $7.91; Yahoo and
    Finnhub both carry the post-release $9.31 → P/E at $125.53 is 13.5."""
    v = report_view.build({
        "symbol": "NVDA", "now": rf.ny(2026, 11, 21, 12),
        "basis": {"price": 125.53, "price_label": "Official close"},
        "statements": {"available": True, "ttm": {"eps_diluted": 7.91},
                       "periods": {"income": "2026-07-26"}, "quarters": []},
        "statements_meta": {"lag": {"income": "does not include the latest quarter"}},
        "eps_check": {"eps_ttm": 9.31}, "vendor": {"eps_ttm": 9.3068}})
    f = v["fields"]
    assert f["eps_ttm"]["value"] == 9.31 and "Yahoo trailingEps" in f["eps_ttm"]["source"]
    assert any("filed statements" in w and "7.91" in w for w in f["eps_ttm"]["warnings"])
    assert f["pe_ttm"]["display"] == "13.5"


def test_sec_debt_uses_long_term_total_not_a_zero_short_line():
    """Live VST 11/21: ShortTermBorrowings 0 + LongTermDebt 21.146B → $21.1B,
    not $0; and no debt tag at all → None (withheld), never 0."""
    from providers.sec_xbrl import normalize
    inst = lambda v: {"units": {"USD": [{"end": "2026-09-30", "val": v, "filed": "2026-11-09"}]}}  # noqa: E731
    flow = {"units": {"USD": [{"start": "2026-01-01", "end": "2026-09-30", "val": 1.0,
                               "filed": "2026-11-09", "form": "10-Q"}]}}
    base = {"CashAndCashEquivalentsAtCarryingValue": inst(2.7e9),
            "Revenues": flow, "NetIncomeLoss": flow}
    vst = normalize({"facts": {"us-gaap": {**base, "ShortTermBorrowings": inst(0.0),
                                           "LongTermDebt": inst(21.146e9)}}})
    assert vst["balance"]["total_debt"] == pytest.approx(21.146e9)
    none = normalize({"facts": {"us-gaap": dict(base)}})
    assert none["balance"]["total_debt"] is None


def test_weeks_old_stale_section_is_withheld_not_shown():
    """Live NVDA 11/21: a 53-day-old news sentiment was on screen ("stale ·
    1270h ago"). Stale fallbacks older than 72h become 'unavailable'."""
    from analysis.composer import _drop_ancient
    old = {"status": "stale", "data": {"bullish_pct": 0.43}, "age_seconds": 1270 * 3600,
           "error_reason": "no_data"}
    out = _drop_ancient("news_sentiment", old)
    assert out["status"] == "unavailable" and "data" not in out
    assert "52.9 days old" in out["error_detail"]
    fresh = {"status": "stale", "data": {}, "age_seconds": 3 * 3600}
    assert _drop_ancient("news_sentiment", fresh) is fresh


def test_value_lens_ev_matches_report_construction():
    from analysis.value_lens import build_value_lens
    st = {"available": True, "ttm": {"fcf": 127e9},
          "balance": {"total_debt": 33.4e9, "cash": 56.6e9, "equity_securities": 42.8e9}}
    vl = build_value_lens(3.025e12, st)
    assert vl["ev"]["value"] == pytest.approx((3.025e12 + 33.4e9 - 56.6e9 - 42.8e9) / 1e6)


def test_portfolio_day_pl_uses_same_basis_as_report(tmp_db, monkeypatch):
    """Live ANT 11/21: Schwab's % change (−9.06%) disagreed with the official
    closes ($97.17 → $88.91, −8.50%) — the report's basis. One basis."""
    from analysis import composer
    hist = rf.bars_ending(dt.date(2026, 11, 20), [97.17, 88.91], n=30, daily=0.05)
    quotes = {"ANT": {"regular_last": 88.91, "regular_change_pct": -9.06,
                      "quote_time_ms": rf.ms(rf.ny(2026, 11, 20, 19, 59))}}
    rf.install(monkeypatch, {"quotes_batch": lambda syms: quotes,
                             "daily_history": lambda s, *a: hist})
    acct = {"as_of": "x", "total_value": 100000.0, "positions": [
        {"symbol": "ANT", "qty": 40.0, "market_value": 3556.40, "day_pl": 0.0}]}
    p = composer._sanitize_day_pl(acct, False, now=rf.NVDA_NOW)["positions"][0]
    assert p["day_pl"] == pytest.approx(40 * (88.91 - 97.17))



def test_screener_day_move_is_the_session_not_after_hours(tmp_db):
    """Live 11/21: the Watchlist showed SPY +1.11% (after-hours) for a Friday
    that fell sharply. The row now uses the report's price basis."""
    from analysis.screener import screen_symbol
    p = rf.nvda_providers()
    prov = {"quote": p["quotes"], "history": p["daily_history"],
            "options": None, "earnings": None}
    row = screen_symbol("NVDA", prov, now=rf.NVDA_NOW)
    assert row["price"] == 125.53
    assert row["day_pct"] == pytest.approx(-14.01, abs=0.01)
    assert row["day_label"] == "Fri Nov 20"
    assert row["outside_band_yesterday"] is True



def test_discussion_stored_under_old_and_new_path_is_merged(tmp_db, tmp_path, monkeypatch):
    """After the rename, 45 discussions existed twice (~/.tickerlens/... and
    ~/.tickerbands/..., same file via the symlink). Keep one, under the
    current home, with the ORIGINAL decision price."""
    import os

    import config
    from db import connect
    from discussions_svc.parser import dedupe_linked_paths, list_discussions
    new_home = tmp_path / ".tickerbands"
    (new_home / "discussions" / "MCD").mkdir(parents=True)
    f = new_home / "discussions" / "MCD" / "a.md"
    f.write_text("x")
    old_home = tmp_path / ".tickerlens"
    os.symlink(new_home, old_home)
    monkeypatch.setattr(config, "TICKERBANDS_HOME", str(new_home))
    conn = connect()
    for path, price in ((str(old_home / "discussions/MCD/a.md"), 280.10),
                        (str(f), 301.55)):            # re-read copy: later price
        conn.execute("INSERT INTO claude_discussions (ticker, file_path, response_markdown, "
                     "created_ts, file_mtime, decision, decision_price) VALUES "
                     "('MCD', ?, 'x', '2026-10-22T23:33:11', 'm', 'hold', ?)", (path, price))
    conn.commit()
    conn.close()
    out = dedupe_linked_paths()
    assert out == {"merged": 1, "removed": 1}
    rows = list_discussions("MCD")
    assert len(rows) == 1 and rows[0]["file_path"] == str(f)
    assert rows[0]["decision_price"] == 280.10
    assert dedupe_linked_paths() == {"merged": 0, "removed": 0}     # idempotent
