"""
analysis/research_quality.py — can a stored research note still be relied on?

Two pure checks over the frontmatter a research session wrote (prompt v5
fields live in `research_meta`; older notes simply lack them):

  * prior_note_status() — OK / STALE / INVALID / SUPERSEDED per note, with
    the reason. The research prompt lists it beside every prior note and the
    report puts a chip on the note, so a later session does not build on a
    failed check. Each note is judged against every completed ≥2σ session in
    the daily bars (big_move_sessions) that lies inside its news window, so
    the verdict does not flip back after one ordinary day.
  * stance_flags() — warnings for the stance the app shows beside a ticker.
    They join the `claude_stance` field warnings (report strip, app header's
    "re-check" badge, the Watchlist's ⚠ count). A value the parser could not
    read (stance, review_by, price_used…) is flagged here rather than dropped.
  * pick_stance() / pick_news() — which note's stance and news check are
    shown, and what a NEWER note without a usable one means for them.

Why (Nov 2026): a note said "no material news" the day after a ≥2σ session,
a hold was built on it, and a later session restated it as current. Stances
also clustered on watch/hold because neither carried conditions that could
later be checked.

Nothing here computes a buy/sell or feeds the Setup Score — it only judges
whether a human-readable note is complete, current and internally consistent.
See docs/RESEARCH_PROMPT.md.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from analysis import market_clock as mc
from analysis import vol_bands
from analysis.news_relevance import (BIG_MOVE_SIGMA, _parse_ts, big_sessions, cause_text,
                                     failed_news_check, is_v5, sessions_closed_after,
                                     sessions_to_explain, stated_cause)

OK, STALE, INVALID, SUPERSEDED = "OK", "STALE", "INVALID", "SUPERSEDED"
PRICE_TOLERANCE = 0.03          # note written while the session traded: a live price moves
PRICE_TOLERANCE_CLOSED = 0.005  # market closed: the report price IS the official close
NEEDS_CONDITIONS = {"hold", "watch"}
# keys a prompt-v5 note must carry (upgrade_if / downgrade_if for hold and
# watch are covered by the "no conditions" flag)
V5_REQUIRED = ("research_status", "price_used", "review_by", "stance_confidence")


def _meta(row: dict | None) -> dict:
    m = (row or {}).get("research_meta")
    return m if isinstance(m, dict) else {}


def _ts_key(s: Any) -> str:
    """created_ts normalized to minute precision ('YYYY-MM-DDTHH:MM') so a
    `supersedes` entry matches with or without seconds / a space separator."""
    return str(s or "").strip().replace(" ", "T")[:16]


_DATE_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y", "%d %b %Y",
                 "%d %B %Y", "%m/%d/%Y", "%Y/%m/%d", "%Y.%m.%d")


def parse_date(v: Any) -> dt.date | None:
    """A date as a session might write it: ISO ('2031-03-09', with or without
    a time), 'Mar 9, 2031', 'March 9 2031', '9 Mar 2031', '03/09/2031'
    (month first), '2031/03/09'. None when it is not a date."""
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    txt = " ".join(str(v or "").strip().strip("\"'").split())
    if not txt:
        return None
    try:
        return dt.date.fromisoformat(txt[:10])
    except ValueError:
        pass
    txt = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", txt, flags=re.I).replace("Sept ", "Sep ")
    txt = re.sub(r"^(\w{3})\.", r"\1", txt)                 # "Mar. 9, 2031"
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    return None


def _unreadable(row: dict | None) -> dict:
    u = _meta(row).get("unreadable")
    return u if isinstance(u, dict) else {}


def stance_owned(stance: dict | None) -> str | None:
    """"yes" / "no" / None — whether the stance was written for a name the
    user owns. The note's own `owned` field decides; where the note does not
    say, the app's position data does (`account_owned`, set by the composer
    and the screener from the brokerage account, when one is connected)."""
    owned = _meta(stance).get("owned")
    if owned in ("yes", "no"):
        return owned
    acct = (stance or {}).get("account_owned")
    return {True: "yes", False: "no"}.get(acct) if isinstance(acct, bool) else None


def stance_label(stance: dict | None) -> str:
    """How a stance is shown. `sell` on a name the user does not own means
    avoid/pass (prompt v5, step 6) and is shown as AVOID; the stored value
    stays `sell`, which the scorecard scores as a call to lag the market.
    A v5 note written with ownership unknown (no `owned`, no account data) was
    written for someone deciding whether to own it (step 6), so its `sell` is
    an avoid too."""
    word = str((stance or {}).get("stance") or "").lower()
    if word == "sell":
        owned = stance_owned(stance)
        if owned == "no" or (owned is None and is_v5(stance)):
            return "AVOID"
    return word.upper()


# ─── ≥2σ sessions from the daily bars ──────────────────────────────────────────

def big_move_sessions(bars: list[dict] | None,
                      div_events: list[dict] | None = None) -> list[dict[str, Any]]:
    """Every completed session in `bars` that moved ≥ 2σ, oldest first:
    {"session_date", "z", "move_pct"}.

    Same arithmetic as the report's day move — close ÷ prior close − 1 on
    dividend-adjusted closes, over the daily σ of the 20 returns BEFORE that
    session — so a session keeps its verdict on every later day. Needs 21
    closes ahead of a session; earlier bars cannot be judged."""
    if not bars or len(bars) < 22:
        return []
    closes = vol_bands.adjust_closes_for_dividends(bars, div_events or [])
    out = []
    for i in range(21, len(closes)):
        if not closes[i - 1]:
            continue
        mv = vol_bands.move_z_score(closes[i - 1], closes[i],
                                    vol_bands.realized_vol(closes[:i], 20))
        if abs(mv["z"]) >= BIG_MOVE_SIGMA:
            out.append({"session_date": str(bars[i]["date"])[:10], "z": mv["z"],
                        "move_pct": mv["move_pct"]})
    return out


# ─── prior notes ───────────────────────────────────────────────────────────────

def _sessions_txt(ss: list[dict]) -> str:
    dates = [s["session_date"] for s in ss]
    return ("session of " + dates[0] if len(dates) == 1 else
            "sessions of " + ", ".join(dates[:-1]) + " and " + dates[-1])


def _judge(notes: list[dict], move: dict | None, basis: dict | None,
           sessions: list[dict] | None
           ) -> tuple[list[dict[str, Any]], dict[str, dt.datetime], list[bool]]:
    """(status per note in the order given, session date → when its cause was
    first stated by a sound note, whether each note is sound — OK or STALE
    before supersession). Notes are walked oldest first, because whether a
    note had to explain a session depends on the notes before it."""
    big = big_sessions(move, basis, sessions)
    last = (basis or {}).get("session_date")

    superseded: dict[str, str] = {}          # note key → superseding note's created_ts
    for n in notes:
        for target in _meta(n).get("supersedes") or []:
            k = _ts_key(target)
            if k and k < _ts_key(n.get("created_ts")):
                superseded.setdefault(k, str(n.get("created_ts")))

    times = [_parse_ts(n.get("created_ts")) for n in notes]
    # oldest first; a note with no readable time is taken as current (last)
    order = sorted(range(len(notes)),
                   key=lambda i: (times[i] is None, times[i].timestamp() if times[i] else 0.0))
    explained: dict[str, dt.datetime] = {}
    out: list[dict[str, Any]] = [{} for _ in notes]
    sound_flags = [False] * len(notes)
    for i in order:
        n, t = notes[i], times[i]
        m, unread = _meta(n), _unreadable(n)
        v5 = is_v5(n)
        status, reason = OK, ""
        key = _ts_key(n.get("created_ts"))
        after = sessions_closed_after(t, big)
        failed = failed_news_check(n, t, big, last, explained)
        if m.get("research_status") in ("partial", "failed"):
            status = INVALID
            reason = f"its research was incomplete (research_status: {m['research_status']})"
        elif v5 and unread.get("research_status"):
            status = INVALID
            reason = (f"its research_status could not be read ('{unread['research_status']}') "
                      "— the research cannot be taken as complete")
        elif m.get("move_cause") == "not_found":
            status = INVALID
            reason = "its news check did not find the cause of a ≥2σ session (move_cause: not_found)"
        elif v5 and unread.get("move_cause"):
            status = INVALID
            reason = (f"its move_cause could not be read ('{unread['move_cause']}') — the "
                      "news check cannot be taken as done")
        elif failed and failed[1] == "not_stated":
            status = INVALID
            reason = (f"its news check does not state the cause of the ≥2σ "
                      f"{_sessions_txt(failed[0])} ({cause_text(n)}) — a v5 note needs "
                      "move_cause: found for every ≥2σ session in its window")
        elif failed:
            status = INVALID
            reason = (f"its news check failed — it reports no material news after the "
                      f"≥2σ {_sessions_txt(failed[0])}")
        elif len(after) == 1:
            status = STALE
            reason = f"written before the ≥2σ session of {after[0]['session_date']} closed"
        elif after:
            status = STALE
            reason = (f"written before {len(after)} ≥2σ sessions closed "
                      f"(latest {after[-1]['session_date']})")
        # a sound note that states the cause settles its sessions for later notes
        # (STALE is about a LATER session, so it still counts). So does a sound
        # note a later one lists in `supersedes`: the follow-up was written
        # from a prompt whose step 1 listed nothing, answered `move_cause: na`
        # as told, and would otherwise fail for replacing the note it relied on.
        sound = sound_flags[i] = status in (OK, STALE)
        if key in superseded:
            status, reason = SUPERSEDED, f"replaced by the note of {superseded[key][:16]}"
        out[i] = {"created_ts": n.get("created_ts"), "status": status, "reason": reason}
        if sound and t is not None and stated_cause(n) == "found":
            for s in sessions_to_explain(t, big, m.get("news_window"), last):
                explained.setdefault(s["session_date"], t)
    return out, explained, sound_flags


def prior_note_status(notes: list[dict], move: dict | None, basis: dict | None,
                      sessions: list[dict] | None = None) -> list[dict[str, Any]]:
    """One {"created_ts", "status", "reason"} per note, in the order given.

    Each note is judged against EVERY completed ≥2σ session — `sessions`
    (big_move_sessions, from the daily bars) plus the report's last session
    (`move`, `basis`) — that lies inside its news window
    (news_relevance.sessions_to_explain), not only the last one, so a verdict
    does not flip back to OK after one ordinary day. A session the market
    explains — net of the market under one daily σ, marked `market_net_pct`
    by news_relevance.big_sessions from its Recent sessions row — is left
    out of every rule below: it needs no cause and makes no note STALE.
    Sessions without such a row keep the plain ≥2σ rule.

    First match wins:
      SUPERSEDED — a later note lists it in `supersedes`. It has already been
        replaced, so a later session is not told to supersede it again.
      INVALID (do not build on it):
      * research_status is partial or failed — or, in a v5 note, unreadable
      * move_cause is not_found — or, in a v5 note, unreadable
      * a ≥2σ session in its window is left without a cause
        (news_relevance.failed_news_check): a v5 note with anything other
        than `move_cause: found`; a note from before v5 whose text says "no
        material news". A session whose cause an earlier sound note already
        stated is not asked of a later note again — also when that earlier
        note has since been superseded and was otherwise sound.
      STALE: a ≥2σ session closed after it was written — it cannot reflect
        that move (a session that is still trading does not count).
      OK otherwise.
    """
    return _judge(notes, move, basis, sessions)[0]


def explained_sessions(notes: list[dict], move: dict | None, basis: dict | None,
                       sessions: list[dict] | None = None) -> dict[str, dt.datetime]:
    """≥2σ session date → the time (New York) of the earliest note that is
    sound (OK or STALE — also one a later note has since superseded, as long
    as nothing else was wrong with it), says `move_cause: found`, and had
    that session in its news window. The research prompt does not ask for
    those causes again, and a later note is not failed for leaving them out.
    An INVALID note explains nothing."""
    return _judge(notes, move, basis, sessions)[1]


def last_research_time(notes: list[dict], move: dict | None, basis: dict | None,
                       sessions: list[dict] | None = None) -> dt.datetime | None:
    """When (New York time) the newest research note that still stands was
    written: OK or STALE — also one a later note has since superseded, as
    long as nothing else was wrong with it. An INVALID note is no research to
    start from (its searches were not finished, or it left a ≥2σ session
    without a cause). None when there is no such note."""
    sound = _judge(notes, move, basis, sessions)[2]
    times = [t for t in (_parse_ts(n.get("created_ts")) for n, ok in zip(notes, sound) if ok)
             if t is not None]
    return max(times) if times else None


def last_research_date(notes: list[dict], move: dict | None, basis: dict | None,
                       sessions: list[dict] | None = None) -> str | None:
    """`last_research_time` as an ISO date — where the next prompt's news
    window starts (news_relevance.news_window), so the days since the last
    sound note are researched rather than only the last 5 sessions."""
    t = last_research_time(notes, move, basis, sessions)
    return t.date().isoformat() if t else None


def older_unexplained_sessions(notes: list[dict], move: dict | None, basis: dict | None,
                               sessions: list[dict] | None, window_from: str | None
                               ) -> list[dict]:
    """≥2σ sessions that closed after the newest sound note was written (they
    are why it reads STALE), lie BEFORE the news window that starts at
    `window_from`, and whose cause no sound note states — oldest first.

    They exist only when the window is capped (the note is older than
    news_relevance.MAX_WINDOW_DAYS): sessions inside the window are asked for
    in step 1 as required, these as a one-line cause each. A note is not
    failed for leaving them out — they are outside its window, so
    `sessions_to_explain` never returns them. Sessions before the newest sound
    note are not listed: that note's own prompt asked for them."""
    if not window_from:
        return []
    out, explained, sound = _judge(notes, move, basis, sessions)
    times = [t for t in (_parse_ts(n.get("created_ts")) for n, ok in zip(notes, sound) if ok)
             if t is not None]
    if not times:
        return []
    big = big_sessions(move, basis, sessions)
    return [s for s in sessions_closed_after(max(times), big)
            if s["session_date"] < str(window_from)[:10]
            and s["session_date"] not in explained]


