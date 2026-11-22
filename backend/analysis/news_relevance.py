"""
analysis/news_relevance.py — keep only headlines that are about the company,
and judge whether a research note's news check can be trusted.

NVDA 11/21: the NEWS card listed Intel, Jim Cramer, an annuity article and
Anthropic, and the stored news check said "No material NVDA news today" the
day after a 14% drop that WSJ/Reuters tied to an SEC accounting probe. Two
failures: an unfiltered feed, and a same-day-only check that was never
challenged by the size of the move. Pure functions.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from analysis import market_clock as mc

# Extra names a headline may use for the company (brand ≠ legal name).
ALIASES = {
    "GOOGL": ["google", "alphabet"], "GOOG": ["google", "alphabet"],
    "META": ["facebook", "instagram", "meta platforms"],
    "BRK.B": ["berkshire"], "BRK.A": ["berkshire"],
    "NVDA": ["nvidia"], "TWLO": ["twilio"], "MNDY": ["monday.com"],
    "VST": ["vistra"], "NFLX": ["netflix"],
}
_SUFFIXES = {"inc", "inc.", "corp", "corp.", "corporation", "co", "co.", "ltd",
             "ltd.", "plc", "holdings", "holding", "group", "class", "a", "b",
             "c", "com", "the", "sa", "nv", "ag", "(mock)", "common", "stock",
             "shares", "ordinary", "lp", "llc", "trust", "etf"}

MIN_HEADLINES_FOR_SENTIMENT = 10
MIN_TAGGED_SOCIAL = 10
BIG_MOVE_SIGMA = 2.0

# "No material news" as a CONCLUSION about the company — not the log line of
# one empty search ("searched DOJ: no relevant results"), which is evidence of
# work done. The qualifier must be followed by a news word.
_NO_NEWS = re.compile(
    r"\bno (?:new |fresh )?(?:material|significant|major|notable|relevant|meaningful|"
    r"company[- ]specific)(?: [\w&.'-]+){0,2}? (?:news|headlines?|developments?|"
    r"catalysts?|events?|story|stories|updates?|announcements?)\b"
    r"|\bnothing (?:material|significant|notable)\b"
    r"|\bno (?:[\w&.'-]+ )?news\b", re.I)


def name_terms(symbol: str, name: str | None) -> list[str]:
    """Search terms for a company: ticker, cleaned legal name's leading
    word(s), and known aliases. 'NVIDIA CORP' → ['nvda', 'nvidia']."""
    terms = {symbol.lower()}
    words = [w for w in re.split(r"[\s,]+", (name or "").lower()) if w]
    core = [w for w in words if w not in _SUFFIXES]
    if core and len(core[0]) >= 3 and core[0] != symbol.lower():
        terms.add(core[0])
    terms.update(ALIASES.get(symbol.upper(), []))
    return sorted(terms)


def _mentions(text: str, symbol: str, terms: list[str]) -> int:
    n = 0
    for t in terms:
        if t == symbol.lower():
            n += len(re.findall(rf"(?<![a-z0-9$]){re.escape(t)}(?![a-z0-9])", text))
        else:
            n += len(re.findall(rf"(?<![a-z0-9]){re.escape(t)}", text))
    return n


def is_relevant(item: dict, symbol: str, terms: list[str]) -> bool:
    """About the company: named in the HEADLINE, or named at least twice in
    the summary. One passing mention in a summary let through "A $2.6B fund
    just bought these 3 stocks" and a SpaceX debt story (live NVDA, 11/21)."""
    head = (item.get("headline") or "").lower()
    if _mentions(head, symbol, terms):
        return True
    return _mentions((item.get("summary") or "").lower(), symbol, terms) >= 2


def spread_by_day(items: list[dict], per_day: int = 2, cap: int = 12) -> list[dict]:
    """Newest first, at most `per_day` per calendar day, so one busy day
    cannot crowd the whole window out of the list."""
    out, seen = [], {}
    for h in sorted(items, key=lambda x: int(x.get("ts") or 0), reverse=True):
        d = dt.datetime.fromtimestamp(int(h.get("ts") or 0)).date()
        if seen.get(d, 0) >= per_day:
            continue
        seen[d] = seen.get(d, 0) + 1
        out.append(h)
        if len(out) >= cap:
            break
    return out


