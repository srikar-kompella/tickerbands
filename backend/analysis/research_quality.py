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
        # a v6 note's stance is derived from its verdict: an unreadable
        # verdict is an unreadable stance
        u = _unreadable(r)
        return u.get("stance") or (u.get("verdict") if not r.get("stance") else None)

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

    v6 = is_v6(stance)
    if v6:
        out.extend(verdict_flags(stance))
    elif word in NEEDS_CONDITIONS:
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

    if v6:
        pass                      # required fields: verdict_flags (v6 list)
    elif is_v5(stance):
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


# ─── prompt v6: the verdict (docs/VERDICT_SPEC.md) ─────────────────────────────

VERDICT_STEPS = ("attractive", "fair", "stretched", "avoid")     # best → worst
BASELINE = {"cheap": "attractive", "fair": "fair", "expensive": "stretched"}
# the legacy stance a v6 verdict stands for (§7; the parser derives it)
VERDICT_TO_STANCE = {"attractive": "buy", "fair": "hold", "stretched": "trim",
                     "avoid": "sell", "unrated": "watch"}
VERDICT_WORD = {"attractive": "Attractive", "fair": "Fair", "stretched": "Stretched",
                "avoid": "Avoid", "unrated": "Unrated"}
VALUE_WORD = {"cheap": "Cheap", "fair": "Fair", "expensive": "Expensive",
              "paused": "Value paused"}
RISK_WORD = {"clean": "Clean", "watch": "Watch", "red_flag": "Red flag"}
# §5 — what the verdict means for this user: (owns it, does not own it)
OWNER_LINES = {"attractive": ("Room to add", "Candidate to buy"),
               "fair": ("Hold", "Wait"),
               "stretched": ("Consider trimming", "Pass"),
               "avoid": ("Consider exiting", "Pass"),
               "unrated": ("Do not act on it", "Do not act on it")}
# §9 — the trust label beside each note's internal status
TRUST = {OK: "current", STALE: "out_of_date", INVALID: "dont_use", SUPERSEDED: "replaced"}
TRUST_LABEL = {"current": "Current", "out_of_date": "Out of date", "dont_use": "Don't use",
               "replaced": "Replaced"}
# required in a v6 note (§7); upgrade_if / downgrade_if / review_by unless unrated
V6_REQUIRED = ("verdict", "value_grade", "risk_grade", "value_basis",
               "research_status", "price_used", "stance_confidence")
V6_CONDITIONS = ("upgrade_if", "downgrade_if", "review_by")
# words in value_basis that say why the grade departs from the app's
# arithmetic (§4) — departure words only; a two-step departure (cheap where
# the app's grade is expensive, or the reverse) is flagged whatever it says
_BASIS_REASON = re.compile(
    r"normali[sz]|one[- ]time|non[- ]recurring|peer|instead of|rather than|depart|override",
    re.I)
GRADE_ORDER = ("cheap", "fair", "expensive")        # most → least favorable
# soft-signal rule keys (§3): the session starts each soft_signals entry with
# one in brackets, e.g. "[ni_over_fcf] …"
_RULE_KEY = re.compile(r"^\s*\[([a-z0-9_]+)\]")


def is_v6(row: dict | None) -> bool:
    """Written from prompt v6 or later: `prompt_version` ≥ 6, or the note
    carries `verdict`, `value_grade` or `risk_grade` (readable or not)."""
    m = _meta(row)
    try:
        if int(m.get("prompt_version") or 0) >= 6:
            return True
    except (TypeError, ValueError):
        pass
    unread = _unreadable(row)
    return any(k in m or k in unread for k in ("verdict", "value_grade", "risk_grade"))


def _items(m: dict, key: str) -> list[str]:
    v = m.get(key)
    return [str(x) for x in v] if isinstance(v, (list, tuple)) else []


def capped(row: dict | None) -> bool:
    """§3 new-listing cap: a stock or bank listed under a year
    (`listed_under_a_year`, < 252 completed sessions; `is_new_listing`
    when the row predates that key) with no public quarter filed yet
    (`public_quarter_filed` not True). Funds have no cap."""
    r = row or {}
    listed = r.get("listed_under_a_year")
    if listed is None:
        listed = r.get("is_new_listing")
    if not listed or r.get("security_kind") == "fund":
        return False
    return r.get("public_quarter_filed") is not True


