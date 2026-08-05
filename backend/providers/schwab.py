"""
providers/schwab.py — quotes, daily history, and an options-positioning summary.

Adapted from the experiment repo's scripts/schwab_data.py (read-only source,
never imported) with the mandatory changes from DESIGN_QUESTIONS Q11:
  * raises ProviderError instead of sys.exit (sys.exit inside uvicorn = dead server)
  * lazy client construction with in-process reuse
  * options fetch upgraded: put/call ratio PLUS ATM implied vol and the ATM
    straddle "implied move" — displayed next to our realized-vol band (S4).

Token: shared with the Portfolio app (weekly `schwab-reauth`). Read-only usage —
market data only, never account orders.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from config import HISTORY_DAYS, SCHWAB_CALLBACK_URL, SCHWAB_TOKEN_PATH
from providers.base import ProviderError, get_secret

name = "schwab"

_client = None  # process-lifetime client; schwab-py refreshes tokens internally


def _make_client():
    """Build (once) an authenticated schwab-py client or raise ProviderError."""
    global _client
    if _client is not None:
        return _client

    key = get_secret("schwab_app_key", "SCHWAB_APP_KEY")
    secret = get_secret("schwab_app_secret", "SCHWAB_APP_SECRET")
    if not key or not secret:
        raise ProviderError("auth_expired", "Schwab credentials not in Keychain/env")
    if not Path(SCHWAB_TOKEN_PATH).exists():
        raise ProviderError(
            "auth_expired",
            f"token missing at {SCHWAB_TOKEN_PATH} — run schwab-reauth",
        )
    try:
        from schwab.auth import easy_client
    except ImportError as e:
        raise ProviderError("unavailable", f"schwab-py not installed: {e}")

    try:
        _client = easy_client(
            api_key=key, app_secret=secret,
            callback_url=SCHWAB_CALLBACK_URL, token_path=SCHWAB_TOKEN_PATH,
        )
    except Exception as e:
        # easy_client raises on expired refresh tokens (the 7-day wall)
        raise ProviderError("auth_expired", f"token refresh failed: {e}")
    return _client


def health_check() -> bool:
    try:
        _make_client()
        return True
    except ProviderError:
        return False


# ─── quotes ────────────────────────────────────────────────────────────────────

def _extended_view(q: dict, regular: dict, extended: dict) -> dict[str, Any]:
    """Extract regular-session vs extended-hours (pre/post) values from a
    Schwab quote blob. Pure + fixture-testable because Schwab's exact field
    population varies by session.

    Sources, most-specific first: the `extended` object's lastPrice/mark; else
    the composite quote.lastPrice when it has traded AWAY from the official
    regularMarketLastPrice after the regular print (classic post-market tell).
    """
    last = q.get("lastPrice") or q.get("mark") or q.get("closePrice") or 0.0
    regular_last = regular.get("regularMarketLastPrice") or None
    regular_pct = regular.get("regularMarketPercentChange")
    reg_time = int(regular.get("regularMarketTradeTime") or 0)
    q_time = int(q.get("quoteTime") or q.get("tradeTime") or 0)

    ah_price = extended.get("lastPrice") or extended.get("mark") or None
    if ah_price is None and regular_last and q_time > reg_time > 0 \
            and abs(float(last) - float(regular_last)) / float(regular_last) > 0.0005:
        ah_price = float(last)

    out: dict[str, Any] = {
        "regular_last": float(regular_last) if regular_last else None,
        "regular_change_pct": (float(regular_pct) if regular_pct is not None else None),
        "ah_price": float(ah_price) if ah_price else None,
        "ah_change_pct": None,
        "is_extended": False,
    }
    if out["ah_price"] and out["regular_last"]:
        out["ah_change_pct"] = round(
            (out["ah_price"] / out["regular_last"] - 1) * 100, 2)
        out["is_extended"] = True
    return out


def fetch_quote(symbol: str) -> dict[str, Any]:
    """Live quote. Field semantics follow LOOKAHEAD_AUDIT F6: pre-market,
    `closePrice` is the prior session's close; intraday it's still prior close
    until Schwab rolls it post-session."""
    client = _make_client()
    try:
        resp = client.get_quotes([symbol])
        resp.raise_for_status()
        raw = resp.json()
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError("unavailable", f"quote fetch failed: {e}")

    blob = raw.get(symbol) or raw.get(symbol.upper()) or {}
    q = blob.get("quote") or {}
    last = q.get("lastPrice") or q.get("mark") or q.get("closePrice")
    if last is None:
        raise ProviderError("not_found", f"no quote for {symbol}")
    ref = blob.get("reference") or {}
    return {
        "symbol": symbol.upper(),
        "name": ref.get("description") or symbol.upper(),
        "last": float(last),
        **_extended_view(q, blob.get("regular") or {}, blob.get("extended") or {}),
        "open": float(q.get("openPrice") or last),
        "close_prev": float(q.get("closePrice") or last),
        "high_today": float(q.get("highPrice") or last),
        "low_today": float(q.get("lowPrice") or last),
        "week52_high": float(q.get("52WeekHigh") or 0) or None,
        "week52_low": float(q.get("52WeekLow") or 0) or None,
        "volume": int(q.get("totalVolume") or 0),
        "net_change_pct": float(q.get("netPercentChange") or 0.0),
        "quote_time_ms": int(q.get("quoteTime") or 0),
        "asset_type": blob.get("assetMainType") or "EQUITY",
    }


def fetch_quotes_batch(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """ALL symbols in ONE Schwab call — the cheap way to wake up a 36-row
    screener (get_quotes is documented to batch; per-symbol loops waste the
    rate budget 36×). Returns {SYM: quote-dict}, same shape as fetch_quote;
    symbols Schwab doesn't return are simply absent."""
    if not symbols:
        return {}
    client = _make_client()
    try:
        resp = client.get_quotes(symbols)
        resp.raise_for_status()
        raw = resp.json()
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError("unavailable", f"batch quotes failed: {e}")

    out: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        blob = raw.get(sym) or raw.get(sym.upper()) or {}
        q = blob.get("quote") or {}
        last = q.get("lastPrice") or q.get("mark") or q.get("closePrice")
        if last is None:
            continue
        ref = blob.get("reference") or {}
        out[sym.upper()] = {
            "symbol": sym.upper(),
            "name": ref.get("description") or sym.upper(),
            "last": float(last),
            **_extended_view(q, blob.get("regular") or {}, blob.get("extended") or {}),
            "open": float(q.get("openPrice") or last),
            "close_prev": float(q.get("closePrice") or last),
            "high_today": float(q.get("highPrice") or last),
            "low_today": float(q.get("lowPrice") or last),
            "week52_high": float(q.get("52WeekHigh") or 0) or None,
            "week52_low": float(q.get("52WeekLow") or 0) or None,
            "volume": int(q.get("totalVolume") or 0),
            "net_change_pct": float(q.get("netPercentChange") or 0.0),
            "quote_time_ms": int(q.get("quoteTime") or 0),
            "asset_type": blob.get("assetMainType") or "EQUITY",
        }
    return out