def filter_headlines(items: list[dict] | None, symbol: str,
                     name: str | None) -> dict[str, Any]:
    items = items or []
    terms = name_terms(symbol, name)
    kept = [h for h in items if is_relevant(h, symbol, terms)]
    tss = [int(h.get("ts") or 0) for h in items if h.get("ts")]
    days = sorted({dt.datetime.fromtimestamp(int(h.get("ts") or 0)).date().isoformat()
                   for h in kept if h.get("ts")})
    return {
        "items": spread_by_day(kept),
        "all_relevant": kept,
        "fetched": len(items),
        "kept": len(kept),
        "dropped": len(items) - len(kept),
        "terms": terms,
        "days_covered": len(days),
        "window_from": (dt.datetime.fromtimestamp(min(tss)).date().isoformat()
                        if tss else None),
        "window_to": (dt.datetime.fromtimestamp(max(tss)).date().isoformat()
                      if tss else None),
    }


# ─── regulatory / legal pass + cause of a big move ────────────────────────────

REG_RE = re.compile(
    r"\b(SEC|Securities and Exchange Commission|DOJ|Justice Department|subpoena\w*|"
    r"probe\w*|investigat\w*|attorneys? general|class[- ]action|lawsuit\w*|sued|"
    r"export (?:control|rule|restriction|curb)s?|BIS|Commerce Department|antitrust|FTC|"
    r"restat\w*|auditor|accounting (?:issue|irregularit)\w*|whistleblower|indict\w*|"
    r"settle\w*|fine[ds]?|penalt\w*|recall\w*)\b", re.I)
CAUSE_RE = re.compile(
    REG_RE.pattern[:-3] + r"|downgrad\w*|upgrad\w*|guidance|outlook|earnings|results|"
    r"miss\w*|beat\w*|CEO|CFO|resign\w*|ousted|steps down|acquisi\w*|merger|deal|"
    r"offering|dilut\w*|tariff\w*|ban\w*|plunge\w*|tumbl\w*|sink\w*|slump\w*|"
    r"sell-?off|soar\w*|surg\w*|rall\w*)\b", re.I)

# 8-K items worth naming; the flagged ones are red flags by definition
EIGHT_K_ITEMS = {
    "1.01": "material agreement", "1.02": "agreement terminated",
    "1.05": "cybersecurity incident", "2.01": "acquisition/disposition",
    "2.02": "results of operations", "2.03": "new debt obligation",
    "2.05": "exit/restructuring costs", "2.06": "material impairment",
    "3.01": "delisting notice", "3.02": "unregistered share sale",
    "4.01": "auditor change", "4.02": "non-reliance on prior financials (restatement)",
    "5.02": "director/officer change", "5.07": "shareholder vote",
    "7.01": "Reg FD disclosure", "8.01": "other events", "9.01": "exhibits",
}
RED_FLAG_ITEMS = {"1.05", "2.06", "3.01", "4.01", "4.02"}


def describe_8k(items: str) -> tuple[str, bool]:
    codes = [c.strip() for c in (items or "").split(",") if c.strip()]
    names = [f"{c} {EIGHT_K_ITEMS.get(c, '')}".strip() for c in codes if c != "9.01"]
    return (", ".join(names) or "exhibits only"), any(c in RED_FLAG_ITEMS for c in codes)


def regulatory_scan(relevant: list[dict], since: str) -> list[dict]:
    """Relevant headlines since `since` that mention a regulator, a probe, a
    lawsuit, export rules, a restatement or similar."""
    out = []
    for h in relevant:
        d = dt.datetime.fromtimestamp(int(h.get("ts") or 0)).date().isoformat()
        if d < since[:10]:
            continue
        text = f"{h.get('headline') or ''} {h.get('summary') or ''}"
        m = sorted({x.group(0).lower() for x in REG_RE.finditer(text)})
        if m:
            out.append({"date": d, "headline": h.get("headline"), "source": h.get("source"),
                        "terms": m, "url": h.get("url")})
    return sorted(out, key=lambda x: x["date"], reverse=True)


def move_cause(relevant: list[dict], filings: list[dict], session_date: str | None,
               z: float | None) -> dict[str, Any] | None:
    """For a ≥2σ session: reported causes found in relevant headlines dated
    the day before through the day after, plus same-window 8-Ks. None when
    the move was ordinary."""
    if z is None or abs(z) < BIG_MOVE_SIGMA or not session_date:
        return None
    d = dt.date.fromisoformat(session_date)
    lo, hi = (d - dt.timedelta(days=1)).isoformat(), (d + dt.timedelta(days=1)).isoformat()
    found = []
    for h in relevant:
        hd = dt.datetime.fromtimestamp(int(h.get("ts") or 0)).date().isoformat()
        if lo <= hd <= hi:
            text = f"{h.get('headline') or ''} {h.get('summary') or ''}"
            if CAUSE_RE.search(text):
                found.append({"date": hd, "headline": h.get("headline"),
                              "source": h.get("source"),
                              "regulatory": bool(REG_RE.search(text))})
    # regulatory/legal candidates first, then newest
    found = sorted(found, key=lambda x: (0 if x["regulatory"] else 1, -int(x["date"].replace("-", ""))))
    fil = [f for f in filings if lo <= f.get("date", "") <= hi]
    return {"found": found[:6], "filings": fil, "session_date": session_date,
            "status": "candidates found" if (found or fil) else "cause not found"}


