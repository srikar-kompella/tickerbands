"""
analysis/scorecard.py — forward outcome audit of research stances.

The app never computes buy/sell (direction is empirically dead at n≈9,600);
stances are dated judgments from research sessions. The only honest way to
"validate" them is forward tracking: what happened to price AFTER each stance,
against the market over the same window. That's what this builds — a
scorecard, not a scoreboard: with few stances over short windows the numbers
are noise, and the payload says so via `caveat`.

Pure functions; the route feeds them cached bars. Excess return vs SPY is the
headline number — a buy that's up 5% while SPY is up 8% validated nothing.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

# stance → what "working" means for the sign of EXCESS return
_EXPECT = {"buy": +1, "sell": -1, "trim": -1, "hold": 0, "watch": 0}


def _close_on_or_before(bars: list[dict], day: str) -> float | None:
    prior = [b for b in bars if b["date"] <= day]
    return prior[-1]["close"] if prior else None


def score_stances(stances: list[dict], bars_by_symbol: dict[str, list[dict]],
                  spy_bars: list[dict]) -> dict[str, Any]:
    """stances: rows with ticker/stance/stance_horizon/stance_note/created_ts.
    Every stance is scored (not just the latest per ticker) — each was a call.
    """
    rows = []
    for s in stances:
        sym = s["ticker"].upper()
        day = str(s["created_ts"])[:10]
        bars = bars_by_symbol.get(sym) or []
        then_px = _close_on_or_before(bars, day)
        now_px = bars[-1]["close"] if bars else None
        spy_then = _close_on_or_before(spy_bars, day)
        spy_now = spy_bars[-1]["close"] if spy_bars else None
        ret = (now_px / then_px - 1) * 100 if then_px and now_px else None
        spy_ret = (spy_now / spy_then - 1) * 100 if spy_then and spy_now else None
        excess = round(ret - spy_ret, 2) if ret is not None and spy_ret is not None else None
        try:
            days = (dt.date.fromisoformat(bars[-1]["date"])
                    - dt.date.fromisoformat(day)).days if bars else None
        except ValueError:
            days = None
        expect = _EXPECT.get(s["stance"], 0)
        rows.append({
            "ticker": sym, "stance": s["stance"],
            "horizon": s.get("stance_horizon"), "note": s.get("stance_note"),
            "date": day, "days_elapsed": days,
            "price_then": round(then_px, 2) if then_px else None,
            "price_now": round(now_px, 2) if now_px else None,
            "return_pct": round(ret, 2) if ret is not None else None,
            "spy_return_pct": round(spy_ret, 2) if spy_ret is not None else None,
            "excess_pct": excess,
            # only directional calls get a working/against verdict
            "working": (None if expect == 0 or excess is None
                        else (excess * expect) > 0),
        })

    by_type: dict[str, dict] = {}
    for st in ("buy", "hold", "watch", "trim", "sell"):
        typ = [r for r in rows if r["stance"] == st and r["excess_pct"] is not None]
        if typ:
            ex = sorted(r["excess_pct"] for r in typ)
            by_type[st] = {
                "n": len(typ),
                "avg_excess_pct": round(sum(ex) / len(ex), 2),
                "median_excess_pct": round(ex[len(ex) // 2], 2),
            }

    scored = [r for r in rows if r["excess_pct"] is not None]
    return {
        "rows": sorted(rows, key=lambda r: (r["date"], r["ticker"])),
        "summary": by_type,
        "n_scored": len(scored),
        "caveat": ("Forward audit, not proof: with this few stances over short "
                   "windows, results are dominated by noise. The scorecard "
                   "becomes meaningful as stances age toward their horizons "
                   "and the sample grows. Excess vs SPY is the number that "
                   "matters — raw returns flatter everyone in a rising market."),
    }
