"""Backend data fixes, round 3 (2026-11-22). One test (or group) per fix:

 1. bank tangible book: preferred equity under the APIC / liquidation tags,
    intangibles filed only at fiscal-year end, missing-component warnings,
    preferred shares with no value → P/TBV withheld
 2. ADRs (market cap withheld, and everything priced off it) and non-USD
    reporters (absolute statement amounts withheld, ratios kept)
 3. no refetch loop: a failed forced refresh leaves a marker; exhibit and
    security-profile failures are cached briefly
 4. `sec_filing_forms` on the analysis payload
 5. fund NAV row is dated; no premium/discount from a stale or undated NAV
 6. forward-yield "new level" exemption needs two agreeing payments outside
    the prior band and steady prior payments
 7. banks: value-lens acquisitions blank; vendor TTM revenue growth withheld
    for banks and when it is > 50 points from the filed quarter's
 8. value_lens.fy() never prints "FY −0"
"""
from __future__ import annotations

import time

import pytest

from analysis import report_view, value_lens
from tests.test_data_fixes_1122 import BANK, FUND, M, _div, _stmts, _view, client  # noqa: F401

B = 1e9


def _inst(end, val, filed="2031-11-05"):
    return {"end": end, "val": val, "form": "10-Q", "filed": filed}


def _dur(s, e, v, form="10-Q"):
    return {"start": s, "end": e, "val": v, "form": form, "filed": "2031-11-05",
            "accn": "0000000000-31-000001"}


_NI = [_dur("2030-01-01", "2030-12-31", 30 * B, "10-K"), _dur("2030-01-01", "2030-09-30", 22 * B),
       _dur("2031-01-01", "2031-09-30", 33 * B), _dur("2031-07-01", "2031-09-30", 12 * B)]


def _bank_facts(extra: dict) -> dict:
    us = {"NetIncomeLoss": {"units": {"USD": _NI}},
          "CashAndDueFromBanks": {"units": {"USD": [_inst("2031-09-30", 20 * B)]}},
          "StockholdersEquity": {"units": {"USD": [_inst("2031-09-30", 372.4 * B)]}},
          "Goodwill": {"units": {"USD": [_inst("2031-09-30", 52.7 * B)]}}}
    us.update(extra)
    return {"facts": {"us-gaap": us}}


# ── 1. bank tangible book ────────────────────────────────────────────────────

def test_preferred_under_the_apic_tag_and_intangibles_from_the_fiscal_year_end():
    from providers.sec_xbrl import normalize
    s = normalize(_bank_facts({
        # JPM: PreferredStockValue last filed in 2009
        "PreferredStockValue": {"units": {"USD": [_inst("2009-06-30", 8.15 * B, "2009-08-10")]}},
        "PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount":
            {"units": {"USD": [_inst("2031-09-30", 21.04 * B)]}},
        "PreferredStockLiquidationPreferenceValue":
            {"units": {"USD": [_inst("2031-09-30", 21.2 * B)]}},
        # filed only in the 10-K
        "FiniteLivedIntangibleAssetsNet":
            {"units": {"USD": [_inst("2030-12-31", 1.3 * B, "2031-02-13")]}},
    }))
    b = s["balance"]
    assert b["preferred_equity"] == pytest.approx(21.04 * B) and b["preferred_found"] is True
    assert b["goodwill_intangibles"] == pytest.approx(54.0 * B)
    assert b["goodwill_intangibles_missing"] == []
    assert b["goodwill_intangibles_dates"] == {"other intangibles": "2030-12-31"}
    # into the report: (372.4 − 21.04 − 52.7 − 1.3) = 297.36B; mcap 825.4B → 2.78x
    f = _view(statements=s, security=BANK, shares_out_m=825.4 * B / 50.0 / 1e6)["fields"]["p_tbv"]
    assert f["display"] == "2.78x" and f["status"] == "ok"
    assert "preferred $21.0B" in f["basis"]
    assert any("other intangibles as of 2030-12-31" in w for w in f["warnings"])
    # an intangibles instant older than a year is not used
    s = normalize(_bank_facts({"FiniteLivedIntangibleAssetsNet":
                               {"units": {"USD": [_inst("2029-12-31", 1.3 * B)]}}}))
    assert s["balance"]["goodwill_intangibles"] == pytest.approx(52.7 * B)
    assert s["balance"]["goodwill_intangibles_missing"] == ["other intangibles"]


