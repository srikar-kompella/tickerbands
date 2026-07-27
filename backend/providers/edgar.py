"""
providers/edgar.py — insider transactions from the OFFICIAL SEC EDGAR API.

Free, no key; fair-use = 10 req/s WITH a User-Agent that identifies you
(SEC requirement — requests without it get blocked). Flow:
  1. company_tickers.json → ticker→CIK map (cached 7d upstream)
  2. data.sec.gov/submissions/CIK##########.json → recent Form 4 accessions
  3. fetch each Form 4's XML → open-market transactions only

Evidence discipline (Cohen/Malloy/Pomorski, "Decoding Inside Information"):
routine trades carry ~no information; open-market purchases — especially
CLUSTER buys (≥2 distinct insiders in ~2 weeks) — carry the signal, on a
MULTI-MONTH horizon. We ship the practical filter (open-market P/S codes
only + cluster detection) and label the limitation: the full routine-trader
classification needs 3 years of per-insider history we don't pull.
"""
from __future__ import annotations

import datetime as dt
import time
import xml.etree.ElementTree as ET
from typing import Any

import requests

from providers.base import ProviderError

name = "edgar"
def _ua() -> dict:
    import config
    if not config.CONTACT_EMAIL:
        raise ProviderError(
            "unavailable",
            "SEC EDGAR requires a contact email in its User-Agent — set "
            "user.contact_email in ~/.tickerlens/config.toml")
    return {"User-Agent": f"TickerLens/1.0 (personal research; {config.CONTACT_EMAIL})"}
MAX_FILINGS = 8          # newest Form 4s parsed per request (cached 12h)
CLUSTER_WINDOW_DAYS = 14


def health_check() -> bool:
    return True  # no credentials; failures surface per-fetch


def _get_json(url: str) -> Any:
    try:
        r = requests.get(url, headers=_ua(), timeout=10)
        if r.status_code == 429:
            time.sleep(1.0)
            r = requests.get(url, headers=_ua(), timeout=10)
        if r.status_code != 200:
            raise ProviderError("unavailable", f"EDGAR {r.status_code}: {url[-60:]}")
        return r.json()
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError("unavailable", f"EDGAR: {e}")


def _cik_for(symbol: str) -> str:
    data = _get_json("https://www.sec.gov/files/company_tickers.json")
    sym = symbol.upper()
    for row in data.values():
        if (row.get("ticker") or "").upper() == sym:
            return f"{int(row['cik_str']):010d}"
    raise ProviderError("no_data", f"no CIK for {sym} (not an SEC filer — ETFs/funds don't file Form 4)")


def _form4_xml_urls(cik: str) -> list[str]:
    sub = _get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    recent = (sub.get("filings") or {}).get("recent") or {}
    urls = []
    for form, acc, doc in zip(recent.get("form", []),
                              recent.get("accessionNumber", []),
                              recent.get("primaryDocument", [])):
        if form == "4" and doc:
            acc_nodash = acc.replace("-", "")
            urls.append("https://www.sec.gov/Archives/edgar/data/"
                        f"{int(cik)}/{acc_nodash}/{doc}")
            if len(urls) >= MAX_FILINGS:
                break
    return urls


def _parse_form4(xml_text: str) -> list[dict[str, Any]]:
    """Open-market non-derivative transactions (code P=buy, S=sell) only —
    awards/option exercises/tax withholding (A/M/F/G…) are the noise the
    literature says to drop."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    owner = root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName") or "unknown"
    title = (root.findtext(".//reportingOwner/reportingOwnerRelationship/officerTitle")
             or ("Director" if root.findtext(".//reportingOwnerRelationship/isDirector") == "1" else ""))
    out = []
    for tx in root.findall(".//nonDerivativeTransaction"):
        code = tx.findtext(".//transactionCoding/transactionCode")
        if code not in ("P", "S"):
            continue
        shares = float(tx.findtext(".//transactionShares/value") or 0)
        price = float(tx.findtext(".//transactionPricePerShare/value") or 0)
        date = tx.findtext(".//transactionDate/value") or ""
        out.append({
            "owner": owner.title(), "title": title or "—", "code": code,
            "date": date, "shares": shares, "price": price,
            "value": round(shares * price, 2),
        })
    return out


def fetch_form4_feed_counts(count: int = 200) -> dict[str, int]:
    """EDGAR latest-filings ATOM feed: Form 4s market-wide -> {TICKER: n}.
    Cheap cluster PROXY; the Analysis Insiders panel is the verification step."""
    import re as _re
    url = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4"
           f"&company=&dateb=&owner=include&count={count}&output=atom")
    try:
        r = requests.get(url, headers=_ua(), timeout=12)
        if r.status_code != 200:
            raise ProviderError("unavailable", f"EDGAR feed {r.status_code}")
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError("unavailable", f"EDGAR feed: {e}")
    ciks = _re.findall(r"CIK=(\d{10})", r.text) or _re.findall(r"data/(\d+)/", r.text)
    try:
        data = _get_json("https://www.sec.gov/files/company_tickers.json")
    except ProviderError:
        return {}
    tick_map = {f"{int(v['cik_str']):010d}": (v.get("ticker") or "").upper()
                for v in data.values()}
    counts: dict[str, int] = {}
    for c in ciks:
        sym = tick_map.get(f"{int(c):010d}")
        if sym:
            counts[sym] = counts.get(sym, 0) + 1
    return counts


def fetch_insiders(symbol: str) -> dict[str, Any]:
    cik = _cik_for(symbol)
    txs: list[dict[str, Any]] = []
    for url in _form4_xml_urls(cik):
        try:
            r = requests.get(url, headers=_ua(), timeout=10)
            if r.status_code == 200:
                txs.extend(_parse_form4(r.text))
            time.sleep(0.12)  # stay well under the 10 req/s fair-use line
        except Exception:
            continue  # one bad filing must not kill the section
    if not txs:
        return {"available": True, "transactions": [], "cluster_buy": False,
                "net_shares_90d": 0, "note": "no open-market insider trades in recent filings"}

    txs.sort(key=lambda t: t["date"], reverse=True)
    cutoff_90 = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    net_90 = sum((t["shares"] if t["code"] == "P" else -t["shares"])
                 for t in txs if t["date"] >= cutoff_90)

    # cluster buy: ≥2 DISTINCT insiders purchasing within the window
    cutoff_c = (dt.date.today() - dt.timedelta(days=CLUSTER_WINDOW_DAYS)).isoformat()
    recent_buyers = {t["owner"] for t in txs if t["code"] == "P" and t["date"] >= cutoff_c}

    return {
        "available": True,
        "transactions": txs[:10],
        "cluster_buy": len(recent_buyers) >= 2,
        "cluster_buyers": sorted(recent_buyers),
        "net_shares_90d": round(net_90),
        "note": ("Open-market trades only (codes P/S); awards, option exercises "
                 "and tax sales excluded. Evidence horizon is MULTI-MONTH."),
    }
