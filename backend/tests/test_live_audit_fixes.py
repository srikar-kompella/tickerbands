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
    days = {nr.ny_date(h["ts"]).day for h in v["items"]}
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


# ── prompt v5 / research quality ─────────────────────────────────────────────

def test_unquoted_colon_in_frontmatter_is_salvaged_not_dropped(tmp_path):
    """The v4 prompt's own example (`news_note: Nov 14–21: SEC probe …`) is
    invalid YAML when written unquoted — the whole file used to be rejected."""
    from discussions_svc.parser import parse_discussion_file
    f = tmp_path / "NVDA" / "x.md"
    f.parent.mkdir()
    f.write_text("---\nticker: NVDA\ncreated_ts: 2026-11-22T10:00:00\n"
                 "news_note: Nov 14–21: SEC probe reported (Reuters 11/20)\n"
                 "stance: hold\nupgrade_if: [revenue growth >= 12% in the Q1 release, SEC closes inquiry]\n"
                 "research_status: complete\nprice_used: $125.53\n---\n## TL;DR\nBody.")
    row = parse_discussion_file(str(f))
    assert row["news_note"] == "Nov 14–21: SEC probe reported (Reuters 11/20)"
    assert row["stance"] == "hold"
    import json
    rm = json.loads(row["research_meta"])
    assert rm["upgrade_if"] == ["revenue growth >= 12% in the Q1 release", "SEC closes inquiry"]
    assert rm["research_status"] == "complete" and rm["price_used"] == 125.53
    assert "recovered line by line" in rm["parse_note"]


def test_research_meta_parsed_from_valid_yaml(tmp_path):
    from discussions_svc.parser import parse_discussion_file
    import json
    f = tmp_path / "AXP.md"
    f.write_text('---\nticker: AXP\nprompt_version: 5\nresearch_status: partial\n'
                 'owned: yes\nmove_cause: not_found\nsupersedes: ["2026-11-21T10:05:00"]\n'
                 'downgrade_if:\n  - "credit losses above 3%"\nreview_by: 2027-01-31\n'
                 'stance_confidence: Medium\n---\nbody')
    rm = json.loads(parse_discussion_file(str(f))["research_meta"])
    assert rm == {"research_status": "partial", "move_cause": "not_found", "owned": "yes",
                  "stance_confidence": "medium", "supersedes": ["2026-11-21T10:05:00"],
                  "downgrade_if": ["credit losses above 3%"], "review_by": "2027-01-31",
                  "prompt_version": 5}


# ── parser: nothing the prompt requires is dropped or mangled silently ───────

def _parse(tmp_path, text, ticker="ZZZT", name="note.md", raw=False):
    from discussions_svc.parser import parse_discussion_file
    import json
    d = tmp_path / ticker
    d.mkdir(exist_ok=True)
    f = d / name
    if raw:
        f.write_bytes(text)
    else:
        f.write_text(text, encoding="utf-8")
    row = parse_discussion_file(str(f))
    return row, json.loads(row["research_meta"] or "{}")