def older_failed_sessions(notes: list[dict], move: dict | None, basis: dict | None,
                          sessions: list[dict] | None, window_from: str | None
                          ) -> list[dict]:
    """≥2σ sessions inside the window of a note that is not sound (INVALID,
    superseded or not) which lie BEFORE the current news window and whose
    cause no sound note states — oldest first.

    The prompt told the session to "redo the work" of an INVALID note; when
    that note's failed check was on a session months back, outside the
    30-day window, the session could not tell whether to widen the window
    (ISTR run). The redo now covers the window only, and these sessions are
    named in step 1 as older sessions still unexplained — optional, like
    `older_unexplained_sessions`; they fail no note."""
    if not window_from:
        return []
    _, explained, sound = _judge(notes, move, basis, sessions)
    big = big_sessions(move, basis, sessions)
    last = (basis or {}).get("session_date")
    out: dict[str, dict] = {}
    for n, ok in zip(notes, sound):
        if ok:
            continue
        for s in sessions_to_explain(_parse_ts(n.get("created_ts")), big,
                                     _meta(n).get("news_window"), last):
            d = s["session_date"]
            if d < str(window_from)[:10] and d not in explained:
                out[d] = s
    return [out[d] for d in sorted(out)]


# ─── which note is shown ───────────────────────────────────────────────────────