def _expected(meta: dict | None, is_new_listing: bool = False) -> tuple[str, str]:
    """(expected verdict, the inputs it follows from) — §4, in this order,
    first match wins:
      1. research_status failed and no hard flag → unrated
      2. any hard flag → avoid (even when research failed or value is paused)
      3. three or more soft signals → avoid
      4. value_grade paused, missing or unreadable → unrated (no baseline)
      5. baseline from value_grade (cheap → attractive, fair → fair,
         expensive → stretched); two soft signals → one step worse
      6. new-listing cap (`is_new_listing` here = the cap applies, see
         `capped`): nothing better than fair
    Watch items never change it."""
    m = meta if isinstance(meta, dict) else {}
    hard, soft = len(_items(m, "hard_flags")), len(_items(m, "soft_signals"))
    vg = m.get("value_grade")
    why = (f"value_grade {vg or 'missing'}, {hard} hard flag(s), {soft} soft signal(s)"
           + (", listed under a year" if is_new_listing else ""))
    if m.get("research_status") == "failed" and not hard:
        return "unrated", "research_status failed and no hard flag"
    if hard:
        return "avoid", why
    if soft >= 3:
        return "avoid", why
    if vg not in BASELINE:
        return "unrated", why
    v = BASELINE[vg]
    if soft == 2:
        v = VERDICT_STEPS[min(VERDICT_STEPS.index(v) + 1, len(VERDICT_STEPS) - 1)]
    if is_new_listing and v == "attractive":
        v = "fair"
    return v, why


def expected_verdict(meta: dict | None, is_new_listing: bool = False) -> str:
    """The verdict the rulebook gives (docs/VERDICT_SPEC.md §4) from the
    note's research_meta: value_grade, the counts of hard_flags and
    soft_signals, research_status, and the new-listing cap."""
    return _expected(meta, is_new_listing)[0]


def expected_risk(meta: dict | None) -> str:
    """§8: any hard flag → red_flag; 3+ soft → red_flag; 2 soft → watch;
    0–1 soft with one or more watch items → watch; else clean."""
    m = meta if isinstance(meta, dict) else {}
    hard, soft = len(_items(m, "hard_flags")), len(_items(m, "soft_signals"))
    if hard or soft >= 3:
        return "red_flag"
    if soft == 2 or _items(m, "watch_items"):
        return "watch"
    return "clean"


def _accepted_verdicts(meta: dict, is_new_listing: bool) -> set[str]:
    v = _expected(meta, is_new_listing)[0]
    out = {v}
    # "too little clean data → unrated" (a required search not run, a cause
    # still missing) is the session's call the app cannot re-derive: accepted
    # when research is incomplete (research_status partial) — never over a
    # hard flag or 3+ soft signals (§4 order: those give avoid first)
    if (meta.get("research_status") == "partial" and v != "avoid"):
        out.add("unrated")
    return out


def _note_prefix_flags(m: dict) -> list[str]:
    """§4: an unrated verdict_note starts "research incomplete" only when
    research is incomplete, and "value paused" when the value grade is
    paused."""
    if m.get("verdict") != "unrated":
        return []
    note = str(m.get("verdict_note") or "").strip().lower()
    incomplete = m.get("research_status") in ("partial", "failed")
    out = []
    if note.startswith("research incomplete") and not incomplete:
        out.append("verdict_note starts \"research incomplete\" but research_status is "
                   f"{m.get('research_status') or 'missing'} — say why it is unrated instead")
    if (m.get("value_grade") == "paused" and not incomplete
            and not note.startswith("value paused")):
        out.append("unrated with value_grade paused — verdict_note should start "
                   "\"value paused\" and say which input is missing")
    return out


