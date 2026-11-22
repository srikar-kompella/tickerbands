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

_NO_NEWS = re.compile(
    r"\bno (material|significant|major|notable|relevant|company[- ]specific)\b"
    r"|\bnothing (material|significant|notable)\b|\bno news\b", re.I)


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
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s)[:19])
    except ValueError:
        return None
    return mc.to_ny(t)


def session_close_dt(session_iso: str) -> dt.datetime:
    d = dt.date.fromisoformat(session_iso)
    return mc.to_ny(dt.datetime.combine(d, mc.close_time(d)))


def research_flags(claude_news: dict | None, claude_stance: dict | None,
                   move: dict | None, basis: dict | None,
                   now: dt.datetime | None = None) -> dict[str, list[str]]:
    """Warnings for the stored news check and research stance.

      * written before the last session closed, when that session moved
        ≥ 2σ → it could not have known about the move
      * says "no material news" after a ≥ 2σ move → the cause was not found;
        that is a failed check, not a neutral one
      * a stance that shares its discussion with a failed news check
    """
    out = {"news": [], "stance": []}
    if not move or not basis or not basis.get("session_date"):
        return out
    z = move.get("z")
    big = z is not None and abs(z) >= BIG_MOVE_SIGMA
    sess = basis["session_date"]
    close_dt = session_close_dt(sess)
    word = basis.get("move_word") or sess
    move_txt = f"{move.get('move_pct'):+.2f}% ({z:+.1f}σ) {word} session" if big else ""

    news_failed = False
    if claude_news:
        t = _parse_ts(claude_news.get("created_ts"))
        note = f"{claude_news.get('news_view') or ''} {claude_news.get('news_note') or ''}"
        if big and t and t < close_dt and basis.get("price_kind") != "live":
            out["news"].append(f"news check written {t.date().isoformat()}, before the "
                               f"{move_txt} — it could not cover that move")
            news_failed = True
        elif big and _NO_NEWS.search(note):
            out["news"].append(f"says no material news after a {move_txt} — the "
                               "move's reported cause was not found; treat this "
                               "check as failed, not neutral")
            news_failed = True
        if t and now:
            age_d = (mc.to_ny(now) - t).days
            if age_d >= 3:
                out["news"].append(f"news check is {age_d} days old")
    if claude_stance:
        t = _parse_ts(claude_stance.get("created_ts"))
        if big and t and t < close_dt and basis.get("price_kind") != "live":
            out["stance"].append(f"stance written {t.date().isoformat()}, before the "
                                 f"{move_txt} — re-run research before acting")
        elif (news_failed and claude_news and claude_stance.get("created_ts")
              and str(claude_news.get("created_ts"))[:16]
              == str(claude_stance.get("created_ts"))[:16]):
            out["stance"].append("stance came from a discussion whose news check "
                                 f"missed the cause of the {move_txt} — re-run "
                                 "research before acting")
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