def test_preferred_not_found_warns_and_preferred_shares_without_a_value_withhold():
    from providers.sec_xbrl import normalize
    s = normalize(_bank_facts({"IntangibleAssetsNetExcludingGoodwill":
                               {"units": {"USD": [_inst("2031-09-30", 1.3 * B)]}}}))
    assert s["balance"]["preferred_found"] is False
    f = _view(statements=s, security=BANK, shares_out_m=16_000.0)["fields"]["p_tbv"]
    assert f["status"] == "ok" and f["value"] is not None
    assert ("preferred equity not found — treated as 0; tangible book may be overstated"
            in f["warnings"])
    s = normalize(_bank_facts({
        "IntangibleAssetsNetExcludingGoodwill": {"units": {"USD": [_inst("2031-09-30", 1.3 * B)]}},
        "PreferredStockSharesIssued": {"units": {"shares": [_inst("2031-09-30", 2_105_375)]}}}))
    assert s["balance"]["preferred_shares"] == 2_105_375
    f = _view(statements=s, security=BANK, shares_out_m=16_000.0)["fields"]["p_tbv"]
    assert f["status"] == "withheld" and f["display"] == "—"
    assert any("2,105,375 preferred shares but no preferred equity value" in w
               for w in f["warnings"])


def test_a_missing_intangibles_component_is_named():
    st = _stmts(balance={"total_debt": None, "cash": 1 * B, "cash_st": 1 * B, "equity": 10 * B,
                         "goodwill_intangibles": 2 * B, "preferred_equity": 0.0,
                         "preferred_found": True,
                         "goodwill_intangibles_missing": ["other intangibles"],
                         "as_of_date": "2031-09-30"})
    f = _view(statements=st, security=BANK, shares_out_m=100.0)["fields"]["p_tbv"]
    assert ("other intangibles not found — treated as 0; tangible book may be overstated"
            in f["warnings"])
    assert f["value"] == pytest.approx(50.0 * 100 * M / (8 * B))


# ── 2. ADRs and non-USD reporters ────────────────────────────────────────────

ADR = {"kind": "stock", "is_fund": False, "is_bank": False, "asset_sub_type": "ADR",
       "financial_currency": "TWD",
       "profile": {"implied_shares_outstanding": 5_186 * M, "financial_currency": "TWD"}}


def test_adr_withholds_market_cap_and_everything_priced_off_it():
    sec = dict(ADR, financial_currency="USD", profile={"implied_shares_outstanding": 5_186 * M})
    v = _view(statements=_stmts(), security=sec, shares_out_m=25_932.0)
    F = v["fields"]
    assert F["market_cap"]["status"] == "withheld" and F["market_cap"]["display"] == "—"
    assert any(w.startswith("ADR — the share counts available are not in ADR units")
               for w in F["market_cap"]["warnings"])
    for k in ("ev", "p_fcf"):
        assert F[k]["status"] == "withheld" and F[k]["value"] is None, k
        assert any(w.startswith("market cap withheld — ADR") for w in F[k]["warnings"]), k
    # net cash, FCF and margins are not priced off market cap
    assert F["net_cash"]["status"] == "ok" and F["fcf_ttm"]["status"] == "ok"
    assert F["gross_margin"]["status"] == "ok"
    assert v["raw"]["market_cap_withheld"].startswith("ADR")
    assert not any(c["check"] == "market_cap" and not c["ok"] for c in v["checks"])


