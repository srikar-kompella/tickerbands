"""Backend data fixes, round 2 (2026-11-22). One test (or group) per fix:

 1. a fund whose every dividend event has a zero/missing amount no longer 500s
 2. the fund-name fallback no longer calls "Trust" / "Shares" companies funds;
    the bank fallbacks run only when the SIC code is unknown
 3. precedence fund > bank > new_listing > stock; `is_new_listing` on the
    payload whatever the kind, and the new-listing notes key off it
 4. a symbol Yahoo has no profile for is cached (unavailable), not refetched
 5. no tax line + net income above pretax income: "tax benefit or
    discontinued operations", no statutory-rate figure
 6. diluted share-count wording names share classes
 7. mock statements/filings carry `shares` / `sic` / `forms` (no refetch loop)
 8. recent_sessions covers every session in the news window (10..25)
 9. the SEC filing list carries Form 4 / 144 / 13D / 13G / NT / S-1 / fund
    forms, capped at 25 with the issuer's own reports kept first
"""
from __future__ import annotations

import datetime as dt

import pytest

from analysis import report_view, vol_bands
from tests import replay_fixtures as rf
from tests.test_data_fixes_1122 import BANK, FUND, M, _div, _stmts, _view, client  # noqa: F401


# ── 1. zero-amount distributions ─────────────────────────────────────────────

def test_fund_with_only_zero_or_missing_distribution_amounts_does_not_crash():
    v = _view(security=FUND, dividends=_div([("2031-08-01", 0), ("2031-09-01", None),
                                             ("2031-10-01", 0.0)]))
    F = v["fields"]
    assert F["fund_schedule"]["status"] == "withheld" and F["fund_schedule"]["display"] == "—"
    assert any("none with a positive amount" in w for w in F["fund_schedule"]["warnings"])
    assert "fund_distributions" not in F
    assert F["div_yield_forward"]["value"] is None
    assert any("no ex-date in the history has a positive amount" in w
               for w in F["div_yield_forward"]["warnings"])


def test_fund_report_with_zero_distribution_amounts_is_served(client, monkeypatch):
    from providers import mock
    monkeypatch.setattr(mock, "fetch_dividends", lambda s: {
        "available": True, "source": "mock", "as_of": "2031-11-22",
        "events": [{"date": "2026-09-01", "amount": 0.0}, {"date": "2026-10-01", "amount": None}]})
    assert client.get("/api/analysis/SPY").status_code == 200
    r = client.get("/api/analysis/SPY/report")
    assert r.status_code == 200 and "Distribution schedule" in r.text


# ── 2. security classification ───────────────────────────────────────────────

@pytest.mark.parametrize("name,kind", [
    ("Northern Trust Corp", "stock"),
    ("Vornado Realty Trust", "stock"),
    ("ZZ Holdings Ordinary Shares", "stock"),
    ("ZZ Group American Depositary Shares", "stock"),
    ("ZZ Income Fund Inc", "stock"),            # a company word vetoes the fund word
    ("ZZ Royalty Trust", "stock"),              # "Trust" alone is not a fund word
    ("ZZ Shares", "stock"),
    ("iShares ZZ Bond", "fund"),
    ("ZZ Covered Call ETF", "fund"),
    ("ZZ Short ETN", "fund"),
    ("ZZ Opportunities Fund", "fund"),
    ("", "stock"),                              # unknown → stock
])
def test_fund_name_fallback(name, kind):
    s = report_view.classify_security({"name": name, "asset_type": "EQUITY"}, 130, False)
    assert s["kind"] == kind


@pytest.mark.parametrize("sic,profile,bank_like,kind", [
    ("6022", None, False, "bank"),
    # a known non-bank SIC is the answer: the industry text and the XBRL
    # bank lines are only fallbacks for an unknown SIC
    ("6211", {"industry": "Banks - Diversified"}, False, "stock"),
    ("7372", None, True, "stock"),
    (None, {"industry": "Banks - Regional"}, False, "bank"),
    (None, None, True, "bank"),
    ("", None, True, "bank"),
])
def test_bank_fallbacks_only_without_a_sic(sic, profile, bank_like, kind):
    s = report_view.classify_security({"name": "ZZ Financial", "asset_type": "EQUITY"},
                                      130, True, profile, sic, bank_like)
    assert s["kind"] == kind


