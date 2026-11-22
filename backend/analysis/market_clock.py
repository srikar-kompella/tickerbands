"""
analysis/market_clock.py — which regular US equity session a report refers to.

Why this exists (NVDA report, Sat 2026-11-21): the report treated Friday's
after-hours quote as "the price" and labeled a Saturday "+3.58% today" while
the last real session was −14.01%. Every price-dependent number needs to know
whether the market is open, and if not, which session's official close it is
quoting. That's a calendar question, so it lives here — pure, no I/O.

NYSE full-day holidays and 1:00 PM early closes are listed explicitly for
2025–2028 (NYSE release of 2025-12-23). Outside that range every weekday is treated as a trading day, and
`calendar_known()` returns False so the caller can say so.
"""
from __future__ import annotations

import datetime as dt

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover — tzdata missing
    NY = None

REGULAR_OPEN = dt.time(9, 30)
REGULAR_CLOSE = dt.time(16, 0)
EARLY_CLOSE = dt.time(13, 0)

# NYSE holidays (exchange closed all day).
HOLIDAYS = {
    # 2025
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27",
    "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
    # 2028 (no New Year's holiday: Jan 1 is a Saturday)
    "2028-01-17", "2028-02-21", "2028-04-14", "2028-05-29", "2028-06-19",
    "2028-07-04", "2028-09-04", "2028-11-23", "2028-12-25",
}
# 1:00 PM ET early closes.
EARLY_CLOSES = {"2025-07-03", "2025-11-28", "2025-12-24",
                "2026-11-27", "2026-12-24", "2027-11-26",
                "2028-07-03", "2028-11-24"}
# Source: NYSE Group release, 2025-12-23 ("2026, 2027 and 2028 Holiday and
# Early Closings Calendar"). Extend when NYSE publishes 2029.
CALENDAR_YEARS = {2025, 2026, 2027, 2028}


def calendar_known(d: dt.date) -> bool:
    return d.year in CALENDAR_YEARS


def calendar_expiring(d: dt.date) -> bool:
    """True from Oct 1 of the last listed year — time to add the next one."""
    return d.year == max(CALENDAR_YEARS) and d.month >= 10


def is_trading_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS


def close_time(d: dt.date) -> dt.time:
    return EARLY_CLOSE if d.isoformat() in EARLY_CLOSES else REGULAR_CLOSE


def previous_trading_day(d: dt.date) -> dt.date:
    p = d - dt.timedelta(days=1)
    while not is_trading_day(p):
        p -= dt.timedelta(days=1)
    return p


def now_ny() -> dt.datetime:
    return dt.datetime.now(NY) if NY else dt.datetime.now()


def to_ny(ts: dt.datetime) -> dt.datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=NY) if NY else ts
    return ts.astimezone(NY) if NY else ts


def session_state(now: dt.datetime | None = None) -> dict:
    """→ {state, today, last_session, last_close_ts, calendar_known}

    state: "open"   regular session in progress (today is the live session)
           "pre"    trading day, before 9:30 ET
           "post"   trading day, after the regular close
           "closed" weekend or holiday
    last_session: date of the most recent session whose regular close has
    PASSED (when open, that's the previous trading day).
    """
    now = to_ny(now or now_ny())
    today = now.date()
    t = now.time()
    if is_trading_day(today):
        if t < REGULAR_OPEN:
            state = "pre"
        elif t < close_time(today):
            state = "open"
        else:
            state = "post"
    else:
        state = "closed"
    last = today if state == "post" else previous_trading_day(today)
    return {
        "state": state,
        "today": today,
        "last_session": last,
        "calendar_known": calendar_known(today),
        "now": now,
    }


def describe_close(d: dt.date) -> str:
    """'Fri Nov 20, 2026, 4:00 PM ET' (early closes say 1:00 PM)."""
    ct = close_time(d)
    hour = ct.strftime("%I:%M %p").lstrip("0")
    return f"{d.strftime('%a %b')} {d.day}, {d.year}, {hour} ET"


def session_word(session: dt.date, today: dt.date) -> str:
    """'today' only when the session IS today; otherwise 'Fri Nov 20'."""
    if session == today:
        return "today"
    return f"{session.strftime('%a %b')} {session.day}"
