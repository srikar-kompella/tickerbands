"""
providers/finra.py — bi-monthly equity short interest from FINRA's free data API.

FINRA publishes consolidated short interest twice a month (Rule 4560); the
query API returns JSON without an API key for public datasets. Exact payload
shapes have shifted before, so this provider is defensive: whatever fails →
ProviderError → the section degrades to 'unavailable' (Q12), never breaks the
page. ⚠ VERIFY-ON-MAC: the sandbox can't reach api.finra.org, so the first
live call happens on the user's machine — the field-name fallbacks below cover
the documented variants.

SI as a % is computed against SHARES OUTSTANDING (Finnhub profile2) and
labeled as such — true float data isn't free, and mislabeling outstanding as
float is how retail tools lie with squeeze numbers.
"""
from __future__ import annotations

import time
from typing import Any

import requests

from providers.base import ProviderError

name = "finra"
URL = "https://api.finra.org/data/group/otcmarket/name/consolidatedShortInterest"
HEADERS = {"Accept": "application/json",
           "User-Agent": "TickerLens/1.0 (personal research)"}


def health_check() -> bool:
    return True


def _field(row: dict, *names: str) -> Any:
    for n in names:
        if n in row and row[n] is not None:
            return row[n]
    return None


def fetch_short_interest(symbol: str) -> dict[str, Any]:
    body = {
        "limit": 2,  # newest two settlement periods → level + delta
        "compareFilters": [{"compareType": "EQUAL",
                            "fieldName": "issueSymbolIdentifier",
                            "fieldValue": symbol.upper()}],
        "sortFields": ["-settlementDate"],
    }
    try:
        r = requests.post(URL, json=body, headers=HEADERS, timeout=10)
        if r.status_code == 429:
            time.sleep(1.0)
            r = requests.post(URL, json=body, headers=HEADERS, timeout=10)
        if r.status_code in (401, 403):
            raise ProviderError("unavailable",
                                "FINRA API needs registration for this dataset — "
                                "section disabled until a (free) FINRA API key is added")
        if r.status_code != 200:
            raise ProviderError("unavailable", f"FINRA HTTP {r.status_code}")
        rows = r.json()
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError("unavailable", f"FINRA: {e}")

    if not isinstance(rows, list) or not rows:
        return {"available": False}

    cur = rows[0]
    si = _field(cur, "currentShortPositionQuantity", "shortInterest", "currentShortPosition")
    prev = (_field(rows[1], "currentShortPositionQuantity", "shortInterest")
            if len(rows) > 1 else _field(cur, "previousShortPositionQuantity"))
    adv = _field(cur, "averageDailyVolumeQuantity", "averageDailyVolume")
    dtc = _field(cur, "daysToCoverQuantity", "daysToCover")
    if dtc is None and si and adv:
        dtc = round(float(si) / float(adv), 1)

    return {
        "available": si is not None,
        "settlement_date": _field(cur, "settlementDate"),
        "short_interest": float(si) if si is not None else None,
        "prev_short_interest": float(prev) if prev is not None else None,
        "change_pct": (round((float(si) - float(prev)) / float(prev) * 100, 1)
                       if si is not None and prev else None),
        "days_to_cover": float(dtc) if dtc is not None else None,
        "pct_of_shares_out": None,  # composer fills via Finnhub shares outstanding
    }
