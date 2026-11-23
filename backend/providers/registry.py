"""
providers/registry.py — capability → provider resolution (the OSS heart).

Every data need is a CAPABILITY; config.toml maps each to an installed
provider module. Missing/`none` capability → resolve() returns None → the
dependent section/feature degrades via the standard per-section contract.
Adding a provider = one module + entries in MODULES/IMPLEMENTATIONS below
(full guide: repo-root CLAUDE.md).
"""
from __future__ import annotations

import os
from typing import Any, Callable

import config
from providers import edgar, finnhub, finra, mock, schwab, sec_xbrl, stocktwits, yfinance

MODULES = {
    "schwab": schwab, "finnhub": finnhub, "stocktwits": stocktwits,
    "edgar": edgar, "finra": finra, "yfinance": yfinance, "mock": mock,
}

# capability → {provider_name: callable}. A provider appears here only for
# capabilities it genuinely implements — the registry is the honest catalog.
IMPLEMENTATIONS: dict[str, dict[str, Callable]] = {
    "quotes": {"schwab": (schwab, "fetch_quote"), "yfinance": (yfinance, "fetch_quote"),
               "mock": (mock, "fetch_quote")},
    "quotes_batch": {"schwab": (schwab, "fetch_quotes_batch"),
                     "yfinance": (yfinance, "fetch_quotes_batch"),
                     "mock": (mock, "fetch_quotes_batch")},
    "daily_history": {"schwab": (schwab, "fetch_history"),
                      "yfinance": (yfinance, "fetch_history"),
                      "mock": (mock, "fetch_history")},
    "option_chain": {"schwab": (schwab, "fetch_options_summary"),
                     "mock": (mock, "fetch_options_summary")},
    "option_chain_lens": {"schwab": (schwab, "fetch_chain_for_lens"),
                          "mock": (mock, "fetch_chain_for_lens")},
    "account": {"schwab": (schwab, "fetch_account_positions"),
                "mock": (mock, "fetch_account_positions")},
    "fundamentals": {"finnhub": (finnhub, "fetch_fundamentals"),
                     "mock": (mock, "fetch_fundamentals")},
    "dividend_history": {"yfinance": (yfinance, "fetch_dividends"),
                         "mock": (mock, "fetch_dividends")},
    # ~6 years of daily closes + splits + distributions (valuation_range)
    "long_history": {"yfinance": (yfinance, "fetch_history_long"),
                     "mock": (mock, "fetch_history_long")},
    "eps_check": {"yfinance": (yfinance, "fetch_eps_ttm"),
                  "mock": (mock, "fetch_eps_ttm")},
    # SEC XBRL = the filed numbers (primary); Yahoo = fallback that is often
    # a few days newer right after an earnings release (merged per statement)
    "financial_statements": {"edgar": (sec_xbrl, "fetch_statements"),
                             "yfinance": (yfinance, "fetch_statements"),
                             "mock": (mock, "fetch_statements")},
    "statements_fallback": {"yfinance": (yfinance, "fetch_statements")},
    "sec_filings": {"edgar": (sec_xbrl, "fetch_recent_filings"),
                    "mock": (mock, "fetch_recent_filings")},
    "rating_changes": {"yfinance": (yfinance, "fetch_rating_changes"),
                       "mock": (mock, "fetch_rating_changes")},
    # quote type / industry / share counts / fund facts — what kind of
    # security this is (report_view.classify_security)
    "security_profile": {"yfinance": (yfinance, "fetch_security_profile"),
                         "mock": (mock, "fetch_security_profile")},
    # the EX-99.1 exhibit of an earnings 8-K, via the filing index
    "sec_exhibit": {"edgar": (sec_xbrl, "fetch_release_exhibit")},
    "news_sentiment": {"finnhub": (finnhub, "fetch_news_sentiment"),
                       "mock": (mock, "fetch_news_sentiment")},
    "news_headlines": {"finnhub": (finnhub, "fetch_headlines"),
                       "mock": (mock, "fetch_headlines")},
    "analyst_recs": {"finnhub": (finnhub, "fetch_recommendations"),
                     "mock": (mock, "fetch_recommendations")},
    "earnings": {"finnhub": (finnhub, "fetch_earnings"), "mock": (mock, "fetch_earnings")},
    "earnings_dates": {"finnhub": (finnhub, "fetch_past_earnings_dates"),
                       "mock": (mock, "fetch_past_earnings_dates")},
    "earnings_confirm": {"yfinance": (yfinance, "fetch_earnings_confirmation"),
                         "mock": (mock, "fetch_earnings_confirmation")},
    "shares_out": {"finnhub": (finnhub, "fetch_shares_outstanding"),
                   "mock": (mock, "fetch_shares_outstanding")},
    "social": {"stocktwits": (stocktwits, "fetch_social"), "mock": (mock, "fetch_social")},
    "insiders": {"edgar": (edgar, "fetch_insiders"), "mock": (mock, "fetch_insiders")},
    "short_interest": {"finra": (finra, "fetch_short_interest"),
                       "mock": (mock, "fetch_short_interest")},
    "symbol_search": {"finnhub": (finnhub, "fetch_symbol_search"),
                      "mock": (mock, "fetch_symbol_search")},
}

# sub-capabilities inherit the parent's configured provider
_ALIAS = {"quotes_batch": "quotes", "option_chain_lens": "option_chain",
          "earnings_dates": "earnings", "shares_out": "fundamentals",
          # keyless Yahoo flag rides on the same provider as the EPS check
          "earnings_confirm": "eps_check", "statements_fallback": "eps_check",
          "rating_changes": "eps_check", "sec_filings": "insiders",
          "security_profile": "eps_check", "sec_exhibit": "insiders",
          "long_history": "eps_check"}


def use_mock() -> bool:
    from envcompat import env
    return (env("MOCK") or "").strip() in {"1", "true", "yes"}


def provider_name(capability: str) -> str:
    cfg_key = _ALIAS.get(capability, capability)
    if use_mock():
        return "mock"
    return config.CAPABILITIES_CONFIG.get(cfg_key, "none")


def resolve(capability: str) -> Callable | None:
    """The fetch callable for a capability, or None (unconfigured/'none'/
    provider doesn't implement it)."""
    name = provider_name(capability)
    if name in ("none", ""):
        return None
    entry = IMPLEMENTATIONS.get(capability, {}).get(name)
    if entry is None:
        return None
    module, attr = entry
    return getattr(module, attr, None)  # late-bound: tests can monkeypatch


def snapshot() -> dict[str, Any]:
    """GET /api/capabilities payload — what's wired, by whom."""
    out = {}
    for cap in config.CAPABILITY_DEFAULTS:
        name = provider_name(cap)
        out[cap] = {"provider": name,
                    "available": resolve(cap) is not None}
    return out
