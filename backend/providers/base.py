"""
providers/base.py — the Provider contract + shared plumbing.

Every data source is one file in providers/ implementing fetch functions that
either return plain dicts/lists or raise ProviderError. NOTHING here may call
sys.exit() — the experiment repo's scripts do, and inside FastAPI that kills
the whole server (DESIGN_QUESTIONS Q11). The composer catches ProviderError
per section and degrades gracefully (Q12).

Adding a new API integration = write one file here implementing fetch(),
register it in analysis/composer.py, add a TTL in config.py. Done.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Protocol


class ProviderError(Exception):
    """A provider failed in a way the composer should degrade around.

    `reason` is a stable, machine-readable string the frontend can key UI off:
      auth_expired | rate_limited | not_found | no_data | unavailable
    """

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


class Provider(Protocol):
    """Structural interface (duck-typed; mock.py satisfies it for tests)."""

    name: str

    def health_check(self) -> bool: ...

    def fetch(self, ticker: str) -> dict[str, Any]: ...


# ─── shared secret access ──────────────────────────────────────────────────────

def from_keychain(service: str) -> str | None:
    """Read a secret from macOS Keychain. None on non-macOS or when unset —
    callers decide whether that's fatal (raise ProviderError) or optional."""
    try:
        out = subprocess.run(
            ["security", "find-generic-password",
             "-a", os.environ.get("USER", ""), "-s", service, "-w"],
            capture_output=True, text=True,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except FileNotFoundError:
        pass  # not macOS — env var fallback below still works
    return None


def _no_keyring() -> bool:
    """Truthy TICKERLENS_NO_KEYRING skips the OS secret stores; "", "0" and
    "false" mean OFF (keyring enabled) — mirrors Prime exactly."""
    return os.environ.get("TICKERLENS_NO_KEYRING", "").strip().lower() not in ("", "0", "false")


def get_secret(keychain_service: str, env_name: str) -> str | None:
    """Resolve one secret. Precedence (first hit wins):

      1. `keyring` — cross-platform OS secret store (macOS Keychain, Windows
         Credential Manager, Linux Secret Service), all under the single
         service name "tickerlens": keyring.get_password("tickerlens", name).
      2. legacy macOS Keychain via `security` (from_keychain) — honors entries
         created with `security add-generic-password -s <name>`.
      3. os.environ — note that config.py merges ~/.tickerlens/.env into the
         environment at import time, so .env keys arrive here too.

    Footgun to know: a stale keyring entry shadows a rotated .env/env value.
    Set TICKERLENS_NO_KEYRING=1 (any truthy value) to skip steps 1–2 entirely
    (tests do this so no test ever touches an OS secret store).
    """
    if not _no_keyring():
        if keychain_service in _SECRET_CACHE:
            return _SECRET_CACHE[keychain_service]
        val = _timed(lambda: _keyring_get(keychain_service))
        if val:
            _SECRET_CACHE[keychain_service] = val
            return val
        val = _timed(lambda: from_keychain(keychain_service))
        if val:
            _SECRET_CACHE[keychain_service] = val
            return val
    return os.environ.get(env_name)


# Secrets are static per process — hit the OS store once per key, then serve
# from memory. (Before this cache, EVERY api call re-queried the Keychain.)
_SECRET_CACHE: dict[str, str] = {}
_STORE_BLOCKED = False
_STORE_TIMEOUT_S = 2.0


def _keyring_get(name: str) -> str | None:
    try:
        import keyring  # lazy: optional dependency
    except ImportError:
        return None
    try:
        return keyring.get_password("tickerlens", name)
    except Exception:
        return None  # locked/absent backend — fall through


def _timed(fn) -> str | None:
    """OS secret stores can BLOCK on a hidden permission dialog (macOS: "Python
    wants to access key ... in your keychain"), hanging every request that
    resolves a secret. Run the lookup on a worker thread with a hard timeout;
    on the first stall, disable the store layers for this process (env still
    works) rather than freezing the app. A restart re-enables them."""
    global _STORE_BLOCKED
    if _STORE_BLOCKED:
        return None
    import queue as _q
    import threading as _t
    box: _q.Queue = _q.Queue(maxsize=1)
    _t.Thread(target=lambda: box.put(fn()), daemon=True).start()
    try:
        return box.get(timeout=_STORE_TIMEOUT_S)
    except _q.Empty:
        _STORE_BLOCKED = True
        print("[tickerlens] WARNING: OS secret store not answering (hidden "
              "keychain permission dialog?) — falling back to env for this "
              "run. Approve the dialog and restart to re-enable.")
        return None


# ─── SUBSCRIPTIONS — THE registry of individual subscriptions ──────────────────
# Single source of truth for what each bundled provider needs from the user:
# which secrets (keyring/Keychain name + env-var fallback pair), what it costs,
# and where to sign up. api/setup.py derives the wizard's key lists from this;
# README's "Subscriptions & API keys" table mirrors it. Adding a provider?
# Add its entry here — do not hardcode key names anywhere else.
SUBSCRIPTIONS: dict[str, dict[str, Any]] = {
    "schwab": {
        "label": "Charles Schwab (broker API)",
        "secrets": [("schwab_app_key", "SCHWAB_APP_KEY"),
                    ("schwab_app_secret", "SCHWAB_APP_SECRET")],
        "cost": "free with a Schwab brokerage account",
        "signup": "https://developer.schwab.com",
    },
    "finnhub": {
        "label": "Finnhub (market data API)",
        # exact historical pair — the env var is FINNHUB_KEY, NOT a case
        # transform of the secret name.
        "secrets": [("finnhub_api_key", "FINNHUB_KEY")],
        "cost": "free tier (60 calls/min)",
        "signup": "https://finnhub.io/register",
    },
    "yfinance": {
        "label": "yfinance (unofficial Yahoo Finance)",
        "secrets": [],
        "cost": "free, no key (tier-0)",
        "signup": "pip install yfinance",
    },
    "edgar": {
        "label": "SEC EDGAR (insider filings)",
        "secrets": [],  # no key; SEC requires a User-Agent contact email
        "cost": "free (fair-use: contact email in config.toml)",
        "signup": "none — set user.contact_email in ~/.tickerlens/config.toml",
    },
    "finra": {
        "label": "FINRA (short interest)",
        "secrets": [],
        "cost": "free, no key",
        "signup": "none",
    },
    "stocktwits": {
        "label": "StockTwits (social sentiment)",
        "secrets": [],
        "cost": "free, no key",
        "signup": "none",
    },
}
