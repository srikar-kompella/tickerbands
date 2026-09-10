"""
BUG A + BUG B acceptance tests (WORKLOG handoff spec, 2026-09-10).

BUG A: panel P/E = price ÷ TTM EPS; yield = TTM distributions ÷ price; every
metric carries source + as-of; >50% source divergence or missing data → None
("—" in UI) + warning — never a silently wrong number.
BUG B: on ex-div dates the day-move, σ and band anchor are dividend-adjusted
(INSW 9/10: $5.05 ex-div must NOT render "−3.84% / −1.89σ · outside band").
"""
import datetime as dt

from analysis import fundamentals_ttm as ft
from analysis import vol_bands as vb


# ── BUG B: synthetic ex-div replay (INSW 2026-09-10 numbers) ─────────────────

def _insw_bars():
    """~30 completed bars ending 2026-09-09 at $104.62, alternating ±2.8%
    daily moves (rv_20d ≈ 44% — tanker-typical)."""
    bars, px = [], 104.62
    seq = [px]
    for _ in range(30):
        seq.append(seq[-1] / (1.028 if len(seq) % 2 else 0.972))
    seq = list(reversed(seq))          # ends at 104.62
    day = dt.date(2026, 9, 9)
    for i, close in enumerate(seq):
        bars.append({"date": (day - dt.timedelta(days=len(seq) - 1 - i)).isoformat(),
                     "close": round(close, 4), "volume": 100_000})
    assert abs(bars[-1]["close"] - 104.62) < 1e-6
    return bars


def test_synthetic_exdiv_day_move_band_and_sigma():
    bars = _insw_bars()
    events = [{"date": "2026-09-10", "amount": 5.05}]   # ex-div TODAY (not in bars)
    current = 100.60                                     # raw −3.84% vs 104.62

    adj = vb.adjust_closes_for_dividends(bars, events)   # today's event is outside
    assert adj == [b["close"] for b in bars]             # the window → no-op here

    rv = vb.realized_vol(adj, 20)
    anchor = bars[-1]["close"] - 5.05                    # 99.57 — the BUG B fix
    move = vb.move_z_score(anchor, current, rv)
    band = vb.band(anchor, rv)

    assert 0.8 <= move["move_pct"] <= 1.3, move          # ~+1%, not −3.84%
    assert 0.25 <= move["z"] <= 0.45, move               # spec: 0.3–0.4σ
    assert not move["outside_band"]
    assert band["low"] <= current <= band["high"]        # inside the band

    # and the unadjusted computation WAS the reported false shock
    raw_move = vb.move_z_score(bars[-1]["close"], current, rv)
    assert raw_move["move_pct"] < -3.5
    assert raw_move["z"] < -1.0


def test_adjust_closes_backadjusts_before_past_exdate():
    bars = [{"date": "2026-09-01", "close": 100.0},
            {"date": "2026-09-02", "close": 101.0},
            {"date": "2026-09-03", "close": 96.5}]       # ex-div 9/3, $5
    adj = vb.adjust_closes_for_dividends(bars, [{"date": "2026-09-03", "amount": 5.0}])
    f = (101.0 - 5.0) / 101.0
    assert abs(adj[0] - 100.0 * f) < 1e-9
    assert abs(adj[1] - 101.0 * f) < 1e-9
    assert adj[2] == 96.5                                # on/after ex-date untouched
    # like-for-like return across the ex-date is now ~+0.5%, not −4.5%
    assert (adj[2] / adj[1] - 1) * 100 > 0

def test_adjust_closes_noop_without_events():
    bars = [{"date": "2026-09-01", "close": 50.0}, {"date": "2026-09-02", "close": 51.0}]
    assert vb.adjust_closes_for_dividends(bars, []) == [50.0, 51.0]


def test_composer_exdiv_today_flag_and_anchor(tmp_db, mock_mode, monkeypatch):
    from analysis import composer
    from providers import mock as mockmod
    today = dt.date.today().isoformat()

    def fake_divs(symbol):
        return {"available": True, "source": "mock", "as_of": today,
                "events": [{"date": today, "amount": 1.0}]}
    monkeypatch.setattr(mockmod, "fetch_dividends", fake_divs)

    a = composer.analyze("NVDA")
    assert a["ex_div"]["today"] is True and a["ex_div"]["amount"] == 1.0
    assert a["ex_div"]["pending"] is False
    # band + move anchored on prev_close MINUS the distribution
    assert abs(a["band"]["prev_close"] - (a["signals"]["prev_close"] - 1.0)) < 1e-6