def verdict_flags(row: dict | None) -> list[str]:
    """The v6 checks (§8) for the shown note: verdict vs the rulebook,
    risk_grade vs the lists, value_grade vs the app's valuation range
    (`valuation_range` attached by the composer), soft signals vs the app's
    `rule_inputs` when attached, required fields, and the conditions every
    verdict but unrated needs. The new-listing cap follows `capped(row)`
    (`listed_under_a_year`, `security_kind`, `public_quarter_filed`)."""
    if not is_v6(row):
        return []
    r = row or {}
    m, unread = _meta(r), _unreadable(r)
    out: list[str] = []
    verdict = m.get("verdict")
    new = capped(r)

    missing = [k for k in V6_REQUIRED if m.get(k) in (None, "") and k not in unread]
    if missing:
        out.append(f"missing required field(s): {', '.join(missing)} — the note does not "
                   "follow prompt v6")
    if verdict and verdict != "unrated":
        cond = [k for k in V6_CONDITIONS if not m.get(k) and k not in unread]
        if cond:
            out.append(f"{verdict} with no conditions — every verdict except unrated needs "
                       f"upgrade_if, downgrade_if and review_by (missing: {', '.join(cond)})")

    legacy = VERDICT_TO_STANCE.get(verdict)
    if (legacy and r.get("stance") and not m.get("stance_derived")
            and str(r["stance"]).lower() != legacy):
        out.append(f"the note writes stance {r['stance']} beside verdict {verdict} (which "
                   f"stands for {legacy}) — v6 notes write the verdict only")
    if verdict and "value_grade" not in unread:
        exp, why = _expected(m, new)
        if verdict not in _accepted_verdicts(m, new):
            out.append(f"verdict does not follow the rulebook: expected {exp} from {why} "
                       f"— the note says {verdict}")
    rg = m.get("risk_grade")
    if rg:
        exp_r = expected_risk(m)
        if rg != exp_r:
            n_h, n_s = len(_items(m, "hard_flags")), len(_items(m, "soft_signals"))
            n_w = len(_items(m, "watch_items"))
            out.append(f"risk_grade does not match the lists: expected {exp_r} from {n_h} hard "
                       f"flag(s), {n_s} soft signal(s), {n_w} watch item(s) — the note says {rg}")
    out.extend(_note_prefix_flags(m))

    if isinstance(r.get("valuation_range"), dict):
        out.extend(value_grade_flags(r, r["valuation_range"]))
    if isinstance(r.get("rule_inputs"), dict):
        out.extend(rule_input_flags(r, r["rule_inputs"]))
    return out


def combined_grade(grade_hint: str | None, normalized_grade_hint: str | None) -> str | None:
    """§4: the less favorable of the reported and the normalized grade
    (cheap + fair → fair; fair + expensive → expensive; cheap + expensive →
    expensive); the reported grade alone without normalization."""
    gs = [g for g in (grade_hint, normalized_grade_hint) if g in GRADE_ORDER]
    if not gs:
        return None
    return max(gs, key=GRADE_ORDER.index)


def value_grade_flags(row: dict | None, valuation_range: dict | None) -> list[str]:
    """§4/§6/§8: a v6 `value_grade` should equal the app's
    `valuation_range.grade` (the less favorable of `grade_hint` and
    `normalized_grade_hint`). A different grade is flagged unless
    `value_basis` gives a departure reason (normalized, one-time,
    non-recurring, peer, instead of, rather than, depart, override); a
    two-step departure (cheap vs expensive) is flagged whatever it says.
    Nothing to check when the range has no grade (fewer than three points,
    withheld) or the note's grade is paused."""
    if not is_v6(row) or not isinstance(valuation_range, dict):
        return []
    m = _meta(row)
    vr = valuation_range
    vg = m.get("value_grade")
    hint, n_hint = vr.get("grade_hint"), vr.get("normalized_grade_hint")
    grade = vr.get("grade") or combined_grade(hint, n_hint)
    if not vg or vg not in GRADE_ORDER or not grade or vg == grade:
        return []
    two_step = abs(GRADE_ORDER.index(vg) - GRADE_ORDER.index(grade)) == 2
    if not two_step and _BASIS_REASON.search(str(m.get("value_basis") or "")):
        return []
    nums = ""
    if vr.get("now") is not None and vr.get("median") is not None:
        nums = (f": {vr.get('label') or vr.get('measure')} now {vr['now']} vs five-year "
                f"median {vr['median']}, high {vr.get('high')}")
        if n_hint and vr.get("now_normalized") is not None:
            nums += f"; on the five-year average {vr['now_normalized']}"
    hints = f"grade {grade}" + (f" (reported {hint}, normalized {n_hint})" if n_hint else "")
    if two_step:
        return [f"value_grade {vg} is two steps from the app's valuation range ({hints}"
                f"{nums}) — a two-step departure is not allowed"]
    return [f"value_grade {vg} differs from the app's valuation range ({hints}"
            f"{nums}) and value_basis gives no departure reason"]


def soft_signal_keys(meta: dict | None) -> set[str]:
    """The rule keys the session wrote in brackets at the start of its
    soft_signals entries ("[ni_over_fcf] …")."""
    m = meta if isinstance(meta, dict) else {}
    out = set()
    for s in _items(m, "soft_signals"):
        mt = _RULE_KEY.match(s)
        if mt:
            out.add(mt.group(1).lower())
    return out


