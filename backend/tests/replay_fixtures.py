"""
tests/replay_fixtures.py — replays of the reports that showed wrong numbers.

Market values come from a fact-check (2026-11-21) of public data for:
  * NVDA, generated Sat 2026-11-21 11:25 ET (Q3 FY27 press release on EDGAR,
    market data, news)
  * GOOGL, Thu 2026-10-29 (morning after the Q3 release; broker day P/L
    larger than the position)
  * VST,  Tue 2026-11-04 after the close (−14.69% day shown as −1.01%)

Each scenario is a set of provider callables keyed by capability, installed
by monkeypatching providers.registry.resolve — the composer, report and
prompt run unmodified on top of them.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from analysis import market_clock as mc

NY = ZoneInfo("America/New_York")


def ny(*args) -> dt.datetime:
    return dt.datetime(*args, tzinfo=NY)


def ms(t: dt.datetime) -> int:
    return int(t.timestamp() * 1000)


def bars_ending(last_date: dt.date, closes_tail: list[float], n: int = 140,
                daily: float = 0.046, extra_today: dict | None = None) -> list[dict]:
    """n trading-day bars ending `last_date`; the final len(closes_tail)
    closes are exact, earlier ones alternate ±`daily` (so 20-day σ ≈ daily)."""
    dates = [last_date]
    while len(dates) < n:
        dates.append(mc.previous_trading_day(dates[-1]))
    dates.reverse()
    closes = list(closes_tail)
    while len(closes) < n:
        r = daily if len(closes) % 2 else -daily
        closes.insert(0, closes[0] / (1 + r))
    out = [{"date": d.isoformat(), "open": c, "high": c * 1.01, "low": c * 0.99,
            "close": round(c, 4), "volume": 30_000_000} for d, c in zip(dates, closes)]
    if extra_today:
        out.append(extra_today)
    return out


def spy_bars(last_date: dt.date, n: int = 140) -> list[dict]:
    return bars_ending(last_date, [600.0], n=n, daily=0.009)


# ─── NVDA, Sat 2026-11-21 11:25 ET ────────────────────────────────────────────

NVDA_NOW = ny(2026, 11, 21, 11, 25)
NVDA_FRI = dt.date(2026, 11, 20)
NVDA_SHARES_M = 24_100.0                     # 24.10B shares


def _nvda_quarters(fresh: bool) -> list[dict]:
    B = 1e9
    q3 = {"end": "2026-10-25", "revenue": 115.8 * B, "gross_profit": 81.1 * B,
          "operating_income": 75.0 * B, "pretax_income": 82.0 * B,
          "net_income": 65.3 * B, "eps_diluted": 2.70, "fcf": 43.83 * B}
    q2 = {"end": "2026-07-26", "revenue": 92.0 * B, "gross_profit": 64.4 * B,
          "operating_income": 62.0 * B, "pretax_income": 70.0 * B,
          "net_income": 64.0 * B, "eps_diluted": 2.63, "fcf": 21.40 * B}
    q1 = {"end": "2026-04-26", "revenue": 86.0 * B, "gross_profit": 60.2 * B,
          "operating_income": 60.0 * B, "pretax_income": 66.0 * B,
          "net_income": 58.0 * B, "eps_diluted": 2.24, "fcf": 48.59 * B}
    q4 = {"end": "2026-01-25", "revenue": 86.2 * B, "gross_profit": 60.3 * B,
          "operating_income": 58.0 * B, "pretax_income": 62.0 * B,
          "net_income": 50.2 * B, "eps_diluted": 1.74, "fcf": 34.90 * B}
    q3fy26 = {"end": "2025-10-26", "revenue": 70.0 * B, "gross_profit": 49.0 * B,
              "operating_income": 45.0 * B, "pretax_income": 47.0 * B,
              "net_income": 40.0 * B, "eps_diluted": 1.62, "fcf": 22.11 * B}
    return [q3, q2, q1, q4] if fresh else [q2, q1, q4, q3fy26]


def nvda_statements(fresh: bool = True) -> dict:
    qs = _nvda_quarters(fresh)
    keys = ("revenue", "gross_profit", "operating_income", "pretax_income",
            "net_income", "fcf")
    ttm = {k: sum(q[k] for q in qs) for k in keys}
    ttm.update({"eps_diluted": round(sum(q["eps_diluted"] for q in qs), 2),
                "ebit": ttm["operating_income"], "ocf": ttm["fcf"] * 1.1,
                "capex": -ttm["fcf"] * 0.1, "dividends_paid": 12.5e9,
                "eff_tax_rate": 0.15})
    return {"available": True, "ttm": ttm, "annual": [],
            "balance": {"total_debt": 8.5e9, "cash": 29.5e9, "cash_st": 29.5e9,
                        "equity_securities": 44.0e9, "equity": 150e9,
                        "as_of_date": qs[0]["end"]},
            "quarters": qs, "latest_quarter_end": qs[0]["end"],
            "source": "replay statements", "as_of": "2026-11-21"}


def nvda_providers(fresh_statements: bool = True) -> dict:
    quote = {
        "symbol": "NVDA", "name": "NVIDIA CORP", "last": 130.03,
        "regular_last": 125.53, "regular_change_pct": -14.01,
        "ah_price": 130.03, "ah_change_pct": 3.58, "is_extended": True,
        "open": 140.0, "close_prev": 125.53, "high_today": 141.0, "low_today": 124.9,
        "week52_high": 243.37, "week52_low": 124.50, "volume": 0,
        "net_change_pct": 3.58, "quote_time_ms": ms(ny(2026, 11, 20, 20, 0)),
        "asset_type": "EQUITY",
    }
    hist = bars_ending(NVDA_FRI, [145.98, 125.53])
    return {
        "quotes": lambda s: quote,
        "daily_history": lambda s, *a: spy_bars(NVDA_FRI) if s == "SPY" else hist,
        "fundamentals": lambda s: {
            "available": True, "pe_ttm": 15.5, "eps_ttm": 9.31,
            "market_cap_m": 3_518_000.0, "revenue_growth_ttm_pct": 93.3,
            "gross_margin_pct": 70.1, "operating_margin_pct": 62.0,
            "net_margin_pct": 62.5, "dividend_yield_pct": 0.36, "beta_reported": 2.4},
        "shares_out": lambda s: NVDA_SHARES_M,
        "eps_check": lambda s: {"eps_ttm": 9.31, "source": "yfinance trailingEps",
                                "as_of": "2026-11-21"},
        "financial_statements": lambda s: nvda_statements(fresh_statements),
        "dividend_history": lambda s: {
            "available": True, "source": "replay", "as_of": "2026-11-21",
            "events": [{"date": "2025-12-04", "amount": 0.01},
                       {"date": "2026-03-11", "amount": 0.01},
                       {"date": "2026-06-10", "amount": 0.25},
                       {"date": "2026-09-10", "amount": 0.25}]},
        "news_sentiment": lambda s: {"available": False},
        "news_headlines": lambda s, *a: [
            {"headline": "Intel shares jump on foundry customer win", "source": "Reuters",
             "url": "", "ts": int(ny(2026, 11, 20, 9).timestamp()), "summary": ""},
            {"headline": "Jim Cramer's lightning round: don't chase software",
             "source": "CNBC", "url": "", "ts": int(ny(2026, 11, 19, 19).timestamp()),
             "summary": ""},
            {"headline": "Should you buy an annuity before rates fall?",
             "source": "MarketWatch", "url": "", "ts": int(ny(2026, 11, 18).timestamp()),
             "summary": ""},
            {"headline": "Anthropic closes new funding round", "source": "Bloomberg",
             "url": "", "ts": int(ny(2026, 11, 17).timestamp()), "summary": ""},
        ],
        "analyst_recs": lambda s: {"available": True, "months": [
            {"period": "2026-08-01", "strong_buy": 20, "buy": 46, "hold": 2,
             "sell": 1, "strong_sell": 0, "total": 69}]},
        "earnings": lambda s: {"available": True, "next_date": "2027-02-24",
                               "days_until": 95, "surprises": []},
        "earnings_dates": lambda s, *a: [{"date": "2026-08-26", "hour": "amc"},
                                         {"date": "2026-11-17", "hour": "amc"}],
        "earnings_confirm": lambda s: {"date": "2027-02-24", "estimate": True,
                                       "source": "Yahoo"},
        "social": lambda s: {"available": True, "bullish": 2, "bearish": 0,
                             "tagged": 2, "total_msgs": 30, "polarity": 1.0},
        "option_chain": lambda s: {"available": True, "put_call_ratio": 1.2,
                                   "atm_iv_pct": 75.0, "implied_move_pct": 9.8,
                                   "implied_move_dte": 6,
                                   "as_of": "2026-11-20T21:00:00+00:00"},
        "account": lambda: {
            # position sizes and account values are illustrative, not real
            "as_of": "2026-11-21T11:24:00", "total_value": 100_000.0, "cash": 10_000.0,
            "day_pl": 450.0, "day_pl_pct": 0.3,
            "positions": [{"symbol": "NVDA", "description": "NVIDIA", "qty": 100.0,
                           "avg_cost": 118.0, "market_value": 12553.00,
                           "cost_basis": 11800.0, "gain": 753.0, "gain_pct": 6.38,
                           "day_pl": 450.0, "day_pl_pct": 3.58, "weight_pct": 12.55}]},
        "insiders": None, "short_interest": None,
    }


# ─── GOOGL, Thu 2026-10-29 10:30 ET (market open, morning after Q3) ─────────────

GOOGL_NOW = ny(2026, 10, 29, 10, 30)


def googl_statements(fresh: bool) -> dict:
    B = 1e9
    ends = (["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31"] if fresh
            else ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"])
    qs = [{"end": e, "revenue": 100 * B, "gross_profit": 59 * B,
           "operating_income": 33 * B, "pretax_income": 36 * B,
           "net_income": 30 * B, "eps_diluted": 2.4 + 0.1 * (3 - i), "fcf": 20 * B}
          for i, e in enumerate(ends)]
    ttm = {k: sum(q[k] for q in qs) for k in ("revenue", "gross_profit",
           "operating_income", "pretax_income", "net_income", "fcf")}
    ttm.update({"eps_diluted": round(sum(q["eps_diluted"] for q in qs), 2),
                "ebit": ttm["operating_income"], "dividends_paid": 10 * B,
                "eff_tax_rate": 0.17})
    return {"available": True, "ttm": ttm, "annual": [],
            "balance": {"total_debt": 25 * B, "cash": 95 * B, "cash_st": 95 * B,
                        "equity_securities": 0.0, "equity": 360 * B,
                        "as_of_date": ends[0]},
            "quarters": qs, "latest_quarter_end": ends[0],
            "source": "replay statements", "as_of": "2026-10-29"}


def googl_providers(statements_fresh_on_refetch: bool = True) -> dict:
    wed = dt.date(2026, 10, 28)
    today_partial = {"date": "2026-10-29", "open": 290, "high": 291, "low": 268,
                     "close": 270.0, "volume": 9_000_000}
    hist = bars_ending(wed, [308.60], daily=0.018, extra_today=today_partial)
    quote = {"symbol": "GOOGL", "name": "Alphabet Inc Class A", "last": 270.00,
             "regular_last": 270.00, "regular_change_pct": -12.51, "ah_price": None,
             "ah_change_pct": None, "is_extended": False, "close_prev": 308.60,
             "week52_high": 320.0, "week52_low": 180.0, "net_change_pct": -12.51,
             "quote_time_ms": ms(ny(2026, 10, 29, 10, 29)), "asset_type": "EQUITY"}
    return {
        "quotes": lambda s: quote,
        "daily_history": lambda s, *a: spy_bars(wed) if s == "SPY" else hist,
        "fundamentals": lambda s: {"available": True, "pe_ttm": 16.3, "eps_ttm": 19.91,
                                   "market_cap_m": 3_300_000.0, "net_margin_pct": 54.8,
                                   "revenue_growth_ttm_pct": 14.0},
        "shares_out": lambda s: 12_100.0,
        "financial_statements": lambda s: googl_statements(statements_fresh_on_refetch),
        "dividend_history": lambda s: {"available": True, "source": "replay",
                                       "as_of": "2026-10-29", "events": [
            {"date": "2025-12-08", "amount": 0.21}, {"date": "2026-03-09", "amount": 0.21},
            {"date": "2026-06-09", "amount": 0.21}, {"date": "2026-09-08", "amount": 0.21}]},
        "earnings": lambda s: {"available": True, "next_date": "2027-02-03",
                               "days_until": 97, "surprises": []},
        "earnings_dates": lambda s, *a: [{"date": "2026-07-23", "hour": "amc"},
                                         {"date": "2026-10-28", "hour": "amc"}],
        "account": lambda: {
            "as_of": "2026-10-29T10:29:00", "total_value": 100_000.0, "cash": 10_000.0,
            "day_pl": -25_000.0, "day_pl_pct": -20.0,
            "positions": [{"symbol": "GOOGL", "qty": 40.0, "avg_cost": 160.0,
                           "market_value": 10800.0, "cost_basis": 6400.0,
                           "gain": 4400.0, "gain_pct": 68.75,
                           "day_pl": -25_000.0, "day_pl_pct": -69.8}]},
    }


# ─── VST, Tue 2026-11-04 18:30 ET (after the close) ───────────────────────────

VST_NOW = ny(2026, 11, 4, 18, 30)


def vst_providers(bar_published: bool = True) -> dict:
    tue = dt.date(2026, 11, 4)
    if bar_published:
        hist = bars_ending(tue, [175.83, 150.00], daily=0.03)
    else:
        hist = bars_ending(dt.date(2026, 11, 3), [175.83], daily=0.03)
    quote = {"symbol": "VST", "name": "Vistra Corp", "last": 148.48,
             "regular_last": 150.00, "regular_change_pct": -14.69, "ah_price": 148.48,
             "ah_change_pct": -1.01, "is_extended": True, "close_prev": 175.83,
             "week52_high": 220.0, "week52_low": 120.0, "net_change_pct": -15.55,
             "quote_time_ms": ms(ny(2026, 11, 4, 18, 29)), "asset_type": "EQUITY"}
    return {
        "quotes": lambda s: quote,
        "daily_history": lambda s, *a: spy_bars(tue) if s == "SPY" else hist,
        "account": lambda: {
            "as_of": "2026-11-04T18:29:00", "total_value": 100_000.0, "cash": 10_000.0,
            "day_pl": -15_000.0, "day_pl_pct": -13.0,
            "positions": [{"symbol": "VST", "qty": 40.0, "avg_cost": 140.0,
                           "market_value": 6000.0, "cost_basis": 5600.0,
                           "gain": 400.0, "gain_pct": 7.14,
                           "day_pl": -15_000.0, "day_pl_pct": -71.4}]},
    }


# ─── installation ─────────────────────────────────────────────────────────────

def install(monkeypatch, providers: dict) -> None:
    """Route every capability through the scenario (None/absent = no provider)."""
    from providers import registry
    monkeypatch.delenv("TICKERBANDS_MOCK", raising=False)
    monkeypatch.setattr(registry, "resolve", lambda cap: providers.get(cap))
    monkeypatch.setattr(registry, "provider_name",
                        lambda cap: "replay" if providers.get(cap) else "none")


def add_discussion(ticker: str, created_ts: str, summary: str, body: str = "## TL;DR\n\nNote.",
                   **cols) -> None:
    from db import connect
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO claude_discussions (ticker, file_path, response_markdown, summary, "
            "tags, created_ts, file_mtime, news_view, news_note, stance, stance_horizon, "
            "stance_note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ticker, f"/replay/{ticker}/{created_ts}.md", body, summary, "[]",
             created_ts, created_ts, cols.get("news_view"), cols.get("news_note"),
             cols.get("stance"), cols.get("stance_horizon"), cols.get("stance_note")))
        conn.commit()
    finally:
        conn.close()