def test_composer_no_exdiv_normal_day(tmp_db, mock_mode):
    from analysis import composer
    a = composer.analyze("MSFT")
    assert a["ex_div"]["today"] is False
    assert abs(a["band"]["prev_close"] - a["signals"]["prev_close"]) < 1e-6
    assert "fundamentals_ttm" in a and "yield_ttm" in a["fundamentals_ttm"]


# ── BUG A: TTM math + divergence guards (ECO/AMLP failure modes) ─────────────

def _eco_vendor():
    return {"pe_ttm": 40.0, "eps_ttm": 10.5, "dividend_yield_pct": 0.8}

def _eco_divs(price=66.8):
    # four variable payments summing to ~13% of price over the trailing year
    today = dt.date.today()
    amts = [3.0, 2.4, 1.9, 1.4]  # = 8.7 ≈ 13.0% of 66.8
    return {"available": True, "source": "yfinance", "as_of": today.isoformat(),
            "events": [{"date": (today - dt.timedelta(days=60 + 90 * i)).isoformat(),
                        "amount": a} for i, a in enumerate(amts)]}


def test_eco_yield_computed_from_distributions_with_vendor_warning():
    out = ft.build_ttm_view(66.8, _eco_vendor(), _eco_divs(),
                            {"eps_ttm": 10.44, "source": "yfinance"})
    y = out["yield_ttm"]
    assert 12.5 <= y["value"] <= 13.5          # ~13% TTM — NOT vendor's 0.8%
    assert "0.8" in (y["warning"] or "")       # divergence named
    assert "distributions" in y["source"]
    assert y["as_of"]


def test_eco_pe_price_over_ttm_eps_with_vendor_warning():
    out = ft.build_ttm_view(66.8, _eco_vendor(), _eco_divs(),
                            {"eps_ttm": 10.44, "source": "yfinance"})
    pe = out["pe_ttm"]
    assert 6.0 <= pe["value"] <= 6.8           # ~6.4x — NOT vendor's 40x
    assert "40" in (pe["warning"] or "")


def test_eps_sources_conflict_withholds_pe():
    out = ft.build_ttm_view(66.8, {"pe_ttm": 40.0, "eps_ttm": 1.6},
                            _eco_divs(), {"eps_ttm": 10.44, "source": "yfinance"})
    pe = out["pe_ttm"]
    assert pe["value"] is None and "disagree" in pe["warning"]


def test_missing_dividend_history_never_shows_vendor_yield():
    out = ft.build_ttm_view(66.8, _eco_vendor(), None, None)
    y = out["yield_ttm"]
    assert y["value"] is None
    assert "unverified" in (y["warning"] or "") or "unavailable" in (y["warning"] or "")


def test_nonpayer_shows_zero_yield_no_warning():
    divs = {"available": True, "source": "yfinance",
            "as_of": dt.date.today().isoformat(), "events": []}
    out = ft.build_ttm_view(100.0, {}, divs, None)
    assert out["yield_ttm"]["value"] == 0.0
    assert out["yield_ttm"]["warning"] is None


def test_negative_eps_pe_undefined():
    out = ft.build_ttm_view(20.0, {}, None, {"eps_ttm": -2.0, "source": "yfinance"})
    assert out["pe_ttm"]["value"] is None
    assert "negative" in out["pe_ttm"]["warning"]


def test_ttm_sum_window():
    today = dt.date(2026, 9, 10)
    events = [{"date": "2025-09-05", "amount": 9.9},   # outside 365d
              {"date": "2025-10-01", "amount": 1.0},
              {"date": "2026-06-01", "amount": 2.0}]
    assert ft.ttm_sum(events, today) == 3.0


def test_composer_exdiv_pending_when_amount_unpublished(tmp_db, mock_mode, monkeypatch):
    """INSW 9/10 reality: the feed knows TODAY is an ex-date but the amount
    isn't recorded yet → flag pending, warn, and adjust NOTHING (never
    fabricate a number)."""
    from analysis import composer
    from providers import mock as mockmod
    today = dt.date.today().isoformat()

    def fake_divs(symbol):
        return {"available": True, "source": "mock", "as_of": today,
                "events": [], "next_ex_date": today}
    monkeypatch.setattr(mockmod, "fetch_dividends", fake_divs)

    a = composer.analyze("AMD")
    assert a["ex_div"]["today"] is True
    assert a["ex_div"]["amount"] is None
    assert a["ex_div"]["pending"] is True
    # no invented adjustment: band anchored on the raw prev close
    assert abs(a["band"]["prev_close"] - a["signals"]["prev_close"]) < 1e-6