# ─── daily history ─────────────────────────────────────────────────────────────

def fetch_history(symbol: str, days: int = HISTORY_DAYS) -> list[dict[str, Any]]:
    """Daily OHLCV, chronological. `days` trading days (approx via 1.5x buffer).

    Bars dated today are INCLUDED (this is a live research tool, not the
    pre-open prediction pipeline) — but analysis/vol_bands.py computes
    signals from *completed* bars only, excluding any bar dated >= today,
    so a mid-session partial bar can never contaminate rv_20d / SMA distances.
    That is the F1 lesson from LOOKAHEAD_AUDIT.md applied here by design.
    """
    client = _make_client()
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=int(days * 1.5) + 7)
    try:
        resp = client.get_price_history_every_day(
            symbol, start_datetime=start, end_datetime=end
        )
        resp.raise_for_status()
        candles = resp.json().get("candles", [])
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError("unavailable", f"history fetch failed: {e}")
    if not candles:
        raise ProviderError("no_data", f"no history for {symbol}")

    return _normalize_daily(candles, days)


def _normalize_daily(candles: list[dict], days: int) -> list[dict[str, Any]]:
    """Chronological, ONE bar per date. Schwab can emit a duplicate/partial
    candle for the current session around the midnight rollover; two candles
    collapsing to the same date crashed the chart (lightweight-charts rightly
    asserts strictly-ascending unique times). Sort by raw epoch, keep the
    LAST candle per date — the later emission is the settled one."""
    by_date: dict[str, dict[str, Any]] = {}
    for c in sorted(candles, key=lambda c: c.get("datetime", 0)):
        d = dt.datetime.fromtimestamp(c.get("datetime", 0) / 1000, dt.timezone.utc)
        by_date[d.date().isoformat()] = {
            "date": d.date().isoformat(),
            "open": float(c.get("open", 0.0)),
            "high": float(c.get("high", 0.0)),
            "low": float(c.get("low", 0.0)),
            "close": float(c.get("close", 0.0)),
            "volume": int(c.get("volume", 0)),
        }
    return [by_date[k] for k in sorted(by_date)][-days:]


