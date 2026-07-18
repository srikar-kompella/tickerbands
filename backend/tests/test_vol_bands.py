"""
vol_bands tests — the math with evidence behind it gets pinned values.
Hand-computed expectations, no reimplementation-testing-itself circularity.
"""
import math

import pytest

from analysis.vol_bands import (
    band,
    beta_and_correlation,
    completed_bars,
    compute_signals,
    move_z_score,
    realized_vol,
)
from config import BAND_Z, TRADING_DAYS


def bars_from(closes, start="2026-01-01"):
    import datetime as dt
    d0 = dt.date.fromisoformat(start)
    return [{"date": (d0 + dt.timedelta(days=i)).isoformat(),
             "open": c, "high": c, "low": c, "close": c, "volume": 1000 + i}
            for i, c in enumerate(closes)]


# ─── completed_bars (the F1 guard) ─────────────────────────────────────────────

def test_completed_bars_drops_today_and_future():
    import datetime as dt
    today = dt.date(2026, 7, 12)
    hist = [{"date": "2026-07-10"}, {"date": "2026-07-11"},
            {"date": "2026-07-12"}, {"date": "2026-07-13"}]
    out = completed_bars(hist, today)
    assert [b["date"] for b in out] == ["2026-07-10", "2026-07-11"]


def test_completed_bars_keeps_all_when_history_is_old():
    import datetime as dt
    hist = [{"date": "2026-01-05"}, {"date": "2026-01-06"}]
    assert len(completed_bars(hist, dt.date(2026, 7, 12))) == 2


# ─── realized_vol ──────────────────────────────────────────────────────────────

def test_realized_vol_constant_prices_is_zero():
    assert realized_vol([100.0] * 30, 20) == 0.0


def test_realized_vol_known_alternating_series():
    # ×1.01 / ×0.99 alternating → returns exactly ±0.01, mean 0 over any even
    # window → sample std = 0.01·√(n/(n−1)) with n=20.
    closes = [100.0]
    for i in range(30):
        closes.append(closes[-1] * (1.01 if i % 2 == 0 else 0.99))
    rv = realized_vol(closes, 20)
    expected_daily = 0.01 * math.sqrt(20 / 19)
    assert rv == pytest.approx(expected_daily * math.sqrt(252) * 100, rel=1e-3)


def test_realized_vol_insufficient_history_raises():
    with pytest.raises(ValueError):
        realized_vol([100.0] * 20, 20)  # needs window+1


# ─── compute_signals ───────────────────────────────────────────────────────────

def test_signals_flat_series_all_zero_distances():
    s = compute_signals([100.0] * 60, [1000] * 60)
    assert s["prev_close"] == 100.0
    assert s["sma20_dist"] == 0.0 and s["sma50_dist"] == 0.0
    assert s["return_1d"] == 0.0 and s["rv_20d"] == 0.0
    assert s["rv_60d"] is None  # needs 61 closes


def test_signals_rv60_present_with_enough_history():
    assert compute_signals([100.0] * 61, [1000] * 61)["rv_60d"] == 0.0


def test_signals_uptrend_positive_distances():
    closes = [100.0 * 1.01 ** i for i in range(60)]
    s = compute_signals(closes, [1000] * 60)
    assert s["sma20_dist"] > 0 and s["sma50_dist"] > s["sma20_dist"] * 0  # both positive
    assert s["return_5d"] == pytest.approx((1.01 ** 5 - 1) * 100, rel=1e-6)


def test_signals_insufficient_history_raises():
    with pytest.raises(ValueError):
        compute_signals([100.0] * 20, [1000] * 20)


def test_signals_volume_z_flags_spike():
    volumes = [1000] * 59 + [5000]
    s = compute_signals([100.0] * 60, volumes)
    assert s["volume_z"] > 3


# ─── band ──────────────────────────────────────────────────────────────────────