def pick_stance(rows: list[dict]) -> tuple[dict | None, list[str]]:
    """(the stance to show, flags when there is none to show) from one
    ticker's discussion rows, NEWEST FIRST, `research_meta` already a dict.

    The stance is the newest note that carries a readable one. Three things
    a NEWER note can do to it are attached so they are flagged instead of
    hidden (stance_flags):
      * `superseded_by` — a later note lists this note in `supersedes`
      * `newer_unreadable` — a later note has a stance the app could not read
      * `newer_no_stance` — a later prompt-v5 note carries no stance at all
        (the key was deleted or left as the template placeholder)
    """
    def bad_stance(r: dict) -> str | None:
        return _unreadable(r).get("stance")

    at = next((i for i, r in enumerate(rows) if r.get("stance")), None)
    newer = rows if at is None else rows[:at]
    unreadable = next(({"created_ts": r["created_ts"], "value": bad_stance(r)}
                       for r in newer if bad_stance(r)), None)
    if at is None:
        if not unreadable:
            return None, []
        return None, [f"stance unreadable: '{unreadable['value']}' in the note of "
                      f"{str(unreadable['created_ts'])[:16]} — no stance is shown"]
    out = dict(rows[at])
    key = _ts_key(out["created_ts"])
    by = next((r["created_ts"] for r in newer
               if key in {_ts_key(x) for x in _meta(r).get("supersedes") or []}), None)
    if by:
        out["superseded_by"] = by
    if unreadable:
        out["newer_unreadable"] = unreadable
    blank = next((r["created_ts"] for r in newer
                  if is_v5(r) and not bad_stance(r) and r["created_ts"] != by), None)
    if blank:
        out["newer_no_stance"] = blank
    return out, []