# ─── options positioning ──────────────────────────────────────────────────────

def fetch_options_summary(symbol: str) -> dict[str, Any]:
    """Put/call ratio + ATM implied vol + implied move from the nearest expiry.

    Semantics note (LOOKAHEAD_AUDIT F4): `totalVolume` is the *current session's*
    volume. TickerLens is used intraday/on-demand, so that's the honest number —
    but the response carries `as_of` so the UI can timestamp it. Pre-market the
    ratio may legitimately be None (near-zero volume).

    Returns {"available": False} (not an error) for tickers without listed
    options — PHYS/PSLV-class; the UI hides the section (Q12).
    """
    client = _make_client()
    try:
        resp = client.get_option_chain(symbol)
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError("unavailable", f"option chain failed: {e}")
    if resp.status_code != 200:
        return {"available": False}
    data = resp.json()
    if (data.get("status") or "").upper() == "FAILED":
        return {"available": False}

    underlying = float(data.get("underlyingPrice") or 0.0)

    def scan(exp_map: dict) -> tuple[int, tuple | None]:
        """Walk one side of the chain → (total volume, best ATM contract).

        Best ATM = smallest (|strike − spot|, dte) among contracts expiring in
        1–45 days with a real mark. The dte tiebreak prefers the *nearest*
        expiry at the same strike distance, which is what "implied move" means.
        """
        total_vol = 0
        best: tuple | None = None  # (dist, mark, iv, dte)
        for _exp, strikes in (exp_map or {}).items():
            for _strike, contracts in strikes.items():
                for c in contracts:
                    total_vol += int(c.get("totalVolume") or 0)
                    dte = int(c.get("daysToExpiration") or 9999)
                    strike = float(c.get("strikePrice") or 0.0)
                    mark = float(c.get("mark") or c.get("last") or 0.0)
                    iv = float(c.get("volatility") or 0.0)  # Schwab reports %
                    if underlying and 0 < dte <= 45 and mark > 0:
                        dist = abs(strike - underlying)
                        if best is None or (dist, dte) < (best[0], best[3]):
                            best = (dist, mark, iv, dte)
        return total_vol, best

    call_vol, atm_call = scan(data.get("callExpDateMap"))
    put_vol, atm_put = scan(data.get("putExpDateMap"))

    if call_vol == 0 and put_vol == 0 and atm_call is None:
        return {"available": False}

    put_call = (put_vol / call_vol) if call_vol > 0 else None
    implied_move_pct = atm_iv = expiry_dte = None
    if atm_call and atm_put and underlying:
        # ATM straddle price ≈ what the options market charges for the move
        # through expiry — expressed as ±% of spot. Honest peer for our band.
        straddle = atm_call[1] + atm_put[1]
        implied_move_pct = round(straddle / underlying * 100, 2)
        atm_iv = round((atm_call[2] + atm_put[2]) / 2, 1)
        expiry_dte = atm_call[3]

    return {
        "available": True,
        "put_call_ratio": round(put_call, 3) if put_call is not None else None,
        "call_volume": call_vol,
        "put_volume": put_vol,
        "atm_iv_pct": atm_iv,               # annualized implied vol, %
        "implied_move_pct": implied_move_pct,  # ±% through nearest expiry
        "implied_move_dte": expiry_dte,
        "as_of": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }


# ─── account positions (the Portfolio tab — replaces the old Portfolio app) ────

def fetch_account_positions() -> dict[str, Any]:
    """Live positions across the linked Schwab account(s). READ-ONLY — this
    codebase never places/replaces/cancels orders, ever.

    Returns {as_of, total_value, cash, day_pl, day_pl_pct, positions:[...]}
    with per-position day P&L straight from Schwab (no derived guessing).
    """
    client = _make_client()
    try:
        from schwab.client import Client
        resp = client.get_accounts(fields=Client.Account.Fields.POSITIONS)
        resp.raise_for_status()
        accounts = resp.json()
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError("unavailable", f"account fetch failed: {e}")

    positions: list[dict[str, Any]] = []
    cash = 0.0
    for acct in accounts if isinstance(accounts, list) else []:
        sec = acct.get("securitiesAccount") or {}
        balances = sec.get("currentBalances") or {}
        cash += float(balances.get("cashBalance")
                      or balances.get("moneyMarketFund") or 0.0)
        for p in sec.get("positions") or []:
            inst = p.get("instrument") or {}
            sym = (inst.get("symbol") or "").upper()
            qty = float(p.get("longQuantity") or 0.0) - float(p.get("shortQuantity") or 0.0)
            mv = float(p.get("marketValue") or 0.0)
            avg = float(p.get("averagePrice") or 0.0)
            cb = avg * qty
            if not sym or qty == 0:
                continue
            positions.append({
                "symbol": sym,
                "description": inst.get("description") or sym,
                "asset_type": inst.get("assetType") or "EQUITY",
                "qty": qty,
                "avg_cost": round(avg, 4),
                "market_value": round(mv, 2),
                "cost_basis": round(cb, 2),
                "gain": round(mv - cb, 2),
                "gain_pct": round((mv - cb) / cb * 100, 2) if cb else None,
                "day_pl": round(float(p.get("currentDayProfitLoss") or 0.0), 2),
                "day_pl_pct": float(p.get("currentDayProfitLossPercentage") or 0.0),
            })

    positions.sort(key=lambda x: -x["market_value"])
    total = sum(p["market_value"] for p in positions) + cash
    day_pl = sum(p["day_pl"] for p in positions)
    prev_total = total - day_pl
    for p in positions:  # weights include cash in the denominator
        p["weight_pct"] = round(p["market_value"] / total * 100, 2) if total else None

    import datetime as _dt
    return {
        "as_of": _dt.datetime.now().isoformat(timespec="seconds"),
        "total_value": round(total, 2),
        "cash": round(cash, 2),
        "day_pl": round(day_pl, 2),
        "day_pl_pct": round(day_pl / prev_total * 100, 2) if prev_total else 0.0,
        "positions": positions,
    }