def test_share_counts_far_apart_withhold_market_cap_but_a_listed_class_does_not():
    far = {"kind": "stock", "profile": {"implied_shares_outstanding": 5_000 * M}}
    F = _view(statements=_stmts(), security=far, shares_out_m=26_000.0)["fields"]
    assert F["market_cap"]["status"] == "withheld"
    assert any("differ by more than 50%" in w for w in F["market_cap"]["warnings"])
    # GOOGL-like: class A alone is about half the total — not a disagreement
    multi = {"kind": "stock", "profile": {"shares_outstanding": 5_800 * M,
                                          "implied_shares_outstanding": 12_100 * M}}
    F = _view(statements=_stmts(), security=multi, shares_out_m=12_100.0)["fields"]
    assert F["market_cap"]["status"] == "ok" and F["market_cap"]["value"] is not None


def test_non_usd_reporter_withholds_amounts_and_keeps_ratios():
    F = _view(statements=_stmts(), security=ADR, shares_out_m=25_932.0)["fields"]
    need = "reported in TWD — the filing's amounts are not US dollars; not shown"
    for k in ("fcf_ttm", "net_cash", "eps_ttm", "pe_ttm"):
        assert F[k]["status"] == "withheld" and F[k]["display"] == "—", k
        assert need in F[k]["warnings"], k
    assert F["ev"]["status"] == "withheld" and F["ev"]["value"] is None
    for k in ("gross_margin", "operating_margin", "net_margin"):
        assert F[k]["status"] == "ok" and F[k]["value"] is not None, k
    # a USD filer is untouched
    F = _view(statements=_stmts(), shares_out_m=100.0)["fields"]
    assert F["fcf_ttm"]["status"] == "ok" and F["net_cash"]["status"] == "ok"


def test_value_lens_for_an_adr_in_another_currency():
    s = _stmts(ttm={"revenue": 4.0e12, "fcf": 0.9e12})
    s["annual"] = [{"end": f"{2030 - i}-12-31", "revenue": (3.8 - 0.4 * i) * 1e12,
                    "net_income": 1e12, "fcf": 0.8e12, "acquisitions": -1e9} for i in range(4)]
    vl = value_lens.build_value_lens(None, s, "TWD")
    assert "TWD 3.80T" in vl["rev_cagr_3y"]["basis"]
    vl = value_lens.withhold_priced(vl, "ADR — share counts not in ADR units", "TWD")
    for k in ("p_fcf", "p_fcf_avg", "pe_avg", "ev", "fcf_ttm", "acquisitions_total"):
        assert vl[k]["value"] is None, k
    assert vl["rev_cagr_3y"]["value"] is not None
    assert vl["warnings"][0] == "price multiples withheld — ADR — share counts not in ADR units"
    assert vl["warnings"][1].startswith("reported in TWD")
    assert value_lens.withhold_priced({"x": 1}, None, "USD") == {"x": 1}


def test_yahoo_profile_carries_the_financial_currency():
    from providers.yfinance import security_profile_from_info
    p = security_profile_from_info({"quoteType": "EQUITY", "financialCurrency": "TWD",
                                    "currency": "USD"})
    assert p["financial_currency"] == "TWD" and p["currency"] == "USD"
    assert security_profile_from_info({"quoteType": "EQUITY"})["financial_currency"] is None


# ── 3. no refetch loops ──────────────────────────────────────────────────────