# ── 3. precedence + is_new_listing ───────────────────────────────────────────

@pytest.mark.parametrize("quote,n,sic,kind,new", [
    ({"asset_type": "EQUITY", "asset_sub_type": "ETF"}, 4, None, "fund", True),
    ({"asset_type": "EQUITY"}, 4, "6021", "bank", True),
    ({"asset_type": "EQUITY"}, 4, None, "new_listing", True),
    ({"asset_type": "EQUITY"}, 21, None, "stock", False),
    ({"asset_type": "EQUITY"}, None, None, "stock", False),
])
def test_precedence_contract(quote, n, sic, kind, new):
    s = report_view.classify_security(quote, n, True, None, sic)
    assert s["kind"] == kind and s["is_new_listing"] is new


def test_new_listing_notes_key_off_is_new_listing_not_kind():
    new_bank = dict(BANK, is_new_listing=True, sessions_listed=None, profile={})
    F = _view(statements=_stmts(), shares_out_m=13_000.0, security=new_bank)["fields"]
    assert any(w.startswith("newly listed — the share count used")
               for w in F["market_cap"]["warnings"])
    rc = {"available": True, "source": "Yahoo", "changes": [], "no_feed": True}
    f = _view(rating_changes=rc, security=new_bank)["fields"]["analyst_actions"]
    assert any("(listed days ago)" in w for w in f["warnings"])
    # the same bank, not new: no listing notes
    F = _view(statements=_stmts(), shares_out_m=13_000.0, security=dict(BANK, profile={}),
              rating_changes=rc)["fields"]
    assert not any("listed" in w for w in F["market_cap"]["warnings"])
    assert not any("listed days ago" in w for w in F["analyst_actions"]["warnings"])


def test_payload_new_fund_is_kind_fund_with_is_new_listing(tmp_db, monkeypatch):
    from analysis import composer
    p = rf.nvda_providers()
    hist = p["daily_history"]("NVDA")[-3:]
    q = dict(p["quotes"]("NVDA"), asset_sub_type="ETF", name="ZZ New Income ETF")
    p["quotes"] = lambda s: q
    p["daily_history"] = lambda s, *a: rf.spy_bars(rf.NVDA_FRI) if s == "SPY" else hist
    rf.install(monkeypatch, p)
    a = composer.analyze("NVDA", now=rf.NVDA_NOW)
    assert a["security_kind"] == "fund" and a["is_new_listing"] is True
    assert a["security"]["is_new_listing"] is True and a["security"]["sessions_listed"] == 3


def test_payload_is_new_listing_false_for_a_seasoned_stock(client):
    a = client.get("/api/analysis/ZZAA").json()
    assert a["security_kind"] == "stock" and a["is_new_listing"] is False


# ── 4. missing Yahoo profile is cached ───────────────────────────────────────

def test_missing_yahoo_profile_is_returned_unavailable_and_cached(tmp_db, monkeypatch):
    from analysis import composer
    from providers import yfinance as yfp
    calls = []

    class T:
        def __init__(self, sym):
            calls.append(sym)
            self.info = {"trailingPegRatio": None}          # no quoteType

    monkeypatch.setattr(yfp, "_yf", lambda: type("YF", (), {"Ticker": T}))
    out = yfp.fetch_security_profile("ZZQQ")
    assert out["available"] is False and out["reason"] == "no_data"
    monkeypatch.setattr(composer.registry, "resolve",
                        lambda cap: yfp.fetch_security_profile if cap == "security_profile" else None)
    assert composer._security_profile("ZZQQ", False)["available"] is False
    assert composer._security_profile("ZZQQ", False)["available"] is False
    assert len(calls) == 2                       # the direct call + one cached fetch
    # an unavailable profile classifies as nothing in particular
    s = report_view.classify_security({"name": "ZZ Thing", "asset_type": "EQUITY"}, 130,
                                      True, out)
    assert s["kind"] == "stock"


# ── 5. tax benefit without a tax line ────────────────────────────────────────