def fetch_chain_for_lens(symbol: str, dte_max: int = 35) -> dict[str, Any]:
    """Filtered option chain for the premium-seller lens: per-contract strike,
    bid/ask/mark, DELTA (the market's breach probability), OI, volume, DTE.
    Window-limited server-side (from/to dates) so the payload stays sane."""
    import datetime as _dt
    client = _make_client()
    today = _dt.date.today()
    try:
        resp = client.get_option_chain(
            symbol,
            from_date=today + _dt.timedelta(days=2),
            to_date=today + _dt.timedelta(days=dte_max),
        )
    except ProviderError:
        raise
    except TypeError:
        resp = client.get_option_chain(symbol)   # older schwab-py: filter client-side
    except Exception as e:
        raise ProviderError("unavailable", f"chain fetch failed: {e}")
    if resp.status_code != 200:
        return {"available": False}
    data = resp.json()
    if (data.get("status") or "").upper() == "FAILED":
        return {"available": False}

    spot = float(data.get("underlyingPrice") or 0.0)

    def rows(exp_map: dict, side: str) -> list[dict[str, Any]]:
        out = []
        for _exp, strikes in (exp_map or {}).items():
            for _k, contracts in strikes.items():
                for c in contracts:
                    dte = int(c.get("daysToExpiration") or 0)
                    bid = float(c.get("bid") or 0.0)
                    if not (2 <= dte <= dte_max) or bid <= 0:
                        continue
                    out.append({
                        "side": side,
                        "strike": float(c.get("strikePrice") or 0.0),
                        "expiry": (c.get("expirationDate") or "")[:10],
                        "dte": dte,
                        "bid": bid,
                        "ask": float(c.get("ask") or 0.0),
                        "mark": float(c.get("mark") or bid),
                        "delta": float(c.get("delta") or 0.0),
                        "iv_pct": float(c.get("volatility") or 0.0),
                        "oi": int(c.get("openInterest") or 0),
                        "volume": int(c.get("totalVolume") or 0),
                    })
        return out

    return {"available": True, "spot": spot,
            "calls": rows(data.get("callExpDateMap"), "call"),
            "puts": rows(data.get("putExpDateMap"), "put")}


def fetch_schwab_watchlists() -> list[dict[str, Any]]:
    """Attempt to read watchlists from Schwab's Trader API.

    Honesty first: the retail Trader API launched WITHOUT the old TDA
    watchlist endpoints, and schwab-py may not expose any. We probe for
    plausible method names at runtime — if none exist (the likely case), we
    raise a clear ProviderError instead of pretending. Fallbacks in the UI:
    import from the old Portfolio app, or bulk-paste symbols.
    """
    client = _make_client()
    candidates = ("get_watchlists_for_multiple_accounts", "get_all_watchlists",
                  "get_watchlists_for_single_account", "get_watchlists")
    fn = next((getattr(client, n) for n in candidates if hasattr(client, n)), None)
    if fn is None:
        raise ProviderError(
            "no_data",
            "Schwab's retail Trader API doesn't expose watchlists "
            "(schwab-py has no watchlist methods). Use 'Import from Portfolio "
            "app' or bulk-paste your symbols instead.",
        )
    try:
        resp = fn()
        resp.raise_for_status()
        raw = resp.json()
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError("unavailable", f"watchlist fetch failed: {e}")

    out = []
    for wl in raw if isinstance(raw, list) else []:
        symbols = []
        for item in wl.get("watchlistItems") or []:
            sym = ((item.get("instrument") or {}).get("symbol") or "").upper()
            if sym:
                symbols.append(sym)
        if symbols:
            out.append({"name": wl.get("name") or "Watchlist", "symbols": symbols})
    if not out:
        raise ProviderError("no_data", "Schwab returned no watchlists")
    return out


def fetch(ticker: str) -> dict[str, Any]:
    """Provider-protocol umbrella (rarely used directly; the composer calls the
    granular functions so each piece caches on its own TTL)."""
    return {
        "quote": fetch_quote(ticker),
        "history": fetch_history(ticker),
        "options": fetch_options_summary(ticker),
    }