def pick_news(rows: list[dict]) -> tuple[dict | None, list[str]]:
    """(the news check to show, flags about the choice) from one ticker's
    discussion rows, NEWEST FIRST. The check is the newest note with a
    readable `news_view`. When the newest note has none — absent, or a word
    the parser could not read — the older check is still shown, and a flag
    says it is not the newest note's."""
    at = next((i for i, r in enumerate(rows) if r.get("news_view")), None)
    if not rows or at == 0:
        return (dict(rows[0]) if rows else None), []
    newest = rows[0]
    when = str(newest.get("created_ts"))[:16]
    raw = _unreadable(newest).get("news_view")
    if at is None:
        return None, ([f"news_view unreadable: '{raw}' in the note of {when} — no news "
                       "check is shown"] if raw else [])
    why = (f"news_view unreadable: '{raw}' in the note of {when}" if raw else
           f"the newest note ({when}) carries no news_view")
    return dict(rows[at]), [f"{why} — the news check shown is from an older note"]


# ─── the shown stance ──────────────────────────────────────────────────────────

def _reference_prices(note_ts: dt.datetime | None, note_date: str, bars: list[dict]
                      ) -> tuple[list[float], tuple[float, float] | None, str, float] | None:
    """(reference prices, live range, bar date, tolerance) for a note written
    at `note_ts` (New York time) — the prices it could legitimately have used.

      * market closed (weekend, holiday, before the open, after the close):
        the report price is the last official close, so only that close
        counts, within PRICE_TOLERANCE_CLOSED. An after-hours quote 2–3% away
        is not the report price.
      * written while the session traded: a live price was legitimate — that
        day's close or range, or the prior close — within PRICE_TOLERANCE.
    None when the bars cannot settle it: no bar on or before the note's date,
    or the note's own session has no bar yet."""
    prior = [b for b in bars if str(b.get("date") or "") <= note_date and b.get("close")]
    if not prior:
        return None
    bar = prior[-1]
    same_day = str(bar["date"]) == note_date
    if note_ts is None:                       # date only: the day's close, loosely
        return [float(bar["close"])], None, str(bar["date"]), PRICE_TOLERANCE
    state = mc.session_state(note_ts)["state"]
    if state == "open":
        if not same_day:
            return None                       # that session's bar is not in yet
        refs = [float(bar["close"])] + ([float(prior[-2]["close"])] if len(prior) >= 2 else [])
        rng = ((float(bar["low"]), float(bar["high"]))
               if bar.get("low") and bar.get("high") else None)
        return refs, rng, str(bar["date"]), PRICE_TOLERANCE
    if state == "pre" and same_day:           # before the open: the previous close
        if len(prior) < 2:
            return None
        bar = prior[-2]
    elif state == "post" and not same_day:
        return None                           # closed today, bar not published yet
    return [float(bar["close"])], None, str(bar["date"]), PRICE_TOLERANCE_CLOSED