def _parse_ts(s: str | None) -> dt.datetime | None:
    """A discussion's `created_ts` → New York time. The app writes it with
    datetime.now(), so a naive value is the user's machine-local time and is
    converted (market_clock.local_to_ny); a value that carries a UTC offset
    is converted from that offset."""
    if not s:
        return None
    txt = str(s).strip().replace(" ", "T", 1)
    if txt.endswith(("Z", "z")):
        txt = txt[:-1] + "+00:00"
    for cand in (txt, txt[:19]):
        try:
            return mc.local_to_ny(dt.datetime.fromisoformat(cand))
        except ValueError:
            continue
    return None


def session_close_dt(session_iso: str) -> dt.datetime:
    d = dt.date.fromisoformat(session_iso)
    return mc.to_ny(dt.datetime.combine(d, mc.close_time(d)))


# ─── ≥2σ sessions a note is judged against ────────────────────────────────────

def big_sessions(move: dict | None, basis: dict | None,
                 sessions: list[dict] | None = None) -> list[dict]:
    """Every COMPLETED session that moved ≥ 2σ, oldest first.

    `sessions` come from the daily bars (research_quality.big_move_sessions),
    so a note stays judged against a big session after later, calmer ones —
    judging against the last session only let a failed news check return to
    OK one ordinary day later. The report's own last session (`move` +
    `basis`) overrides the bar-derived entry for its date, because its z is
    the one the report shows; a session that is still trading is left out."""
    by: dict[str, dict] = {}
    for s in sessions or []:
        d, z = str(s.get("session_date") or "")[:10], s.get("z")
        if d and z is not None and abs(z) >= BIG_MOVE_SIGMA:
            by[d] = {**s, "session_date": d}
    sess = (basis or {}).get("session_date")
    z = (move or {}).get("z")
    if sess:
        live = (basis or {}).get("price_kind") == "live"
        if live or (z is not None and abs(z) < BIG_MOVE_SIGMA):
            by.pop(sess, None)
        elif z is not None:
            by[sess] = {"session_date": sess, "z": z, "move_pct": (move or {}).get("move_pct"),
                        "label": (basis or {}).get("move_word")}
    return [by[k] for k in sorted(by)]


def sessions_closed_after(t: dt.datetime | None, big: list[dict]) -> list[dict]:
    """The ≥2σ sessions that closed after a note written at `t` — moves it
    cannot reflect. Empty when the time is unknown."""
    if t is None:
        return []
    return [s for s in big if t < session_close_dt(s["session_date"])]


WINDOW_SESSIONS = 5      # the prompt's news window is never shorter than 5 sessions


def parse_window(text: Any) -> tuple[str, str] | None:
    """A note's `news_window` ("YYYY-MM-DD..YYYY-MM-DD") → (from, to)."""
    m = re.match(r"\s*(\d{4}-\d{2}-\d{2})\s*(?:\.{2,}|→|->|–|—|to)\s*(\d{4}-\d{2}-\d{2})\s*$",
                 str(text or ""))
    if not m:
        return None
    try:
        lo, hi = dt.date.fromisoformat(m.group(1)), dt.date.fromisoformat(m.group(2))
    except ValueError:
        return None
    return (lo.isoformat(), hi.isoformat()) if lo <= hi else None


def sessions_to_explain(t: dt.datetime | None, big: list[dict], window: Any = None,
                        last_session: str | None = None) -> list[dict]:
    """The ≥2σ sessions a note written at `t` had to explain, oldest first:
    every one that had closed by then and lies inside its news window.

    The window is the last WINDOW_SESSIONS sessions that had closed when the
    note was written — the minimum the research prompt sets — and reaches
    further back when the note's own `news_window` starts earlier. A stated
    window can widen it and cannot narrow it, so a note that checked one day
    is still held to the whole week. Judging only the LAST closed session let
    a "no material news" note through one ordinary day after a −14% session.
    With no readable time the note is taken as current (`last_session`)."""
    if t is not None:
        last = mc.session_state(t)["last_session"]
    elif last_session:
        try:
            last = dt.date.fromisoformat(str(last_session)[:10])
        except ValueError:
            return []
    else:
        return []
    lo = last
    for _ in range(WINDOW_SESSIONS - 1):
        lo = mc.previous_trading_day(lo)
    lo_iso = lo.isoformat()
    w = parse_window(window)
    if w:
        lo_iso = min(lo_iso, w[0])
    return [s for s in big if lo_iso <= s["session_date"] <= last.isoformat()]