def test_no_tax_line_names_both_explanations_and_no_statutory_figure():
    F = _view(statements=_stmts(ttm={"tax": None}), shares_out_m=150.0)["fields"]
    for key in ("eps_ttm", "pe_ttm", "net_margin"):
        w = [x for x in F[key]["warnings"] if "exceeds pretax income by" in x]
        assert w, key
        assert "(a tax benefit or discontinued operations)" in w[0]
        assert "Taxed at" not in w[0] and "would be about" not in w[0]
    # with a tax line the benefit is named as such, with the 21% figure
    F = _view(statements=_stmts(), shares_out_m=150.0)["fields"]
    assert any("income-tax benefit" in x and "Taxed at 21%" in x
               for x in F["net_margin"]["warnings"])


# ── 6. diluted share count wording ───────────────────────────────────────────

def test_diluted_count_above_outstanding_names_share_classes():
    s = _stmts(shares={"diluted": 15_000 * M, "period": "2031-09-30", "span": "quarter"})
    w = [x for x in _view(statements=s, shares_out_m=13_000.0)["fields"]["market_cap"]["warnings"]
         if x.startswith("diluted share count")]
    assert w and "or share classes not in the count used" in w[0]


# ── 7. mock mode does not refetch every request ──────────────────────────────

def test_mock_statements_and_filings_are_fetched_once(client, monkeypatch):
    from providers import mock
    n = {"st": 0, "fil": 0}
    st, fil = mock.fetch_statements, mock.fetch_recent_filings

    def st2(s):
        n["st"] += 1
        return st(s)

    def fil2(s, since):
        n["fil"] += 1
        return fil(s, since)
    monkeypatch.setattr(mock, "fetch_statements", st2)
    monkeypatch.setattr(mock, "fetch_recent_filings", fil2)
    for _ in range(3):
        assert client.get("/api/analysis/ZZAB").status_code == 200
    assert n == {"st": 1, "fil": 1}
    assert "shares" in st("ZZAB") and "sic" in fil("ZZAB", "2020-01-01")


# ── 8. recent sessions cover the news window ─────────────────────────────────

def _bars(n, last=dt.date(2026, 11, 20)):
    out, d = [], last
    while len(out) < n:
        if d.weekday() < 5:
            out.append({"date": d.isoformat(), "close": 100.0 + len(out) % 7})
        d -= dt.timedelta(days=1)
    return list(reversed(out))


def test_sessions_to_cover():
    bars = _bars(60)
    assert vol_bands.sessions_to_cover(bars, bars[-5]["date"]) == 10      # floor
    assert vol_bands.sessions_to_cover(bars, bars[-18]["date"]) == 18
    assert vol_bands.sessions_to_cover(bars, bars[-40]["date"]) == 25     # cap
    assert vol_bands.sessions_to_cover(bars, None) == 10
    closes = [b["close"] for b in bars]
    rs = vol_bands.recent_sessions(bars, closes, None, None, n=18)
    assert len(rs) == 18 and rs[0]["date"] == bars[-18]["date"]


def test_payload_recent_sessions_reach_back_to_the_news_window(tmp_db, monkeypatch):
    from analysis import composer
    real = composer.news_relevance.news_window

    def wide(last_note, last_session, today):
        out = real(last_note, last_session, today)
        return {**out, "from": "2026-10-23"}
    monkeypatch.setattr(composer.news_relevance, "news_window", wide)
    a = _analyze_nvda(monkeypatch)
    rs = a["recent_sessions"]
    assert rs[0]["date"] <= "2026-10-23" or rs[0]["date"] == "2026-10-23"
    assert {x["date"] for x in rs} >= {"2026-10-23", "2026-11-02", "2026-11-20"}
    assert all(x["spy_change_pct"] is not None for x in rs)
    assert len(rs) == len([d for d in (x["date"] for x in rs) if d >= "2026-10-23"])


def _analyze_nvda(monkeypatch, **over):
    from analysis import composer
    p = rf.nvda_providers()
    p.update(over)
    rf.install(monkeypatch, p)
    return composer.analyze("NVDA", now=rf.NVDA_NOW)


# ── 9. wider SEC filing list ─────────────────────────────────────────────────

def _feed(rows):
    forms, dates, accs, items, docs = [], [], [], [], []
    for i, (form, date, it) in enumerate(rows):
        forms.append(form); dates.append(date); items.append(it)
        accs.append(f"0000000000-31-{i:06d}"); docs.append(f"doc{i}.htm")
    return {"sic": "7372", "sicDescription": "Services-Prepackaged Software",
            "filings": {"recent": {"form": forms, "filingDate": dates,
                                   "accessionNumber": accs, "items": items,
                                   "primaryDocument": docs}}}


