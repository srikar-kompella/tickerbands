"""
providers/stocktwits.py — retail social sentiment from StockTwits' public API.

Adapted from the experiment's scripts/stocktwits_data.py (read-only source).
No auth required; 200 req/hr public limit is comfortable for an on-demand tool
(this was the batch pipeline's constraint, not ours — Q1).

Signal semantics: users self-tag messages Bullish/Bearish. We aggregate the
last ~30 messages: polarity = (bull − bear) / tagged ∈ [−1, +1], plus raw
counts so the UI can show how thin the sample is (an ETF stream with 3 tagged
messages should *look* thin, not authoritative).
"""
from __future__ import annotations

import time
from typing import Any

import requests

from providers.base import ProviderError

name = "stocktwits"
URL = "https://api.stocktwits.com/api/2/streams/symbol/{sym}.json"
HEADERS = {"User-Agent": "tickerlens/1.0"}


def health_check() -> bool:
    return True  # no credentials to check; failures surface per-fetch


def fetch_social(symbol: str) -> dict[str, Any]:
    try:
        r = requests.get(URL.format(sym=symbol), headers=HEADERS, timeout=8)
        if r.status_code == 429:
            time.sleep(2.0)
            r = requests.get(URL.format(sym=symbol), headers=HEADERS, timeout=8)
        if r.status_code == 429:
            raise ProviderError("rate_limited", "StockTwits 429 after retry")
        if r.status_code == 404:
            raise ProviderError("not_found", f"no StockTwits stream for {symbol}")
        if r.status_code != 200:
            raise ProviderError("unavailable", f"StockTwits HTTP {r.status_code}")
        payload = r.json()
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError("unavailable", f"StockTwits: {e}")

    messages = payload.get("messages") or []
    bullish = bearish = 0
    recent: list[dict[str, Any]] = []
    for msg in messages:
        label = (((msg.get("entities") or {}).get("sentiment") or {}).get("basic") or "").lower()
        if label == "bullish":
            bullish += 1
        elif label == "bearish":
            bearish += 1
        if len(recent) < 3:  # a taste of the stream for the UI, not a firehose
            recent.append({
                "body": (msg.get("body") or "")[:200],
                "sentiment": label or None,
                "created_at": msg.get("created_at"),
            })
    tagged = bullish + bearish
    return {
        "available": True,
        "bullish": bullish,
        "bearish": bearish,
        "tagged": tagged,
        "total_msgs": len(messages),
        "polarity": round((bullish - bearish) / tagged, 3) if tagged else 0.0,
        "sample": recent,
    }


def fetch(ticker: str) -> dict[str, Any]:
    return {"social": fetch_social(ticker)}