def test_a_failed_forced_refresh_is_not_retried_while_the_marker_stands(tmp_db, monkeypatch):
    from analysis import composer
    from providers.base import ProviderError
    calls = []

    def boom(symbol):
        calls.append(symbol)
        raise ProviderError("unavailable", "down")
    from cache import store
    store.put("statements:ZZLP", {"available": True, "periods": {"income": "2031-09-30"},
                                  "ttm": {}})          # old format: no `shares`
    for _ in range(3):
        d, meta = composer._one_statement_source("statements:ZZLP", boom, "ZZLP", False, None)
        assert "shares" not in d and meta["refreshed"] is False
    assert calls == ["ZZLP"]                          # one attempt, not three
    # once the marker has expired, one more attempt
    store.put("refresh_attempt:statements:ZZLP", {"outcome": "stale"})
    monkeypatch.setattr(composer, "REFRESH_RETRY_SECONDS", 0)
    composer._one_statement_source("statements:ZZLP", boom, "ZZLP", False, None)
    assert calls == ["ZZLP", "ZZLP"]


def test_filings_and_rating_refreshes_are_marked_too(tmp_db, monkeypatch):
    from analysis import composer
    from cache import store
    calls = []
    store.put("sec_filings:ZZMK:2031-10-01", {"available": True, "filings": []})
    store.put("rating_changes:ZZMK", {"available": True, "source": "Yahoo", "changes": []})

    def fail(*a):
        calls.append(a)
        from providers.base import ProviderError
        raise ProviderError("unavailable", "down")
    monkeypatch.setattr(composer.registry, "resolve", lambda cap: fail)
    for _ in range(3):
        assert "sic" not in composer._recent_filings("ZZMK", "2031-10-01", False)
        assert "no_feed" not in composer._rating_changes("ZZMK", False)
    assert len(calls) == 2


def test_exhibit_failure_is_cached_briefly(tmp_db, monkeypatch):
    from analysis import composer
    from providers.base import ProviderError
    calls = []

    def idx(url):
        calls.append(url)
        raise ProviderError("unavailable", "EDGAR filing index HTTP 503")
    monkeypatch.setattr(composer, "use_mock", lambda: False)
    monkeypatch.setattr(composer.registry, "resolve", lambda cap: idx)
    url = "https://www.sec.gov/Archives/edgar/data/1/000000000031000001/x.htm"
    assert composer._release_exhibit(url) is None
    assert composer._release_exhibit(url) is None
    assert len(calls) == 1
    from cache.store import _get_row
    assert _get_row(f"sec_exhibit:{url}")[0]["exhibit_url"] is None
    monkeypatch.setattr(composer, "EXHIBIT_FAIL_TTL", -1)       # past the short TTL
    composer._release_exhibit(url)
    assert len(calls) == 2


def test_security_profile_network_errors_back_off(tmp_db, monkeypatch):
    from analysis import composer
    from providers.base import ProviderError
    calls = []

    def prof(symbol):
        calls.append(symbol)
        raise ProviderError("unavailable", "yfinance profile: timeout")
    monkeypatch.setattr(composer.registry, "resolve", lambda cap: prof)
    for _ in range(3):
        assert composer._security_profile("ZZSP", False) is None
    assert calls == ["ZZSP"]
    # a cached good copy is served (not refetched) while the backoff stands
    from cache import store
    store.put("security_profile:ZZSP", {"available": True, "quote_type": "EQUITY"})
    assert composer._security_profile("ZZSP", False)["quote_type"] == "EQUITY"
    assert calls == ["ZZSP"]


# ── 4. the filing-form filter on the payload ─────────────────────────────────

def test_analysis_payload_names_the_sec_filing_forms(client):
    from providers import sec_xbrl
    a = client.get("/api/analysis/NVDA").json()
    assert "sec_filing_forms" in a
    if a["sec_filing_forms"] is not None:
        assert "8-K" in a["sec_filing_forms"] and "10-Q" in a["sec_filing_forms"]
        assert set(a["sec_filing_forms"]) <= set(sec_xbrl.FILING_FORMS)


# ── 5. fund NAV ──────────────────────────────────────────────────────────────

def _fund(**prof):
    return dict(FUND, profile={**FUND["profile"], **prof})


