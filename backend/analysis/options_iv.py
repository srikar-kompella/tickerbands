"""
analysis/options_iv.py — implied vol restated to the last official close.

Why: with the market closed, the broker keeps re-solving implied vol from the
same frozen option marks while the clock runs down to expiry. Less time left
for the same straddle price means a higher implied vol. NVDA, weekend of
Nov 21–22 2026: the straddle stayed ±8.05% while "ATM IV" read 115.5% on
Saturday morning and 132.5% after midnight — √(52.6 h ÷ 39.9 h) × 115.5% =
132.6%, i.e. pure clock time. The figure the marks actually imply is the one
at the close they were struck at.

Rule: when the chain was read after the last session's close, scale the
broker IV by √(time to expiry at read ÷ time to expiry at that close), time
measured in hours to the expiry's 4 PM ET close. During the session nothing
changes. Without an expiry date (older cache) the expiry is the read date
plus the reported days to expiry.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

from analysis import market_clock as mc


def _parse(ts: str | None) -> dt.datetime | None:
    if not ts:
        return None
    try:
        t = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return mc.to_ny(t)


def _close_at(d: dt.date) -> dt.datetime:
    return mc.to_ny(dt.datetime.combine(d, mc.close_time(d)))


def expiry_close(opt: dict) -> dt.datetime | None:
    """4 PM ET (or the early close) on the expiry the ATM figures come from."""
    exp = opt.get("expiry_date")
    if exp:
        try:
            return _close_at(dt.date.fromisoformat(str(exp)[:10]))
        except ValueError:
            pass
    read = _parse(opt.get("as_of"))
    dte = opt.get("implied_move_dte")
    if read is None or dte is None:
        return None
    return _close_at(read.date() + dt.timedelta(days=int(dte)))


def restate_to_close(opt: dict | None, basis: dict | None) -> dict | None:
    """A copy of the options summary with `atm_iv_pct` restated to the last
    official close when the chain was read after it (market closed). Adds
    `atm_iv_raw_pct` and `atm_iv_note`; returns the input unchanged when no
    restatement applies. Never mutates the cached dict."""
    if not opt or not opt.get("available") or opt.get("atm_iv_pct") is None:
        return opt
    b = basis or {}
    if b.get("price_kind") == "live" or not b.get("session_date"):
        return opt
    read = _parse(opt.get("as_of"))
    exp = expiry_close(opt)
    try:
        last_close = _close_at(dt.date.fromisoformat(b["session_date"]))
    except ValueError:
        return opt
    if read is None or exp is None or read <= last_close or exp <= read:
        return opt
    t_read = (exp - read).total_seconds()
    t_close = (exp - last_close).total_seconds()
    if t_close <= 0:
        return opt
    raw = float(opt["atm_iv_pct"])
    iv = round(raw * math.sqrt(t_read / t_close), 1)
    if abs(iv - raw) < 0.1:
        return opt
    sd = dt.date.fromisoformat(b["session_date"])
    day = f"{sd:%a %b} {sd.day}"
    out = dict(opt)
    out.update({
        "atm_iv_pct": iv, "atm_iv_raw_pct": raw,
        "atm_iv_note": (f"restated to the {day} close: the broker shows {raw:.1f}%, "
                        "re-solved from the same closing marks with less time left to "
                        "expiry while the market is closed"),
    })
    return out


def sessions_to_expiry(opt: dict | None, basis: dict | None) -> int | None:
    """Trading sessions the straddle covers: those after the report's
    session (or including today's, when it is live) through the expiry.
    Calendar days miscount over a weekend — the same Friday-close straddle
    to a Monday expiry read "2d" on Saturday and "1d" on Sunday."""
    exp = expiry_close(opt or {})
    b = basis or {}
    if exp is None or not b.get("session_date"):
        return None
    try:
        d = dt.date.fromisoformat(b["session_date"])
    except ValueError:
        return None
    if b.get("price_kind") != "live":
        d += dt.timedelta(days=1)
    n = 0
    while d <= exp.date():
        n += mc.is_trading_day(d)
        d += dt.timedelta(days=1)
    return n or None
