"""
analysis/price_basis.py — ONE price per report, decided once.

The NVDA report of Sat 2026-11-21 showed $130.03 "+3.58% today" and a 0.86σ
"inside band" move. $130.03 was Friday's after-hours quote; Friday's official
close was $125.53, down 14.01% (~3σ, outside the band). Market cap, P/E, EV
and yield were then computed at yet other prices. This module removes the
ambiguity at the source:

  * market OPEN   → the live regular-session price; the day move is live vs
                    the prior official close.
  * market CLOSED → the official close of the last completed regular session
    (pre-market,    (from daily bars; or the broker's regular-session last
    after hours,    print when the bar hasn't been published yet). The day
    weekend,        move is that close vs the PRIOR official close — what
    holiday)        actually happened in the last real session.
  * Extended-hours prices are reported only as a separate, labeled line.

Everything downstream (market cap, P/E, P/FCF, EV, yields, position value,
P/L, weight, 52-week position) uses `basis["price"]` and nothing else.

Pure: inputs in, dict out. `now` is injectable for replay tests.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from analysis import market_clock as mc

# A live quote older than this while the market is open is not "the price".
LIVE_QUOTE_MAX_AGE_S = 10 * 60
# Broker regular-session print vs the history bar for the same session.
CLOSE_MISMATCH_PCT = 0.5


def _ms_to_ny(ms: int | None) -> dt.datetime | None:
    if not ms:
        return None
    try:
        return mc.to_ny(dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc))
    except (OverflowError, OSError, ValueError):
        return None


def _fmt_time(t: dt.datetime) -> str:
    return t.strftime("%I:%M %p").lstrip("0") + " ET"


def build(quote: dict | None, history: list[dict] | None,
          div_events: list[dict] | None = None,
          next_ex_date: str | None = None,
          now: dt.datetime | None = None,
          quote_status: str = "ok",
          quote_age_s: float | None = None) -> tuple[dict[str, Any], list[dict]]:
    """→ (basis, completed_bars). `completed_bars` ends with the official
    close the report uses as its anchor (when the market is open: the prior
    session; otherwise: the last completed session)."""
    quote = quote or {}
    history = sorted(history or [], key=lambda b: b["date"])
    div_events = div_events or []
    clk = mc.session_state(now)
    state = quote.get("simulated_session") or clk["state"]
    today: dt.date = clk["today"]
    now_ny: dt.datetime = clk["now"]
    warnings: list[str] = []

    if state == "open":
        cutoff = today.isoformat()
        bars = [b for b in history if b["date"] < cutoff]
        last_session = bars[-1]["date"] if bars else None
    else:
        last_session = clk["last_session"].isoformat()
        bars = [b for b in history if b["date"] <= last_session]

    synthetic = False
    if state != "open" and last_session and (not bars or bars[-1]["date"] < last_session):
        # the session ended but its daily bar isn't published yet → use the
        # broker's regular-session last print, which IS the official close
        rl = quote.get("regular_last")
        if rl and quote_status == "ok":
            bars = bars + [{"date": last_session, "close": float(rl), "volume": 0,
                            "synthetic": True}]
            synthetic = True
        elif bars:
            warnings.append(
                f"no official close available for {last_session} yet — showing "
                f"the {bars[-1]['date']} close; figures may be a session behind")
    if (state != "open" and bars and not synthetic and quote.get("regular_last")
            and bars[-1]["date"] == last_session and quote_status == "ok"):
        rl, bc = float(quote["regular_last"]), float(bars[-1]["close"])
        if bc and abs(rl / bc - 1) * 100 > CLOSE_MISMATCH_PCT:
            warnings.append(
                f"broker regular-session price ${rl:,.2f} differs from the "
                f"official close ${bc:,.2f} in the daily bars — using the bar")

    basis: dict[str, Any] = {
        "state": state,
        "simulated": bool(quote.get("simulated_session")),
        "calendar_known": clk["calendar_known"],
        "generated_ny": now_ny.isoformat(timespec="minutes"),
        "price": None, "price_kind": None, "session_date": None,
        "prior_close": None, "prior_date": None,
        "day_move_pct": None, "day_move_abs": None,
        "ex_div_amount": None, "ex_div_pending": False,
        "after_hours": None, "synthetic_close": synthetic,
        "price_label": None, "as_of_label": None, "move_word": None,
        "is_today": False, "warnings": warnings,
    }
    if not clk["calendar_known"]:
        warnings.append("holiday calendar not loaded for this year — weekdays "
                        "assumed to be trading days")
    elif mc.calendar_expiring(today):
        warnings.append("NYSE holiday calendar ends this year — add next year's "
                        "dates to analysis/market_clock.py")

    if state == "open":
        live = quote.get("regular_last") or quote.get("last")
        q_time = _ms_to_ny(quote.get("quote_time_ms"))
        age = ((now_ny - q_time).total_seconds() if q_time
               else (quote_age_s if quote_age_s is not None else None))
        if quote_status != "ok" or live is None:
            warnings.append("live quote unavailable or served from a stale "
                            "cache — price withheld while the market is open")
            live = None
        elif age is not None and age > LIVE_QUOTE_MAX_AGE_S:
            warnings.append(f"live quote is {round(age / 60)} min old while the "
                            "market is open — price withheld")
            live = None
        if not bars:
            return basis, bars
        basis.update({
            "price": float(live) if live is not None else None,
            "price_kind": "live",
            "session_date": today.isoformat(),
            "prior_close": float(bars[-1]["close"]),
            "prior_date": bars[-1]["date"],
            "is_today": True,
            "move_word": "today",
            "price_label": (f"Live regular-session price · {_fmt_time(q_time)}"
                            if q_time else "Live regular-session price"),
            "as_of_label": (f"as of {_fmt_time(q_time) if q_time else 'now'} today "
                            "(market open)"),
        })
    else:
        if len(bars) < 2:
            return basis, bars
        sess = dt.date.fromisoformat(bars[-1]["date"])
        basis.update({
            "price": float(bars[-1]["close"]),
            "price_kind": "official_close",
            "session_date": bars[-1]["date"],
            "prior_close": float(bars[-2]["close"]),
            "prior_date": bars[-2]["date"],
            "is_today": sess == today,
            "move_word": mc.session_word(sess, today),
            "price_label": f"Official close · {mc.describe_close(sess)}",
            "as_of_label": ("as of today's close" if sess == today
                            else f"as of {sess.strftime('%A')}'s close "
                                 f"({sess.strftime('%b')} {sess.day})"),
        })

    # ── distribution on the move's session date (BUG B, generalized) ─────────
    sess_iso = basis["session_date"]
    ex_amt = sum(float(e.get("amount") or 0.0) for e in div_events
                 if str(e.get("date"))[:10] == sess_iso)
    if ex_amt == 0.0:
        q_ex = str(quote.get("div_ex_date") or "")[:10]
        if q_ex == sess_iso and quote.get("div_pay_amount"):
            ex_amt = float(quote["div_pay_amount"])
        elif sess_iso and (str(next_ex_date or "")[:10] == sess_iso or q_ex == sess_iso):
            basis["ex_div_pending"] = True
    if ex_amt > 0:
        basis["ex_div_amount"] = round(ex_amt, 4)

    price, prior = basis["price"], basis["prior_close"]
    if price is not None and prior:
        anchor = prior - ex_amt
        basis["day_move_abs"] = round(price - anchor, 4)
        basis["day_move_pct"] = round((price / anchor - 1) * 100, 2)

    # ── extended-hours quote: separate, labeled, never the price ────────────
    if state != "open" and basis["price"]:
        ah = quote.get("ah_price")
        q_time = _ms_to_ny(quote.get("ah_time_ms") or quote.get("quote_time_ms"))
        last = quote.get("last")
        close_dt = mc.to_ny(dt.datetime.combine(
            dt.date.fromisoformat(basis["session_date"]),
            mc.close_time(dt.date.fromisoformat(basis["session_date"]))))
        if ah is not None and quote.get("ah_time_ms") is None and q_time and q_time < close_dt:
            ah = None          # an extended print from BEFORE the close is not after-hours
        if (ah is None and last and abs(float(last) / basis["price"] - 1) > 0.0005
                and (q_time is None or q_time > close_dt)):
            ah = float(last)
        if ah:
            kind = "pre-market" if state == "pre" else "after hours"
            when = ""
            if q_time:
                when = f", {_fmt_time(q_time)} {q_time.strftime('%a')}"
            basis["after_hours"] = {
                "price": round(float(ah), 2),
                "change_pct": round((float(ah) / basis["price"] - 1) * 100, 2),
                "label": f"{kind}{when}",
                "kind": kind,
                "quote_time": q_time.isoformat(timespec="minutes") if q_time else None,
            }
    return basis, bars