def test_band_hand_computed():
    # rv=32% annualized → daily σ = 32/√252 = 2.0159% → half-width 2.5804%
    b = band(100.0, 32.0)
    assert b["daily_sigma_pct"] == pytest.approx(32.0 / math.sqrt(TRADING_DAYS), abs=1e-3)
    assert b["half_width_pct"] == pytest.approx(BAND_Z * 32.0 / math.sqrt(TRADING_DAYS), abs=1e-3)
    assert b["low"] == pytest.approx(100 - b["half_width_pct"], abs=0.01)
    assert b["high"] == pytest.approx(100 + b["half_width_pct"], abs=0.01)
    assert b["coverage_target"] == 0.80


def test_band_zero_vol_collapses_to_price():
    b = band(250.0, 0.0)
    assert b["low"] == b["high"] == 250.0


def test_band_scales_with_price():
    # low/high are rounded to cents for display, so compare within rounding
    # tolerance (4 bounds × $0.005 each), not exact ratios.
    b1, b2 = band(100.0, 25.0), band(400.0, 25.0)
    assert (b2["high"] - b2["low"]) == pytest.approx(
        4 * (b1["high"] - b1["low"]), abs=0.05)


# ─── move_z_score ──────────────────────────────────────────────────────────────

def test_move_z_zero_move():
    m = move_z_score(100.0, 100.0, 32.0)
    assert m["z"] == 0.0 and m["outside_band"] is False and m["abs_percentile"] == 0.0


def test_move_z_exactly_at_band_edge_is_outside():
    sigma_daily = 32.0 / math.sqrt(TRADING_DAYS)
    price = 100.0 * (1 + BAND_Z * sigma_daily / 100)
    m = move_z_score(100.0, price, 32.0)
    assert m["z"] == pytest.approx(BAND_Z, abs=0.01)
    assert m["outside_band"] is True  # >= is 'left the 80% range'


def test_move_z_percentile_monotone():
    small = move_z_score(100.0, 100.5, 32.0)["abs_percentile"]
    large = move_z_score(100.0, 104.0, 32.0)["abs_percentile"]
    assert 0 < small < large < 100


def test_move_z_zero_vol_degrades_gracefully():
    m = move_z_score(100.0, 101.0, 0.0)
    assert m["z"] == 0.0  # no σ → no z, not a crash


# ─── beta / correlation ────────────────────────────────────────────────────────

def test_beta_identical_series_is_one():
    closes = [100.0 * 1.005 ** i * (1 + 0.01 * math.sin(i)) for i in range(80)]
    r = beta_and_correlation(closes, closes)
    assert r["beta"] == pytest.approx(1.0, abs=0.01)
    assert r["correlation"] == pytest.approx(1.0, abs=0.01)


def test_beta_2x_leveraged_series():
    import random
    rng = random.Random(7)
    spy = [100.0]
    for _ in range(80):
        spy.append(spy[-1] * (1 + rng.gauss(0, 0.01)))
    lev = [100.0]
    for i in range(1, len(spy)):
        lev.append(lev[-1] * (1 + 2 * (spy[i] / spy[i - 1] - 1)))
    r = beta_and_correlation(lev, spy)
    assert r["beta"] == pytest.approx(2.0, abs=0.05)
    assert r["correlation"] == pytest.approx(1.0, abs=0.01)


def test_beta_inverse_series_negative():
    import random
    rng = random.Random(9)
    spy = [100.0]
    for _ in range(80):
        spy.append(spy[-1] * (1 + rng.gauss(0, 0.01)))
    inv = [100.0]
    for i in range(1, len(spy)):
        inv.append(inv[-1] * (1 - (spy[i] / spy[i - 1] - 1)))
    r = beta_and_correlation(inv, spy)
    assert r["beta"] == pytest.approx(-1.0, abs=0.05)
    assert r["correlation"] == pytest.approx(-1.0, abs=0.01)


def test_beta_insufficient_overlap_returns_none():
    r = beta_and_correlation([100.0] * 30, [100.0] * 30)
    assert r["beta"] is None and r["correlation"] is None


def test_beta_flat_series_returns_none_not_div_zero():
    r = beta_and_correlation([100.0] * 80, [100.0] * 80)
    assert r["beta"] is None