def test_template_placeholders_are_dropped_from_scalars_and_list_items(tmp_path):
    """A leftover "<…>" used to be stored: `upgrade_if` with only the
    placeholder hid the no-conditions flag, and a placeholder under
    `plan_triggers_met` showed as a met trigger."""
    from analysis import research_quality as rq
    import datetime as dt
    row, rm = _parse(tmp_path, """---
ticker: "ZZZT"
created_ts: 2031-02-03T10:15:00
prompt_version: 5
summary: "<your TL;DR in one line — shown on the timeline>"
tags: ["<kebab-case-tag>", "plan-check"]
research_status: "complete"
price_used: 12.34
stance: "hold"
stance_horizon: "<for example 3-6mo>"
stance_confidence: "medium"
stance_note: '<one line of reasoning behind the stance>'
upgrade_if: ["<dated, checkable condition>"]
downgrade_if: ["<dated, checkable condition>", "by 2031-03-15: recall cost above $5M"]
plan_triggers_met: ["<a trigger of the user's that is met, with the evidence>"]
plan_triggers_unknown:
  - "<a trigger you could not check>"
review_by: "<YYYY-MM-DD>"
supersedes: ["<created_ts of an earlier note this one replaces>"]
---
## TL;DR
Body line.""")
    assert "upgrade_if" not in rm and "plan_triggers_met" not in rm
    assert "plan_triggers_unknown" not in rm and "supersedes" not in rm
    assert rm["downgrade_if"] == ["by 2031-03-15: recall cost above $5M"]
    assert "review_by" not in rm and "unreadable" not in rm
    assert row["summary"] == "TL;DR" and row["stance_note"] is None
    assert row["stance_horizon"] is None and row["tags"] == '["plan-check"]'
    w = rq.stance_flags({**row, "research_meta": rm}, None, dt.date(2031, 2, 3))
    assert "hold with no conditions — treat as no stance (missing: upgrade_if)" in w
    assert not any("trigger(s) met" in x for x in w)
    assert any(x.startswith("missing required field(s): review_by") for x in w)
    # text that merely contains angle brackets is kept
    row, rm = _parse(tmp_path, '---\nticker: ZZZT\nupgrade_if: ["margin <40% or >45%"]\n'
                               'stance_note: "P/E < 12 and FCF yield > 6%"\n---\nbody',
                     name="b.md")
    assert rm["upgrade_if"] == ["margin <40% or >45%"]
    assert row["stance_note"] == "P/E < 12 and FCF yield > 6%"


def test_stance_words_add_exit_pass_are_mapped_and_unknown_ones_are_reported(tmp_path):
    for i, (written, want) in enumerate([("add", "buy"), ("Exit", "sell"), ("pass", "sell"),
                                         ("avoid", "sell"), ('"Trim."', "trim"),
                                         ("watch", "watch")]):
        row, rm = _parse(tmp_path, f"---\nticker: ZZZT\nstance: {written}\n---\nbody",
                         name=f"s{i}.md")
        assert row["stance"] == want, written
        assert "unreadable" not in rm
    row, rm = _parse(tmp_path, '---\nticker: ZZZT\nstance: "accumulate on dips"\n---\nbody',
                     name="u.md")
    assert row["stance"] is None and rm["unreadable"] == {"stance": "accumulate on dips"}
    # no stance key at all is not "unreadable"
    row, rm = _parse(tmp_path, "---\nticker: ZZZT\nsummary: s\n---\nbody", name="n.md")
    assert row["stance"] is None and "unreadable" not in rm


def test_price_used_and_review_by_are_read_or_reported_never_dropped(tmp_path):
    row, rm = _parse(tmp_path, '---\nticker: ZZZT\nprompt_version: 5\n'
                               'price_used: "$130.03 (after hours)"\n'
                               'review_by: "Mar 9, 2027"\nsources_count: "7 sources"\n'
                               '---\nbody')
    assert rm["price_used"] == 130.03 and rm["price_used_note"] == "extended-hours"
    assert rm["review_by"] == "2027-03-09" and rm["sources_count"] == 7
    assert "unreadable" not in rm
    for written, want in (("1,234.50", 1234.5), ("$12.34", 12.34), ("12.34 USD", 12.34),
                          ("pre-market $12.80", 12.8)):
        _, rm = _parse(tmp_path, f'---\nticker: ZZZT\nprice_used: "{written}"\n---\nbody',
                       name="p.md")
        assert rm["price_used"] == want, written
    assert rm["price_used_note"] == "extended-hours"            # the pre-market one
    # a number buried in text is not guessed at; a non-date is not a date
    _, rm = _parse(tmp_path, '---\nticker: ZZZT\nprice_used: "Nov 20 close"\n'
                             'review_by: "after the next earnings"\n'
                             'research_status: "done"\n---\nbody', name="q.md")
    assert "price_used" not in rm and "review_by" not in rm and "research_status" not in rm
    assert rm["unreadable"] == {"research_status": "done", "price_used": "Nov 20 close",
                                "review_by": "after the next earnings"}
    from analysis import research_quality as rq
    import datetime as dt
    w = rq.stance_flags({"stance": "buy", "created_ts": "2031-02-03T10:15:00",
                         "research_meta": rm}, None, dt.date(2031, 2, 3))
    assert "review_by unreadable: 'after the next earnings' — the stance has no review date" in w
    assert "price_used unreadable: 'Nov 20 close'" in w
    assert "unreadable field(s): research_status 'done'" in w