def _meta_of(row: dict | None) -> dict:
    m = (row or {}).get("research_meta")
    return m if isinstance(m, dict) else {}


# keys only a prompt-v5 note carries (read or not): any of them marks the note
# as v5 even when the `prompt_version` line was deleted
V5_MARKERS = ("move_cause", "research_status", "news_window")


def is_v5(row: dict | None) -> bool:
    """Written from prompt v5 or later: `prompt_version` ≥ 5, or the note
    carries a key only v5 has — `move_cause`, `research_status` or
    `news_window`, readable or not. Trusting the `prompt_version` line alone
    let a note drop that one line and be judged by the lenient rules for
    notes from before v5 (wording only, no `move_cause` required)."""
    m = _meta_of(row)
    try:
        if int(m.get("prompt_version") or 0) >= 5:
            return True
    except (TypeError, ValueError):
        pass
    unread = m.get("unreadable") if isinstance(m.get("unreadable"), dict) else {}
    return (any(m.get(k) not in (None, "") or unread.get(k) for k in V5_MARKERS)
            or bool((row or {}).get("move_cause")))


def stated_cause(row: dict | None) -> str | None:
    return (row or {}).get("move_cause") or _meta_of(row).get("move_cause")


def cause_text(row: dict | None) -> str:
    """How a note's `move_cause` reads in a reason or a flag."""
    cause = stated_cause(row)
    if cause:
        return f"move_cause: {cause}"
    unread = _meta_of(row).get("unreadable")
    raw = unread.get("move_cause") if isinstance(unread, dict) else None
    return f"move_cause unreadable: '{raw}'" if raw else "move_cause absent"


def failed_news_check(row: dict, t: dt.datetime | None, big: list[dict],
                      last_session: str | None = None,
                      explained: dict[str, dt.datetime] | None = None
                      ) -> tuple[list[dict], str] | None:
    """(sessions, how) when a note's news check left a ≥2σ session it had to
    explain without a cause; None when it did not.

    `explained` maps a session date to the time of the earliest sound note
    that stated its cause (research_quality.explained_sessions); a session
    explained BEFORE this note was written is not asked of it again.

    how:
      * "not_found"  — the note says `move_cause: not_found`
      * "not_stated" — a prompt-v5 note with anything other than
        `move_cause: found` (na, absent, unreadable). The structured field
        decides; wording such as "broad tech selloff, no obvious company
        catalyst identified" does not pass as a cause.
      * "no_news"    — a note from before v5 whose text concludes there was
        no material news (_NO_NEWS). Those notes have no `move_cause`.
    """
    need = sessions_to_explain(t, big, _meta_of(row).get("news_window"), last_session)
    if explained:
        need = [s for s in need
                if not (explained.get(s["session_date"]) is not None
                        and (t is None or explained[s["session_date"]] < t))]
    if not need:
        return None
    cause = stated_cause(row)
    if cause == "found":
        return None
    if cause == "not_found":
        return need, "not_found"
    if is_v5(row):
        return need, "not_stated"
    if _NO_NEWS.search(f"{row.get('news_view') or ''} {row.get('news_note') or ''}"):
        return need, "no_news"
    return None


def signed(v: float, digits: int, unit: str = "") -> str:
    """'+3.58%' / '−14.01%' — the typographic minus the report uses."""
    return ("−" if v < 0 else "+") + f"{abs(v):.{digits}f}{unit}"


def _move_txt(s: dict) -> str:
    """'−14.01% (−3.0σ) Fri Nov 20 session'."""
    d = dt.date.fromisoformat(s["session_date"])
    word = s.get("label") or f"{d.strftime('%a %b')} {d.day}"
    pct = s.get("move_pct")
    return ((signed(pct, 2, "%") + " " if pct is not None else "")
            + f"({signed(s['z'], 1, 'σ')}) {word} session")


def _moves_txt(ss: list[dict]) -> str:
    """The latest session in full; earlier ones by date."""
    txt = _move_txt(ss[-1])
    if len(ss) > 1:
        txt += " (and the ≥2σ session" + ("s" if len(ss) > 2 else "") + " of " \
            + ", ".join(s["session_date"] for s in ss[:-1]) + ")"
    return txt