def test_nav_row_is_dated_and_not_compared_when_stale_or_undated():
    basis = {"price": 45.58, "price_kind": "close", "session_date": "2031-11-20",
             "prior_date": "2031-11-19", "prior_close": 48.13, "price_label": "Official close"}
    # undated (Yahoo), fetched after the session; NAV ≈ the prior close
    f = _view(basis=basis, security=_fund(nav=48.1545, as_of="2031-11-22"),
              statements=None)["fields"]["fund_nav"]
    assert f["display"] == "$48.15 (fetched 2031-11-22; NAV date not stated)"
    assert f["premium_discount_pct"] is None and "not compared" in f["basis"]
    assert any("does not date its NAV" in w for w in f["warnings"])
    assert any("likely the prior session's NAV" in w for w in f["warnings"])
    # fetched before the report session
    f = _view(basis=basis, security=_fund(nav=46.0, as_of="2031-11-18"),
              statements=None)["fields"]["fund_nav"]
    assert any("before the report session (2031-11-20)" in w for w in f["warnings"])
    # a dated NAV older than the session
    f = _view(basis=basis, security=_fund(nav=46.0, nav_date="2031-11-19"),
              statements=None)["fields"]["fund_nav"]
    assert f["display"] == "$46.00 (as of 2031-11-19)"
    assert any("older than the report session" in w for w in f["warnings"])
    assert f["premium_discount_pct"] is None
    # a NAV of the report session itself → premium/discount
    f = _view(basis=basis, security=_fund(nav=45.0, nav_date="2031-11-20"),
              statements=None)["fields"]["fund_nav"]
    assert f["premium_discount_pct"] == pytest.approx((45.58 / 45.0 - 1) * 100)
    assert "+1.29% vs the report price" in f["basis"] and not f["warnings"]


# ── 6. forward-yield exemption ───────────────────────────────────────────────

TQQQ = [("2023-12-20", 0.1115), ("2024-03-20", 0.1075), ("2024-06-26", 0.1415),
        ("2024-09-25", 0.115), ("2024-12-23", 0.1375), ("2025-03-26", 0.099),
        ("2025-06-25", 0.109), ("2025-09-24", 0.049), ("2025-12-24", 0.086),
        ("2026-03-25", 0.072), ("2026-06-24", 0.171), ("2026-09-23", 0.156)]


def test_variable_payouts_do_not_count_as_a_new_level():
    ev = [{"date": d, "amount": a} for d, a in TQQQ]
    assert report_view.sustained_level(ev, 4) is False
    fd = report_view.forward_distribution(ev)
    assert fd["ok"] is False and fd["why"].startswith("variable distributions")
    # a steady payer that raised twice in a row still counts (NVDA-like)
    nv = [{"date": f"2030-{m:02d}-05", "amount": 0.01} for m in (1, 4, 7, 10)] + \
         [{"date": "2031-01-05", "amount": 0.25}, {"date": "2031-04-05", "amount": 0.25}]
    assert report_view.sustained_level(nv, 4) is True
    assert report_view.forward_distribution(nv)["ok"] is True
    # the two latest payments disagree → not a level
    nv2 = nv[:-1] + [{"date": "2031-04-05", "amount": 0.40}]
    assert report_view.sustained_level(nv2, 4) is False
    # a cut repeated twice (both below the prior band) is a level too
    nv3 = nv[:-2] + [{"date": "2031-01-05", "amount": 0.005},
                     {"date": "2031-04-05", "amount": 0.0052}]
    assert report_view.sustained_level(nv3, 4) is True
    # the latest two inside the prior band are not a "new" level
    nv4 = nv[:-2] + [{"date": "2031-01-05", "amount": 0.011},
                     {"date": "2031-04-05", "amount": 0.0105}]
    assert report_view.sustained_level(nv4, 4) is False