def test_salvage_keeps_a_comma_inside_a_quoted_list_item(tmp_path):
    row, rm = _parse(tmp_path, """---
ticker: ZZZT
news_note: Jan 27–Feb 3: recall reported (Example Wire 01/30)
stance: hold
downgrade_if: ["recall cost above $5,000M", 'any restatement, of any year', the user's stop is hit, plain item]
upgrade_if: ["by 2031-05-05: revenue growth above 10%, per the Q1 release"]
tags: [a, "b, c"]
---
body""")
    assert "recovered line by line" in rm["parse_note"]          # the salvage path ran
    assert rm["downgrade_if"] == ["recall cost above $5,000M", "any restatement, of any year",
                                  "the user's stop is hit", "plain item"]
    assert rm["upgrade_if"] == ["by 2031-05-05: revenue growth above 10%, per the Q1 release"]
    assert row["tags"] == '["a", "b, c"]'


def test_yaml_list_item_read_as_a_mapping_is_stored_as_its_text(tmp_path):
    row, rm = _parse(tmp_path, """---
ticker: ZZZT
stance: watch
upgrade_if:
  - by 2027-03-01: SEC closes inquiry
  - "by 2027-04-01: guidance raised"
downgrade_if: [by 2027-03-15: an 8-K item 4.02 is filed, plain item]
supersedes: [2031-01-28T09:00:00]
---
body""")
    assert "parse_note" not in rm                                # strict YAML accepted it
    assert rm["upgrade_if"] == ["by 2027-03-01: SEC closes inquiry",
                                "by 2027-04-01: guidance raised"]
    assert rm["downgrade_if"] == ["by 2027-03-15: an 8-K item 4.02 is filed", "plain item"]
    assert rm["supersedes"] == ["2031-01-28T09:00:00"]
    assert not [x for k in ("upgrade_if", "downgrade_if") for x in rm[k] if "{" in x]


def test_unquoted_boolean_looking_ticker_falls_back_to_the_folder(tmp_path):
    for i, written in enumerate(("ON", "NO", "YES", "TRUE", "true")):
        row, _ = _parse(tmp_path, f"---\nticker: {written}\nsummary: s\n---\nbody",
                        ticker="ON", name=f"t{i}.md")
        assert row["ticker"] == "ON", written
    row, _ = _parse(tmp_path, '---\nticker: "ON"\n---\nbody', ticker="elsewhere")
    assert row["ticker"] == "ON"                                 # quoted → taken as written


V5_FILE = ('---\nticker: "ZZZT"\ncreated_ts: 2031-02-03T10:15:00\nprompt_version: 5\n'
           'summary: "Recall is the reported cause"\nstance: "hold"\n'
           'research_status: "complete"\n---\n## TL;DR\nBody line.\n')


