#!/usr/bin/env python3
"""
schwab_reauth.py — mint or refresh the Schwab OAuth token (one-shot, interactive).

Run whenever the app reports "Schwab auth expired" (the refresh token dies
~weekly by Schwab policy):

    cd backend && .venv/bin/python schwab_reauth.py

Opens a browser for the Schwab login; the token lands at the configured path
(default ~/.tickerlens/schwab_token.json). Credentials come from env /
~/.tickerlens/.env / macOS Keychain (SCHWAB_APP_KEY, SCHWAB_APP_SECRET).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402  (loads ~/.tickerlens/.env)
from providers.base import get_secret  # noqa: E402

key = get_secret("schwab_app_key", "SCHWAB_APP_KEY")
secret = get_secret("schwab_app_secret", "SCHWAB_APP_SECRET")
if not key or not secret:
    sys.exit("Missing SCHWAB_APP_KEY / SCHWAB_APP_SECRET — add them to "
             f"{config.TICKERLENS_HOME}/.env (see CLAUDE.md → Schwab setup).")

token_path = config.SCHWAB_TOKEN_PATH
if os.path.exists(token_path):
    os.remove(token_path)   # force a fresh browser flow
    print(f"removed stale token: {token_path}")

from schwab.auth import easy_client  # noqa: E402

print("Opening browser for Schwab login… complete it, then wait here.")
easy_client(api_key=key, app_secret=secret,
            callback_url=config.SCHWAB_CALLBACK_URL, token_path=token_path)
print(f"✓ token written: {token_path}\nRestart nothing — the app picks it up "
      "on the next request.")