def test_tqqq_forward_yield_is_withheld():
    from tests.test_data_fixes_1122 import NOW  # noqa: F401
    basis = {"price": 41.2, "price_kind": "close", "session_date": "2026-11-20",
             "prior_date": "2026-11-19", "price_label": "Official close"}
    import datetime as dt
    F = _view(basis=basis, security=FUND, statements=None,
              now=dt.datetime(2026, 11, 22, 12, 0),
              dividends=_div(TQQQ))["fields"]
    assert F["div_yield_forward"]["status"] == "withheld"
    assert F["div_yield_trailing"]["display"] == "1.18%"


# ── 7. banks ─────────────────────────────────────────────────────────────────

def test_bank_value_lens_blanks_cash_paid_for_acquisitions():
    s = _stmts()
    s["annual"] = [{"end": f"{2030 - i}-12-31", "revenue": 120 * M, "net_income": 30 * M,
                    "fcf": 25 * M, "acquisitions": -2 * B} for i in range(4)]
    vl = value_lens.build_value_lens(800 * B, s)
    assert vl["acquisitions_total"]["value"] is not None
    vl = value_lens.for_bank(vl)
    assert vl["acquisitions_total"]["value"] is None
    assert vl["acquisitions_total"]["label"] == "Cash paid for acquisitions"


def _quarters(rev_now: float, rev_year_ago: float) -> list[dict]:
    ends = ["2031-09-30", "2031-06-30", "2031-03-31", "2030-12-31", "2030-09-30"]
    revs = [rev_now, 1.0 * B, 1.0 * B, 1.0 * B, rev_year_ago]
    return [{"end": e, "revenue": r, "net_income": r * 0.2} for e, r in zip(ends, revs)]


def test_vendor_ttm_revenue_growth_withheld_for_banks_and_when_far_from_the_quarter():
    st = _stmts(quarters=_quarters(1.081 * B, 1.0 * B))
    F = _view(statements=st, vendor={"revenue_growth_ttm_pct": 108.98}, shares_out_m=100.0)["fields"]
    assert F["revenue_growth_q"]["display"] == "+8.1%"
    f = F["revenue_growth"]
    assert f["status"] == "withheld" and f["display"] == "—"
    assert any("more than 50 points apart" in w for w in f["warnings"])
    # close to the quarter → shown
    F = _view(statements=st, vendor={"revenue_growth_ttm_pct": 9.4}, shares_out_m=100.0)["fields"]
    assert F["revenue_growth"]["status"] == "ok" and F["revenue_growth"]["display"] == "9.4%"
    # a bank: withheld whatever the gap
    F = _view(statements=st, vendor={"revenue_growth_ttm_pct": 9.4}, security=BANK,
              shares_out_m=100.0)["fields"]
    assert F["revenue_growth"]["status"] == "withheld"
    assert any(w.startswith("bank — the vendor's revenue line") for w in F["revenue_growth"]["warnings"])


# ── 8. undated fiscal years ──────────────────────────────────────────────────

def test_fy_labels_without_end_dates():
    s = _stmts(ttm={"revenue": 4.1 * B})
    s["annual"] = [{"end": None, "revenue": (4.0 - 0.3 * i) * B} for i in range(4)]
    vl = value_lens.build_value_lens(50 * B, s)
    basis = vl["rev_cagr_3y"]["basis"]
    assert "FY −0" not in basis
    assert "latest FY (undated) ($4.0B)" in basis and "FY −3 (undated)" in basis


# ── 9. comments / dead code ──────────────────────────────────────────────────

def test_unused_last_report_date_is_gone():
    from analysis import composer
    assert not hasattr(composer, "_last_report_date")


def test_marker_helpers_tolerate_a_missing_cache_table(monkeypatch):
    from analysis import composer

    def broken(key):
        raise RuntimeError("no such table: cache")
    monkeypatch.setattr(composer, "_get_row", broken)
    assert composer._refresh_recently_tried("x") is False
    assert time.time() > 0