def test_file_saved_with_its_markdown_fence_or_a_bom_still_parses(tmp_path):
    want_row, want_rm = _parse(tmp_path, V5_FILE, name="plain.md")
    assert want_row["stance"] == "hold" and want_rm["research_status"] == "complete"
    variants = {
        "fence.md": "```markdown\n" + V5_FILE + "```\n",
        "fence_md.md": "```md\n" + V5_FILE + "```",
        "fence_bare.md": "\n```\n" + V5_FILE + "\n```\n\n",
        "fence_open_only.md": "```markdown\n" + V5_FILE,
        "crlf.md": V5_FILE.replace("\n", "\r\n"),
        "blank_lines_first.md": "\n\n" + V5_FILE,
    }
    for name, text in variants.items():
        row, rm = _parse(tmp_path, text, name=name)
        for k in ("ticker", "summary", "stance", "created_ts", "response_markdown"):
            assert row[k] == want_row[k], (name, k)
        assert rm == want_rm, name
    for name, data in (("bom.md", b"\xef\xbb\xbf" + V5_FILE.encode()),
                       ("bom_fence.md", b"\xef\xbb\xbf```markdown\n" + V5_FILE.encode()
                        + b"```\n")):
        row, rm = _parse(tmp_path, data, name=name, raw=True)
        assert row["summary"] == "Recall is the reported cause" and row["stance"] == "hold", name
        assert rm == want_rm, name
    # a body that simply opens with a code block is not a wrapped file
    row, _ = _parse(tmp_path, "```python\nprint(1)\n```\nrest", name="code.md")
    assert row["response_markdown"].startswith("```python")


def test_unclosed_frontmatter_is_rejected_not_ingested_as_the_body(tmp_path):
    from discussions_svc.parser import ParseError
    for name, text in (("unclosed.md", '---\nticker: "ZZZT"\nstance: "hold"\n## TL;DR\nBody'),
                       ("two_blocks.md", '---\nticker: "ZZZT"\n---\n---\nstance: "hold"\n'
                                         '---\n## TL;DR\nBody'),
                       ("fenced_unclosed.md", "```markdown\n---\nticker: ZZZT\nbody\n```")):
        with pytest.raises(ParseError, match="still starts with '---'"):
            _parse(tmp_path, text, name=name)
    # one stray extra delimiter in front of real prose is dropped, not fatal
    row, _ = _parse(tmp_path, V5_FILE.replace("---\n## TL;DR", "---\n---\n## TL;DR"),
                    name="stray_rule.md")
    assert row["response_markdown"] == "## TL;DR\nBody line." and row["stance"] == "hold"
    with pytest.raises(ParseError, match="empty body"):
        _parse(tmp_path, '---\nticker: "ZZZT"\n---\n---\n', name="only_rule.md")



# ── parser: chat text around the file, near-miss values, odd YAML shapes ─────

def test_chat_line_before_the_fence_is_stripped_not_ingested_as_the_summary(tmp_path):
    """A claude.ai reply saved whole — "Here is the complete file to save:"
    then the fenced file — used to ingest as a note whose summary was that
    line, with every frontmatter field lost."""
    want_row, want_rm = _parse(tmp_path, V5_FILE, name="plain.md")
    variants = {
        "pre.md": "Here is the complete file to save:\n\n```markdown\n" + V5_FILE + "```\n",
        "pre_two.md": "Done.\nSave this at the path above:\n```\n" + V5_FILE + "```",
        "pre_post.md": ("Here is the complete file to save:\n```markdown\n" + V5_FILE
                        + "```\n\nLet me know if you want the stance changed."),
        "post_only.md": "```markdown\n" + V5_FILE + "```\nSaved as requested.\n",
        "tilde.md": "Here it is:\n~~~markdown\n" + V5_FILE + "~~~\n",
    }
    for name, text in variants.items():
        row, rm = _parse(tmp_path, text, name=name)
        for k in ("ticker", "summary", "stance", "created_ts", "response_markdown"):
            assert row[k] == want_row[k], (name, k)
        assert rm == want_rm, name
    # a code block inside the body survives, with or without chat around the file
    with_code = V5_FILE + "\n```python\nprint(1)\n```\nAfter the block.\n"
    for name, text in (("code.md", "```markdown\n" + with_code + "```\n"),
                       ("code_pre.md", "Here you go:\n```markdown\n" + with_code + "```\n")):
        row, _ = _parse(tmp_path, text, name=name)
        assert row["response_markdown"].endswith("```python\nprint(1)\n```\nAfter the block."), name
        assert row["stance"] == "hold", name