def rule_input_flags(row: dict | None, rule_inputs: dict | None) -> list[str]:
    """§8: a soft_signals entry whose [key] the app computes as not met
    (`fires: false`), or no entry for a key the app computes as met
    (`fires: true`) → "soft signal X does not match the app's arithmetic".
    `fires: null` is never checked. Skipped when the statements behind
    `rule_inputs` were filed after the note was written (`filed` later than
    the note's date) — the note could not have seen them — and when the
    filed quarters are one behind the latest results (`behind_release`: the
    newest quarter is only in the earnings release, which the session read
    and the arithmetic has not)."""
    if not is_v6(row) or not isinstance(rule_inputs, dict):
        return []
    rules = [r for r in rule_inputs.get("rules") or [] if isinstance(r, dict) and r.get("key")]
    if not rules:
        return []
    filed = str(rule_inputs.get("filed") or "")[:10]
    note_day = str((row or {}).get("created_ts") or "")[:10]
    if filed and note_day and filed > note_day:
        return []
    if rule_inputs.get("behind_release"):
        return []
    m = _meta(row)
    if _unreadable(row).get("soft_signals"):
        return []
    written = soft_signal_keys(m)
    out = []
    for r in rules:
        k, fires = str(r["key"]), r.get("fires")
        vals = r.get("summary") or ""
        if fires is False and k in written:
            out.append(f"soft signal {k} does not match the app's arithmetic: the app computes "
                       f"it as not met ({r.get('threshold') or ''}"
                       f"{'; ' + vals if vals else ''})")
        elif fires is True and k not in written:
            out.append(f"soft signal {k} does not match the app's arithmetic: the app computes "
                       f"it as met ({r.get('threshold') or ''}{'; ' + vals if vals else ''}) "
                       f"and soft_signals has no [{k}] entry")
    return out


def verdict_label(row: dict | None) -> str | None:
    """`Stretched (Expensive · Watch)` — §1. None when the note has no
    readable verdict; a missing grade is left out of the brackets."""
    m = _meta(row)
    v = m.get("verdict")
    if v not in VERDICT_WORD:
        return None
    parts = [w for w in (VALUE_WORD.get(m.get("value_grade")),
                         RISK_WORD.get(m.get("risk_grade"))) if w]
    return VERDICT_WORD[v] + (f" ({' · '.join(parts)})" if parts else "")


def _owned_for_line(row: dict | None, owned: bool | None) -> bool | None:
    if isinstance(owned, bool):
        return owned
    acct = (row or {}).get("account_owned")
    if isinstance(acct, bool):
        return acct
    o = _meta(row).get("owned")
    return {"yes": True, "no": False}.get(o)


def owner_line(row: dict | None, owned: bool | None = None) -> str | None:
    """What the verdict means for this user (§5), derived by the app.
    `owned`: True / False, or None → the account's view (`account_owned`,
    linked share classes count), else the note's `owned`, else both lines.
    The derived line only — the note's `owner_note` (the user's own rules)
    travels as its own field and is printed once, after this line."""
    v = _meta(row).get("verdict")
    if v not in OWNER_LINES:
        return None
    own, not_own = OWNER_LINES[v]
    o = _owned_for_line(row, owned)
    if o is True:
        return f"You own it: {own.lower()}"
    if o is False:
        return f"Not owned: {not_own.lower()}"
    return f"If you own it: {own.lower()} · if not: {not_own.lower()}"


def trust(status: str | None) -> str | None:
    """§9: OK → current, STALE → out_of_date, INVALID → dont_use,
    SUPERSEDED → replaced; None (not judged) → None."""
    return TRUST.get(status) if status else None


def verdict_fields(row: dict | None, owned: bool | None = None) -> dict[str, Any]:
    """The verdict keys the payload carries beside the legacy stance
    (claude_stance, Watchlist rows): `format` "v6" with the verdict, grades,
    label, lists and owner line; "earlier" (with nothing else) for a note
    from before v6."""
    if not row:
        return {}
    if not is_v6(row):
        return {"format": "earlier"}
    m = _meta(row)
    return {"format": "v6",
            "verdict": m.get("verdict"), "value_grade": m.get("value_grade"),
            "risk_grade": m.get("risk_grade"), "verdict_label": verdict_label(row),
            "value_basis": m.get("value_basis"), "verdict_note": m.get("verdict_note"),
            "hard_flags": _items(m, "hard_flags"), "soft_signals": _items(m, "soft_signals"),
            "watch_items": _items(m, "watch_items"),
            "owner_line": owner_line(row, owned), "owner_note": m.get("owner_note"),
            "expected_verdict": expected_verdict(m, capped(row)),
            "expected_risk": expected_risk(m)}
