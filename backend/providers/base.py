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


def get_secret(keychain_service: str, env_name: str) -> str | None:
    """Keychain first (the user's setup), env var fallback (tests / other hosts)."""
    return from_keychain(keychain_service) or os.environ.get(env_name)