def test_raw_frontmatter_left_in_the_body_is_rejected(tmp_path):
    """What the stripping cannot undo is refused, never filed as the analysis."""
    from discussions_svc.parser import ParseError
    long_chat = "\n".join(f"Chat line {i}." for i in range(8))
    for name, text in (
            ("long_chat.md", long_chat + "\n```markdown\n" + V5_FILE + "```\n"),
            ("bare.md", "Here is the complete file to save:\n" + V5_FILE),
            ("twice.md", V5_FILE + "\nAnd again:\n```markdown\n" + V5_FILE + "```\n")):
        with pytest.raises(ParseError, match="frontmatter block"):
            _parse(tmp_path, text, name=name)
    # a horizontal rule in the prose is not a frontmatter block
    row, _ = _parse(tmp_path, V5_FILE + "\n---\n\nSecond section.\n", name="rule.md")
    assert row["response_markdown"].endswith("---\n\nSecond section.")
    # a fence that does not open a note is not a wrapper
    row, _ = _parse(tmp_path, "Intro line\n```\n---\nnot yaml at all\n```\nrest", name="x.md")
    assert row["summary"] == "Intro line"


def test_news_view_aliases_are_read_and_other_words_are_reported(tmp_path):
    for i, (written, want) in enumerate([("negative", "bearish"), ("Positive", "bullish"),
                                         ("mixed", "neutral"), ('"Bearish."', "bearish"),
                                         ("neutral", "neutral")]):
        row, rm = _parse(tmp_path, f"---\nticker: ZZZT\nnews_view: {written}\n---\nbody",
                         name=f"v{i}.md")
        assert row["news_view"] == want, written
        assert "unreadable" not in rm
    row, rm = _parse(tmp_path, '---\nticker: ZZZT\nnews_view: "cautiously optimistic"\n---\nbody',
                     name="u.md")
    assert row["news_view"] is None
    assert rm["unreadable"] == {"news_view": "cautiously optimistic"}
    # absent, or the template slot left as it was, is not "unreadable"
    for name, line in (("a.md", "summary: s"),
                       ("t.md", 'news_view: "<bullish|neutral|bearish>"')):
        row, rm = _parse(tmp_path, f"---\nticker: ZZZT\n{line}\n---\nbody", name=name)
        assert row["news_view"] is None and "unreadable" not in rm, name


def test_stance_word_left_in_template_brackets_is_unreadable_not_that_stance(tmp_path):
    for i, written in enumerate(('"<hold>"', "<Hold>", '"<add>"', "'<watch>'")):
        row, rm = _parse(tmp_path, f"---\nticker: ZZZT\nstance: {written}\n---\nbody",
                         name=f"b{i}.md")
        assert row["stance"] is None, written
        assert rm["unreadable"]["stance"].strip("\"'").lower() == written.strip("\"'").lower()
    # the untouched slot is simply no stance
    row, rm = _parse(tmp_path, '---\nticker: ZZZT\nstance: "<buy|sell|hold|trim|watch>"\n'
                               '---\nbody', name="slot.md")
    assert row["stance"] is None and "unreadable" not in rm


def test_near_miss_enum_values_are_normalized_or_fail_closed(tmp_path):
    row, rm = _parse(tmp_path, '---\nticker: ZZZT\nprompt_version: 5\n'
                               'research_status: "Incomplete"\nmove_cause: "Not Found"\n'
                               'owned: " NO "\nstance_confidence: "HIGH."\n---\nbody')
    assert rm["research_status"] == "partial" and rm["move_cause"] == "not_found"
    assert rm["owned"] == "no" and rm["stance_confidence"] == "high"
    assert "unreadable" not in rm
    for i, (written, want) in enumerate([("not-found", "not_found"), ("NOT_FOUND", "not_found"),
                                         ("Found", "found"), ("N/A", "na"), ("na", "na")]):
        _, rm = _parse(tmp_path, f'---\nticker: ZZZT\nmove_cause: "{written}"\n---\nbody',
                       name=f"m{i}.md")
        assert rm["move_cause"] == want, written
    # anything else is not guessed at: it is unreadable, and a v5 note with an
    # unreadable research_status or move_cause is INVALID
    row, rm = _parse(tmp_path, '---\nticker: ZZZT\ncreated_ts: 2031-02-03T10:15:00\n'
                               'prompt_version: 5\nresearch_status: "mostly complete"\n'
                               'move_cause: "macro, probably"\nstance: "hold"\n---\nbody',
                     name="bad.md")
    assert "research_status" not in rm and "move_cause" not in rm
    assert rm["unreadable"] == {"research_status": "mostly complete",
                                "move_cause": "macro, probably"}
    from analysis import research_quality as rq
    got = rq.prior_note_status([{**row, "research_meta": rm}], None, None)
    assert got[0]["status"] == "INVALID"
    assert "research_status could not be read ('mostly complete')" in got[0]["reason"]


