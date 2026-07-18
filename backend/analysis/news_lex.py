"""
analysis/news_lex.py — headline sentiment via a small transparent lexicon.

Why this exists (the user's ask #3): Finnhub's aggregated /news-sentiment sits
behind a paid tier for many accounts, while /company-news HEADLINES are free.
Rather than showing an empty section or paying for a black-box score, we score
the headlines we already fetch with an inspectable finance-tuned wordlist.

Honesty notes:
  * This is a crude instrument — it reads words, not meaning. It's labeled
    "headline-scored" in the UI, never passed off as Finnhub's model.
  * It feeds the same c_news_sentiment component (bullish% − bearish%,
    volume-damped), so the Setup Score keeps working when Finnhub's paid
    endpoint is unavailable.
  * Pure function, fully unit-tested. Extend the wordlists freely — they're
    data, not logic.
"""
from __future__ import annotations

import re
from typing import Any

POSITIVE = {
    "beat", "beats", "tops", "surge", "surges", "soar", "soars", "jump",
    "jumps", "rally", "rallies", "record", "upgrade", "upgraded", "upgrades",
    "raise", "raises", "raised", "buyback", "outperform", "strong", "growth",
    "profit", "profits", "gain", "gains", "wins", "win", "approval",
    "approves", "approved", "expands", "expansion", "partnership", "bullish",
    "breakthrough", "exceeds", "boost", "boosts", "hikes", "dividend",
    "milestone", "upbeat", "optimistic",
}
NEGATIVE = {
    "miss", "misses", "missed", "fall", "falls", "plunge", "plunges", "drop",
    "drops", "sink", "sinks", "slump", "slumps", "downgrade", "downgraded",
    "downgrades", "cut", "cuts", "lawsuit", "sues", "sued", "probe",
    "investigation", "recall", "recalls", "layoff", "layoffs", "warns",
    "warning", "weak", "loss", "losses", "decline", "declines", "bearish",
    "fraud", "fine", "fined", "halt", "halts", "bankruptcy", "default",
    "shortfall", "disappointing", "tumble", "tumbles", "crash", "delays",
    "delay", "delisted",
}

_WORD = re.compile(r"[a-z']+")


def score_text(text: str) -> int:
    """Net lexicon hits for one headline: >0 positive, <0 negative, 0 neutral."""
    words = _WORD.findall((text or "").lower())
    return sum((w in POSITIVE) - (w in NEGATIVE) for w in words)


def score_headlines(headlines: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate headline scores → the same shape c_news_sentiment consumes.

    Neutral headlines still count toward article volume (coverage exists even
    when tone is flat) but not toward the bull/bear split.
    """
    if not headlines:
        return {"available": False}
    pos = neg = 0
    scored = []
    for h in headlines:
        s = score_text(f"{h.get('headline', '')} {h.get('summary', '')}")
        scored.append(1 if s > 0 else -1 if s < 0 else 0)
        if s > 0:
            pos += 1
        elif s < 0:
            neg += 1
    n = len(headlines)
    return {
        "available": True,
        "bullish_pct": round(pos / n, 3),
        "bearish_pct": round(neg / n, 3),
        "articles_week": n,
        "buzz": None,           # unknowable from a single page of headlines
        "method": "headline-lexicon",
        "per_headline": scored,  # aligned with the headlines list, for UI chips
    }