def stance_flags(stance: dict | None, bars: list[dict] | None = None,
                 today: dt.date | None = None) -> list[str]:
    """Warnings for the stance shown beside the ticker. `stance` is the
    discussion row (stance, created_ts, research_meta, and — set by
    pick_stance and the composer — `superseded_by` / `newer_unreadable` /
    `newer_no_stance` / `account_owned`); `bars` are completed daily bars;
    `today` is injectable."""
    if not stance or not stance.get("stance"):
        return []
    m = _meta(stance)
    unread = m.get("unreadable") if isinstance(m.get("unreadable"), dict) else {}
    out: list[str] = []
    word = str(stance["stance"]).lower()

    if stance.get("superseded_by"):
        out.append(f"superseded by the note of {str(stance['superseded_by'])[:16]}, which "
                   "carries no readable stance — re-run research")
    nu = stance.get("newer_unreadable") or {}
    if nu.get("value"):
        out.append(f"stance unreadable: '{nu['value']}' in the note of "
                   f"{str(nu.get('created_ts'))[:16]} — the stance shown is from an older note")
    if stance.get("newer_no_stance"):
        out.append(f"newer note of {str(stance['newer_no_stance'])[:16]} carries no stance "
                   "— the stance shown is from an older note")
    if m.get("owned") == "no" and stance.get("account_owned") is True:
        out.append("the note says not owned (owned: no) but the account holds this "
                   "position — the stance was written for a name not held; re-run research")

    if m.get("research_status") in ("partial", "failed"):
        out.append(f"research incomplete (research_status: {m['research_status']}) — "
                   "the stance rests on an unfinished news check")

    if word in NEEDS_CONDITIONS:
        missing = [k for k in ("upgrade_if", "downgrade_if") if not m.get(k)]
        if missing:
            out.append(f"{word} with no conditions — treat as no stance "
                       f"(missing: {', '.join(missing)})")

    rb = m.get("review_by") or unread.get("review_by")
    if rb:
        due = parse_date(rb)
        if due is None:
            out.append(f"review_by unreadable: '{rb}' — the stance has no review date")
        elif due < (today or dt.date.today()):
            out.append(f"expired — review_by {due.isoformat()} has passed; re-run research")

    if m.get("move_cause") == "not_found":
        out.append("the session did not find the cause of a ≥2σ move (move_cause: not_found)")

    if unread.get("price_used"):
        out.append(f"price_used unreadable: '{unread['price_used']}'")
    if m.get("price_used_note") == "extended-hours":
        out.append("price_used is an extended-hours price — the report price is the "
                   "official close")
    try:
        pu = float(m["price_used"]) if m.get("price_used") not in (None, "") else None
    except (TypeError, ValueError):
        pu = None
    note_ts = _parse_ts(stance.get("created_ts"))
    note_date = (note_ts.date().isoformat() if note_ts
                 else str(stance.get("created_ts") or "")[:10])
    if pu and bars and note_date:
        got = _reference_prices(note_ts, note_date, bars)
        if got:
            refs, rng, bar_date, tolerance = got
            inside = rng is not None and rng[0] <= pu <= rng[1]
            off = min(abs(pu / r - 1) for r in refs)
            if not inside and off > tolerance:
                out.append(f"price_used ${pu:,.2f} is {off * 100:.1f}% away from the "
                           f"{bar_date} session close ${refs[0]:,.2f} — the note was not "
                           "computed at the report price")

    if is_v5(stance):
        missing = [k for k in V5_REQUIRED if m.get(k) in (None, "") and k not in unread]
        if missing:
            out.append(f"missing required field(s): {', '.join(missing)} — the note "
                       "does not follow prompt v5")
    bad = [f"{k} '{v}'" for k, v in unread.items()
           if k not in ("stance", "price_used", "review_by", "news_view")]
    if bad:
        out.append("unreadable field(s): " + ", ".join(bad))

    met = m.get("plan_triggers_met") or []
    if met:
        out.append("trigger(s) met: " + "; ".join(str(x) for x in met))
    return out


def stance_conditions(stance: dict | None) -> dict[str, Any]:
    """The checkable part of a stance, for display under it (report strip,
    header tooltip). Empty lists / None when the note did not carry them."""
    m = _meta(stance)
    return {"upgrade_if": list(m.get("upgrade_if") or []),
            "downgrade_if": list(m.get("downgrade_if") or []),
            "review_by": m.get("review_by"),
            "confidence": m.get("stance_confidence"),
            "research_status": m.get("research_status"),
            "plan_triggers_met": list(m.get("plan_triggers_met") or []),
            "plan_triggers_unknown": list(m.get("plan_triggers_unknown") or [])}