def test_odd_yaml_shapes_are_coerced_not_fatal(tmp_path):
    import json
    row, _ = _parse(tmp_path, "---\nticker: ZZZT\ntags: 7\n"
                              "created_ts: [2031-02-03T10:15:00, 2031-02-04T09:00:00]\n"
                              "summary: [a, b]\nprompt: {k: v}\n---\nbody", name="odd.md")
    assert json.loads(row["tags"]) == ["7"]
    assert row["created_ts"] == "2031-02-03T10:15:00"
    assert isinstance(row["summary"], str) and isinstance(row["prompt"], str)
    row, _ = _parse(tmp_path, "---\nticker: ZZZT\ntags: {theme: ai, kind: review}\n"
                              "created_ts: {a: 1}\n---\nbody", name="odd2.md")
    assert json.loads(row["tags"]) == ["theme: ai", "kind: review"]
    assert row["created_ts"] == row["file_mtime"]            # not text, not a date → mtime
    row, _ = _parse(tmp_path, "---\nticker: ZZZT\ntags: [a, 3, 2031-02-03]\ncreated_ts: 20310203\n"
                              "---\nbody", name="odd3.md")
    assert json.loads(row["tags"]) == ["a", "3", "2031-02-03"]
    assert row["created_ts"] == row["file_mtime"]


def test_any_parse_failure_is_a_parse_error(tmp_path, monkeypatch):
    """An exception nobody anticipated must not leave a file neither
    ingested nor quarantined."""
    from discussions_svc import parser
    monkeypatch.setattr(parser, "_research_meta",
                        lambda meta: (_ for _ in ()).throw(KeyError("boom")))
    with pytest.raises(parser.ParseError, match="could not read the file: KeyError"):
        _parse(tmp_path, V5_FILE, name="boom.md")