def test_filing_list_carries_the_new_forms_and_caps_at_25(monkeypatch):
    from providers import edgar, sec_xbrl
    rows = ([("4", f"2031-11-{20 - i // 3:02d}", "") for i in range(30)]
            + [("144", "2031-11-10", ""), ("NT 10-Q", "2031-11-09", ""),
               ("SC 13G/A", "2031-11-08", ""), ("SCHEDULE 13D", "2031-11-07", ""),
               ("S-8", "2031-11-06", ""), ("497", "2031-11-05", ""),
               ("8-K", "2031-11-04", "2.02,9.01"), ("10-Q", "2031-11-04", ""),
               ("10-K/A", "2031-11-03", ""), ("424B4", "2031-11-02", ""),
               ("8-K", "2031-09-01", "8.01")])            # before `since`
    monkeypatch.setattr(edgar, "_cik_for", lambda s: "0000012345")
    monkeypatch.setattr(edgar, "_get_json", lambda url: _feed(rows))
    out = sec_xbrl.fetch_recent_filings("ZZAA", "2031-10-01")
    fil = out["filings"]
    forms = [f["form"] for f in fil]
    assert len(fil) == 25 and out["omitted"] == 39 - 25
    assert "S-8" not in forms and "8-K" in forms and forms.count("8-K") == 1
    # the issuer's own reports are all kept, ahead of the Form 4 burst
    for f in ("NT 10-Q", "10-Q", "10-K/A", "424B4", "8-K"):
        assert f in forms
    assert [f["date"] for f in fil] == sorted((f["date"] for f in fil), reverse=True)
    assert "forms" in out and "N-PORT-P" in out["forms"]
    assert sec_xbrl.describe_form("4") == ("insider trade (Form 4)", False)
    assert sec_xbrl.describe_form("144")[0] == "planned insider sale (Form 144)"
    desc, red = sec_xbrl.describe_form("NT 10-Q")
    assert "late-filing notice" in desc and red is True
    assert sec_xbrl.describe_form("NT 10-K")[1] is True
    assert sec_xbrl.describe_form("10-Q") == ("10-Q", False)
    assert sec_xbrl.form_group("N-CSRS") == "fund" and sec_xbrl.form_group("4") == "insider"


def test_regulatory_row_lists_company_filings_first_and_counts_routine_forms():
    fil = ([{"form": "4", "date": "2031-11-20", "desc": "insider trade (Form 4)",
             "group": "insider", "red_flag": False}] * 7
           + [{"form": "144", "date": "2031-11-19", "desc": "planned insider sale (Form 144)",
               "group": "insider", "red_flag": False},
              {"form": "NT 10-Q", "date": "2031-11-18", "desc": "late-filing notice — x",
               "group": "company", "red_flag": True},
              {"form": "8-K", "date": "2031-11-05", "desc": "2.02 Results", "group": "company",
               "red_flag": False}])
    f = _view(filings=fil, news_window={"from": "2031-11-01", "to": "2031-11-22"})["fields"]["regulatory"]
    d = f["display"]
    assert d.index("NT 10-Q") < d.index("8-K") < d.index("Form 4 ×7")
    assert "Form 144 ×1" in d
    assert any("NT 10-Q" in w or "late-filing" in w for w in f["warnings"])


def test_move_cause_ignores_insider_filings_and_release_detection_still_works(tmp_db, monkeypatch):
    feed = {"available": True, "since": "2026-11-01", "cik": "0000012345", "sic": "3674",
            "sic_description": None, "forms": ["4", "8-K"],
            "filings": [{"form": "4", "date": "2026-11-20", "items": "", "group": "insider",
                         "url": "https://www.sec.gov/x/f4.xml"},
                        {"form": "8-K", "date": "2026-11-19", "items": "2.02,9.01",
                         "url": "https://www.sec.gov/x/8k.htm"}]}
    a = _analyze_nvda(monkeypatch, sec_filings=lambda s, since: feed)
    assert [f["form"] for f in a["move_cause"]["filings"]] == ["8-K"]
    assert a["statements_meta"]["release"]["form"] == "8-K"
    f4 = next(f for f in a["sec_filings"] if f["form"] == "4")
    assert f4["desc"] == "insider trade (Form 4)" and f4["group"] == "insider"
