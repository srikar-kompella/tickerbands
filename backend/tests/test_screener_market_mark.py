"""The Watchlist (screener) judges notes with the same market-wide rule as the
report: a ≥2σ session explained by the market (net of beta × SPY under one
daily σ) is marked and needs no news cause."""
import datetime as dt

from analysis import screener, vol_bands


def _bars(closes, start=dt.date(2026, 6, 1)):
    out, d = [], start
    for c in closes:
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        out.append({"date": d.isoformat(), "open": c, "high": c, "low": c, "close": c,
                    "volume": 1000})
        d += dt.timedelta(days=1)
    return out


def test_market_wide_session_is_marked_in_the_screener(monkeypatch):
    # a calm stock that tracks SPY 1:1, then a −6% day when SPY also fell 6%
    spy_c, stk_c = [400.0], [100.0]
    for i in range(80):
        r = 0.004 if i % 2 else -0.004
        spy_c.append(spy_c[-1] * (1 + r))
        stk_c.append(stk_c[-1] * (1 + r * 1.0 + (0.0005 if i % 3 else -0.0005)))
    spy_c.append(spy_c[-1] * 0.94)
    stk_c.append(stk_c[-1] * 0.94)
    bars, spy = _bars(stk_c), _bars(spy_c)
    monkeypatch.setattr(screener, "_get_row", lambda key: (spy, 0) if key == "history:SPY" else None)
    from analysis import research_quality
    sessions = research_quality.big_move_sessions(bars, [])
    assert sessions and sessions[-1]["session_date"] == bars[-1]["date"]
    basis = {"session_date": bars[-1]["date"], "price_kind": "official_close"}
    marked = screener._market_marked(bars, [], basis, sessions)
    last = [s for s in marked if s["session_date"] == bars[-1]["date"]][0]
    assert last.get("market_net_pct") is not None and abs(last["market_net_pct"]) < 1.0


def test_without_cached_spy_the_plain_rule_applies(monkeypatch):
    monkeypatch.setattr(screener, "_get_row", lambda key: None)
    bars = _bars([100 + (i % 2) for i in range(40)])
    s = [{"session_date": bars[-1]["date"], "z": -3.0, "move_pct": -6.0}]
    assert screener._market_marked(bars, [], {"session_date": bars[-1]["date"]}, s) == s
