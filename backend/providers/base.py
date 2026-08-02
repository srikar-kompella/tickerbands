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
        try:
            import keyring  # lazy: optional dependency
        except ImportError:
            keyring = None
        if keyring is not None:
            try:
                val = keyring.get_password("tickerlens", keychain_service)
                if val:
                    return val
            except Exception:
                pass  # locked/absent backend — fall through
        val = from_keychain(keychain_service)
        if val:
            return val
    return os.environ.get(env_name)


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