def test_watcher_quarantines_on_any_exception_and_keeps_a_good_file_on_a_db_error(
        tmp_db, tmp_discussions, monkeypatch):
    from tests.conftest import write_md
    from discussions_svc import watcher
    from discussions_svc.parser import list_discussions
    monkeypatch.setattr(watcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(watcher, "_wait_until_stable", lambda p: True)
    events = []
    monkeypatch.setattr(watcher.broadcaster, "publish_threadsafe", events.append)

    # not a ParseError: still quarantined, with an error event
    real_parse = watcher.parse_discussion_file
    path = write_md(tmp_discussions, "ZZZT", "odd.md", ["ticker: ZZZT", "summary: s"])
    monkeypatch.setattr(watcher, "parse_discussion_file",
                        lambda p: (_ for _ in ()).throw(TypeError("unhashable type")))
    watcher._ingest(path)
    assert not (tmp_discussions / "ZZZT" / "odd.md").exists()
    assert (tmp_discussions / "_failed" / "odd.md").exists()
    assert events == [{"type": "error", "file": "odd.md", "detail": "unhashable type"}]
    assert list_discussions("ZZZT") == []

    # the file is fine but the store fails: reported, and the file stays put
    monkeypatch.setattr(watcher, "parse_discussion_file", real_parse)
    events.clear()
    good = write_md(tmp_discussions, "ZZZT", "good.md", ["ticker: ZZZT", "summary: s"])
    monkeypatch.setattr(watcher, "upsert_discussion",
                        lambda row: (_ for _ in ()).throw(RuntimeError("database is locked")))
    watcher._ingest(good)
    assert (tmp_discussions / "ZZZT" / "good.md").exists()
    assert not (tmp_discussions / "_failed" / "good.md").exists()
    assert events == [{"type": "error", "file": "good.md",
                       "detail": "parsed but not stored: database is locked"}]


def test_catch_up_scan_survives_a_file_that_raises(tmp_db, tmp_discussions, monkeypatch):
    from tests.conftest import write_md
    from discussions_svc import watcher
    from discussions_svc.parser import list_discussions
    write_md(tmp_discussions, "AAA", "1.md", ["ticker: AAA", "summary: first"])
    write_md(tmp_discussions, "BBB", "2.md", ["ticker: BBB", "summary: second"])
    real = watcher.parse_discussion_file

    def flaky(path):
        if "/AAA/" in path:
            raise ValueError("unexpected")
        return real(path)
    monkeypatch.setattr(watcher, "parse_discussion_file", flaky)
    assert watcher.catch_up_scan() >= 2
    assert [d["summary"] for d in list_discussions("BBB")] == ["second"]
    assert list_discussions("AAA") == []


# ── final-review fixes: split fences, "$" tickers, the startup scan ──────────

def test_note_saved_from_two_code_blocks_is_rejected(tmp_path):
    """A reply that put the frontmatter in one code block and the analysis in
    a second one, saved whole: the first fence is stripped as the wrapper,
    and what is left under the frontmatter is the first block's closing
    fence, the chat line and the second block's opening fence. That residue
    used to be filed as the analysis."""
    from discussions_svc.parser import ParseError
    at = V5_FILE.index("## TL;DR")
    front, body = V5_FILE[:at], V5_FILE[at:]
    variants = {
        "split_pre.md": ("Here is the frontmatter:\n```markdown\n" + front
                         + "```\nAnd the analysis:\n```markdown\n" + body + "```\n"),
        "split.md": ("```markdown\n" + front + "```\n\nAnd the analysis:\n\n```markdown\n"
                     + body + "```\n"),
        "split_no_chat.md": "```\n" + front + "```\n```\n" + body + "```",
        "split_tilde.md": ("Part one:\n~~~markdown\n" + front + "~~~\nPart two:\n~~~\n"
                           + body + "~~~\n"),
        # no wrapper to strip at all: frontmatter, then a stray bare fence
        "stray_fence.md": front + "```\n" + body,
    }
    for name, text in variants.items():
        with pytest.raises(ParseError, match="starts with a bare code fence"):
            _parse(tmp_path, text, name=name)
    # a body that opens with a labeled code block is still a body
    row, _ = _parse(tmp_path, front + "```python\nprint(1)\n```\n" + body, name="code_first.md")
    assert row["response_markdown"].startswith("```python\nprint(1)\n```\n## TL;DR")
    # and one whole fenced file still parses, with a code block inside the body
    row, _ = _parse(tmp_path, "```markdown\n" + V5_FILE + "\n```\nx = 1\n```\nDone.\n```\n",
                    name="inner_code.md")
    assert row["response_markdown"] == "## TL;DR\nBody line.\n\n```\nx = 1\n```\nDone."


def test_leading_dollar_sign_is_dropped_from_the_ticker(tmp_path):
    for i, written in enumerate(('"$zzzt"', "$ZZZT", "' $zzzt '", "ZZZT")):
        row, _ = _parse(tmp_path, f"---\nticker: {written}\n---\nbody", name=f"t{i}.md")
        assert row["ticker"] == "ZZZT", written
    # only a "$": the folder name decides, and a "$" there is dropped too
    row, _ = _parse(tmp_path, '---\nticker: "$"\n---\nbody', ticker="$zzqx", name="d.md")
    assert row["ticker"] == "ZZQX"


def test_list_discussions_accepts_a_dollar_prefixed_ticker(tmp_db):
    from discussions_svc.parser import list_discussions
    rf.add_discussion("ZZZT", "2031-02-03T10:15:00", "One note")
    assert [d["summary"] for d in list_discussions("$zzzt")] == ["One note"]


def test_catch_up_scan_quarantines_a_rejected_file_and_says_so(
        tmp_db, tmp_discussions, capsys, monkeypatch):
    """The startup scan used to skip a file the parser rejects without a
    word: it stayed in its ticker folder, never ingested, never reported."""
    import os
    import time

    from tests.conftest import write_md
    from discussions_svc import watcher
    from discussions_svc.parser import list_discussions
    write_md(tmp_discussions, "ZZZT", "good.md", ["ticker: ZZZT", "summary: fine"])
    unclosed = '---\nticker: "ZZZT"\nstance: "hold"\n## TL;DR\nBody'
    bad = tmp_discussions / "ZZZT" / "bad.md"
    bad.write_text(unclosed)
    old = time.time() - 60
    os.utime(bad, (old, old))
    fresh = tmp_discussions / "ZZZT" / "fresh.md"     # touched just now: may be mid-write
    fresh.write_text(unclosed)

    assert watcher.catch_up_scan() >= 3
    assert not bad.exists() and (tmp_discussions / "_failed" / "bad.md").exists()
    assert (tmp_discussions / "_failed" / "bad.md").read_text() == unclosed
    assert fresh.exists() and not (tmp_discussions / "_failed" / "fresh.md").exists()
    out = capsys.readouterr().out
    line, = [ln for ln in out.splitlines() if "rejected at startup" in ln]
    assert line.startswith("[tickerbands] discussion rejected at startup: ")
    assert "bad.md" in line and "still starts with '---'" in line
    assert f"(moved to {tmp_discussions / '_failed' / 'bad.md'})" in line
    assert "fresh.md" not in out
    assert [d["summary"] for d in list_discussions("ZZZT")] == ["fine"]

    # the next scan does not look inside _failed/ again; once the fresh file
    # has settled it is quarantined too
    os.utime(fresh, (old, old))
    assert watcher.catch_up_scan() == 2
    assert not fresh.exists() and (tmp_discussions / "_failed" / "fresh.md").exists()
    assert "fresh.md" in capsys.readouterr().out
    assert watcher.catch_up_scan() == 1

    # a file that parses but cannot be stored stays where it is, and is named
    good2 = write_md(tmp_discussions, "ZZZT", "good2.md", ["ticker: ZZZT", "summary: later"])
    os.utime(good2, (old, old))
    monkeypatch.setattr(watcher, "upsert_discussion",
                        lambda row: (_ for _ in ()).throw(RuntimeError("database is locked")))
    assert watcher.catch_up_scan() == 2
    assert os.path.exists(good2) and not (tmp_discussions / "_failed" / "good2.md").exists()
    out = capsys.readouterr().out
    assert "discussion parsed but not stored" in out and "database is locked" in out


def test_catch_up_scan_reports_a_file_it_could_not_move(tmp_db, tmp_discussions, capsys,
                                                        monkeypatch):
    import os
    import time

    from discussions_svc import watcher
    (tmp_discussions / "ZZZT").mkdir()
    bad = tmp_discussions / "ZZZT" / "bad.md"
    bad.write_text("---\nticker: ZZZT\n---\n")           # empty body
    old = time.time() - 60
    os.utime(bad, (old, old))
    monkeypatch.setattr(watcher.shutil, "move",
                        lambda *a: (_ for _ in ()).throw(OSError("read-only")))
    assert watcher.catch_up_scan() == 1
    assert bad.exists()
    out = capsys.readouterr().out
    assert "empty body" in out and "(could not be moved to _failed/)" in out


def test_ny_date_is_the_new_york_calendar_day_whatever_the_machine_zone():
    """8 PM ET on Fri Nov 20 is Nov 21 in UTC; market days are NY days."""
    import datetime as _dt
    ts = _dt.datetime(2026, 11, 21, 1, 0, tzinfo=_dt.timezone.utc).timestamp()
    assert nr.ny_date(ts) == _dt.date(2026, 11, 20)