def research_flags(claude_news: dict | None, claude_stance: dict | None,
                   move: dict | None, basis: dict | None,
                   now: dt.datetime | None = None,
                   sessions: list[dict] | None = None,
                   explained: dict[str, dt.datetime] | None = None
                   ) -> dict[str, list[str]]:
    """Warnings for the stored news check and research stance. Each note is
    judged against every completed ≥2σ session inside its news window
    (`sessions_to_explain`), not only the last one, so a warning does not
    disappear after one ordinary day.

      * the note's research was partial or failed, or its research_status
        could not be read → the check was not completed
      * written before a ≥2σ session closed → it could not have known about
        that move
      * a ≥2σ session in its window has no stated cause (`failed_news_check`):
        `move_cause: not_found`; a prompt-v5 note with anything other than
        `move_cause: found`; or, for a note from before v5, the text "no
        material news". That is a failed check, not a neutral one.
      * a stance whose own discussion has a failed news check
    """
    out = {"news": [], "stance": []}
    if claude_news:
        m = _meta_of(claude_news)
        unread = m.get("unreadable") if isinstance(m.get("unreadable"), dict) else {}
        if m.get("research_status") in ("partial", "failed"):
            out["news"].append(f"research incomplete (research_status: {m['research_status']}) "
                               "— this news check was not completed; do not read it as a result")
        elif unread.get("research_status"):
            out["news"].append(f"research_status unreadable: '{unread['research_status']}' — "
                               "this news check cannot be taken as completed")
        if unread.get("move_cause"):
            out["news"].append(f"move_cause unreadable: '{unread['move_cause']}' — the "
                               "check does not say whether a cause was found")
    if not basis or not basis.get("session_date"):
        return out
    big = big_sessions(move, basis, sessions)
    last = basis["session_date"]

    if claude_news:
        t = _parse_ts(claude_news.get("created_ts"))
        after = sessions_closed_after(t, big)
        failed = failed_news_check(claude_news, t, big, last, explained)
        if after:
            out["news"].append(f"news check written {t.date().isoformat()}, before the "
                               f"{_move_txt(after[-1])} — it could not cover that move")
        elif failed and failed[1] == "not_found":
            out["news"].append(f"did not find the cause of the {_moves_txt(failed[0])} "
                               "(move_cause: not_found) — treat this check as failed, "
                               "not neutral")
        elif failed and failed[1] == "not_stated":
            out["news"].append(f"does not state the cause of the {_moves_txt(failed[0])} "
                               f"({cause_text(claude_news)}) — a ≥2σ session in its window "
                               "needs move_cause: found; treat this check as failed, "
                               "not neutral")
        elif failed:
            out["news"].append(f"says no material news after a {_moves_txt(failed[0])} — the "
                               "move's reported cause was not found; treat this "
                               "check as failed, not neutral")
        if t and now:
            age_d = (mc.to_ny(now) - t).days
            if age_d >= 3:
                out["news"].append(f"news check is {age_d} days old")
    if claude_stance:
        t = _parse_ts(claude_stance.get("created_ts"))
        after = sessions_closed_after(t, big)
        failed = failed_news_check(claude_stance, t, big, last, explained)
        if after:
            out["stance"].append(f"stance written {t.date().isoformat()}, before the "
                                 f"{_move_txt(after[-1])} — re-run research before acting")
        elif failed and failed[1] == "no_news":
            # (move_cause: not_found has its own stance flag in research_quality)
            out["stance"].append("stance came from a discussion whose news check "
                                 f"missed the cause of the {_moves_txt(failed[0])} — re-run "
                                 "research before acting")
        elif failed and failed[1] == "not_stated":
            out["stance"].append("stance came from a discussion whose news check does not "
                                 f"state the cause of the {_moves_txt(failed[0])} "
                                 f"({cause_text(claude_stance)}) — re-run research before "
                                 "acting")
    return out


def news_window(last_report_date: str | None, last_session: str | None,
                today: dt.date) -> dict[str, str]:
    """The window a news check must cover: since the last report for this
    ticker, or the last 5 trading days — whichever starts EARLIER."""
    d = dt.date.fromisoformat(last_session) if last_session else today
    five = d
    for _ in range(4):
        five = mc.previous_trading_day(five)
    start = five
    if last_report_date:
        try:
            lr = dt.date.fromisoformat(last_report_date[:10])
            start = min(start, lr)
        except ValueError:
            pass
    return {"from": start.isoformat(), "to": today.isoformat()}
