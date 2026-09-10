"""
analysis/fundamentals_ttm.py — TTM-true fundamentals with provenance.

Why this exists (BUG A, 2026-09-10): vendor metric feeds annualize the latest
regular distribution and serve stale FY EPS. For variable payers and foreign
issuers that produces *materially* wrong numbers — ECO rendered P/E 40 /
yield 0.8% while the real trailing figures were ~6.4x / ~13% (same family as
the 8/5 AMLP 5.8%-vs-7.3% miss). Wrong enough to cause dismissal for the
exact wrong reason.

Doctrine:
  * Yield = SUM of actual trailing-12-month distributions ÷ price. The
    dividend HISTORY is ground truth; a vendor's pre-baked yield is only a
    cross-check. When history is unavailable the vendor figure is NOT shown
    (rendered "—" + warning) — unverified beats silently wrong.
  * P/E = price ÷ TTM EPS. EPS comes from a statement-derived source
    (yfinance trailingEps), cross-checked against the vendor's TTM EPS.
    Sources that disagree >50% → "—" + warning naming both numbers.
  * EVERY metric carries {source, as_of, warning} — no naked numbers.

Pure functions; the composer feeds cached provider payloads.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

DIVERGENCE_LIMIT = 0.50  # >50% relative disagreement → refuse to pick a side


def ttm_sum(events: list[dict], asof: dt.date | None = None) -> float:
    """Sum of distributions with ex-date inside the trailing 365 days."""
    asof = asof or dt.date.today()
    cutoff = (asof - dt.timedelta(days=365)).isoformat()
    return sum(float(e.get("amount") or 0.0) for e in events
               if cutoff < str(e.get("date"))[:10] <= asof.isoformat())


def _diverge(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return False
    hi = max(abs(a), abs(b))
    return hi > 0 and abs(a - b) / hi > DIVERGENCE_LIMIT


def _metric(value: float | None, source: str, as_of: str | None,
            warning: str | None = None) -> dict[str, Any]:
    return {"value": (round(value, 2) if value is not None else None),
            "source": source, "as_of": as_of, "warning": warning}


def build_ttm_view(price: float | None, vendor: dict | None,
                   dividends: dict | None, eps_check: dict | None,
                   asof: dt.date | None = None) -> dict[str, Any]:
    """→ {"yield_ttm": metric, "pe_ttm": metric} (see _metric shape)."""
    asof = asof or dt.date.today()
    today = asof.isoformat()
    vendor = vendor or {}
    v_yield = vendor.get("dividend_yield_pct")
    v_pe = vendor.get("pe_ttm")
    v_eps = vendor.get("eps_ttm")

    # ── yield: actual trailing distributions are the only trustworthy basis ──
    if dividends and dividends.get("available") and price:
        paid = ttm_sum(dividends.get("events") or [], asof)
        computed = paid / price * 100.0
        warn = None
        if _diverge(computed, v_yield):
            warn = (f"vendor (finnhub) reports {v_yield}% — diverges >50% from "
                    f"actual trailing distributions (${paid:.2f} paid); "
                    "vendor annualization breaks on variable payers")
        yield_ttm = _metric(computed,
                            f"computed TTM: trailing-12mo distributions ÷ price "
                            f"({dividends.get('source', '?')})",
                            dividends.get("as_of") or today, warn)
    elif v_yield is not None:
        yield_ttm = _metric(None, "finnhub (vendor metric — unverified)", today,
                            f"distribution history unavailable; vendor figure "
                            f"{v_yield}% cannot be verified and is not shown")
    else:
        yield_ttm = _metric(None, "no source", today, None)

    # ── P/E: price ÷ TTM EPS, statement-derived EPS first ────────────────────
    c_eps = (eps_check or {}).get("eps_ttm")
    eps_src = (eps_check or {}).get("source", "yfinance")
    if c_eps is not None and v_eps is not None and _diverge(c_eps, v_eps):
        pe_ttm = _metric(None, "conflict", today,
                         f"TTM EPS sources disagree >50% ({eps_src} {c_eps} vs "
                         f"finnhub {v_eps}) — P/E withheld rather than guessed")
    else:
        eps = c_eps if c_eps is not None else v_eps
        src = (f"price ÷ TTM EPS ({eps_src})" if c_eps is not None
               else "price ÷ TTM EPS (finnhub — single source)")
        if eps is None or not price:
            if v_pe is not None:
                pe_ttm = _metric(None, "finnhub (vendor metric — unverified)",
                                 today,
                                 f"no TTM EPS available to verify vendor P/E "
                                 f"{v_pe}x — not shown")
            else:
                pe_ttm = _metric(None, "no source", today, None)
        elif eps <= 0:
            pe_ttm = _metric(None, src, today,
                             "negative TTM EPS — P/E undefined")
        else:
            computed_pe = price / eps
            warn = None
            if _diverge(computed_pe, v_pe):
                warn = (f"vendor (finnhub) P/E {v_pe}x diverges >50% from "
                        f"computed {computed_pe:.1f}x — showing price ÷ TTM "
                        "EPS (stale FY EPS is the usual vendor failure)")
            pe_ttm = _metric(computed_pe, src,
                             (eps_check or {}).get("as_of") or today, warn)

    return {"yield_ttm": yield_ttm, "pe_ttm": pe_ttm}
