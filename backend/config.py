"""
config.py — paths, ports, TTLs, flags, and the user's instance configuration.

OSS layout: ALL user data lives in an instance directory OUTSIDE the repo —
    ~/.tickerlens/            (override with TICKERLENS_HOME)
      config.toml             identity + capability→provider mapping (no secrets)
      .env                    secrets (API keys), chmod 600
      tickerlens.db           the database
      discussions/            research-discussion markdown files
      schwab_token.json       broker OAuth token (if Schwab is used)
`git pull` can never touch user data, and code never writes into the repo.

Secrets resolution order (providers/base.get_secret):
    keyring ("tickerlens" service) → legacy macOS Keychain → process env
    (which this module pre-seeds from ~/.tickerlens/.env) → absent.
    TICKERLENS_NO_KEYRING=1 skips the two keychain steps.
"""
from __future__ import annotations

import os

# ─── instance directory ────────────────────────────────────────────────────────
TICKERLENS_HOME = os.path.expanduser(os.environ.get("TICKERLENS_HOME", "~/.tickerlens"))
os.makedirs(TICKERLENS_HOME, exist_ok=True)

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BACKEND_DIR)

# secrets: load instance .env into the environment (setdefault — real env wins)
_ENV_PATH = os.path.join(TICKERLENS_HOME, ".env")
if os.path.exists(_ENV_PATH):
    for _line in open(_ENV_PATH):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# user config (no secrets here)
_CONFIG: dict = {}
_cfg_path = os.path.join(TICKERLENS_HOME, "config.toml")
if os.path.exists(_cfg_path):
    try:
        try:
            import tomllib
        except ImportError:      # Python 3.10 fallback
            import tomli as tomllib
        with open(_cfg_path, "rb") as _f:
            _CONFIG = tomllib.load(_f)
    except Exception as _e:  # malformed config must not brick the app
        print(f"[tickerlens] WARNING: config.toml unreadable ({_e}) — using defaults")

USER_NAME: str = (_CONFIG.get("user", {}).get("name") or "the user").strip()
# Required for SEC EDGAR's fair-use User-Agent policy; the edgar provider
# refuses politely until it's set.
CONTACT_EMAIL = (_CONFIG.get("user", {}).get("contact_email") or None)

# ─── capability → provider mapping (the heart of BYO-credentials) ──────────────
# Override any of these in config.toml [capabilities]. "none" disables a
# capability; dependent features degrade/hide via the per-section contract.
CAPABILITY_DEFAULTS = {
    # Price data defaults to the KEYLESS tier-0 provider so a fresh clone
    # works before any signup; the wizard upgrades these to a broker/feed.
    "quotes": "yfinance",
    "daily_history": "yfinance",
    "option_chain": "schwab",
    "account": "schwab",
    "fundamentals": "finnhub",
    "news_sentiment": "finnhub",
    "news_headlines": "finnhub",
    "analyst_recs": "finnhub",
    "earnings": "finnhub",
    "social": "stocktwits",
    "insiders": "edgar",
    "short_interest": "finra",
    "symbol_search": "finnhub",
}
CAPABILITIES_CONFIG: dict = {
    **CAPABILITY_DEFAULTS,
    **{k: str(v).lower() for k, v in (_CONFIG.get("capabilities") or {}).items()},
}

# ─── identity / ports ──────────────────────────────────────────────────────────
BACKEND_PORT = 8001
FRONTEND_ORIGINS = ["http://localhost:5174", "http://127.0.0.1:5174"]

# ─── paths (instance-dir based; env overrides for tests/tools) ─────────────────
DB_PATH = os.environ.get("TICKERLENS_DB_PATH",
                         os.path.join(TICKERLENS_HOME, "tickerlens.db"))
DISCUSSIONS_DIR = os.environ.get(
    "TICKERLENS_DISCUSSIONS_DIR", os.path.join(TICKERLENS_HOME, "discussions"))
DISCUSSIONS_FAILED_DIR = os.path.join(DISCUSSIONS_DIR, "_failed")
DISCOVERIES_DIR = os.path.join(TICKERLENS_HOME, "discoveries")

_paths_cfg = _CONFIG.get("paths") or {}
SCHWAB_TOKEN_PATH = os.environ.get(
    "TICKERLENS_SCHWAB_TOKEN_PATH",
    os.path.expanduser(_paths_cfg.get("schwab_token")
                       or os.path.join(TICKERLENS_HOME, "schwab_token.json")))
SCHWAB_CALLBACK_URL = os.environ.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182")
# Optional: a legacy portfolio SQLite to one-time-import watchlist/actions from.
PORTFOLIO_DB_PATH = os.path.expanduser(_paths_cfg.get("portfolio_db") or "") or None

# Validation receipts ship with the repo but are BASKET-SPECIFIC (see CLAUDE.md
# honesty rules) — regenerate on your own symbols before leaning on them.
BAND_COVERAGE_JSON = os.path.join(BACKEND_DIR, "analysis", "band_coverage.json")
BAND_VALIDATION_JSON = os.path.join(BACKEND_DIR, "analysis", "band_validation.json")
GLOSSARY_JSON = os.path.join(BACKEND_DIR, "analysis", "glossary.json")

# ─── cache TTLs (seconds) ──────────────────────────────────────────────────────
TTL = {
    "quote": 30, "history": 600, "options": 300, "news_sentiment": 3600,
    "headlines": 3600, "social": 900, "analyst": 12 * 3600,
    "fundamentals": 24 * 3600, "earnings": 12 * 3600, "portfolio": 300,
    "market_context": 300, "search": 24 * 3600, "history2y": 24 * 3600,
    "earnings_dates": 24 * 3600, "insiders": 12 * 3600,
    "short_interest": 24 * 3600, "shares_out": 7 * 24 * 3600,
    "earnings_calendar_market": 6 * 3600,
}

# ─── feature flags (capability gating happens in providers/registry.py) ────────
FEATURES = {
    "fundamentals": True, "options": True, "news": True, "social": True,
    "analyst": True, "earnings": True, "ownership": True, "setup_score": True,
    "insiders": True, "short_interest": True, "earnings_move": True,
}

# ─── volatility band constants (validated — do not tune casually) ──────────────
BAND_Z = 1.28
TRADING_DAYS = 252
HISTORY_DAYS = 130
BAND_ENGINES = ("flat20", "ewma", "conformal")

# ─── Setup Score defaults (user-tunable in Settings) ───────────────────────────
DEFAULT_WEIGHTS = {
    "momentum": 30.0, "news_sentiment": 22.5, "options_positioning": 22.5,
    "analyst_trend": 15.0, "social_buzz": 10.0,
}
VOL_FACTOR_MIN, VOL_FACTOR_MAX = 0.85, 1.15
SCORE_BANDS = {"green": 70, "yellow": 40}
LEAN_BULLISH, LEAN_BEARISH = 60, 40

SCORE_DISCLAIMER = (
    "Setup Score aggregates observable signals — it does not predict tomorrow's "
    "direction. Empirical evidence: 1-day direction prediction from these signals "
    "fails at ~50% accuracy across ~9,600 backtested predictions."
)


def reload() -> None:
    """Re-read config.toml + .env and refresh module globals in place — lets
    the setup wizard apply changes without a process restart. Only the values
    a wizard can change are refreshed; structural constants stay put."""
    global _CONFIG, USER_NAME, CONTACT_EMAIL, CAPABILITIES_CONFIG, PORTFOLIO_DB_PATH
    cfg_path = os.path.join(TICKERLENS_HOME, "config.toml")
    cfg: dict = {}
    if os.path.exists(cfg_path):
        try:
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib
            with open(cfg_path, "rb") as f:
                cfg = tomllib.load(f)
        except Exception as e:
            print(f"[tickerlens] WARNING: config.toml unreadable ({e})")
    _CONFIG = cfg
    USER_NAME = (cfg.get("user", {}).get("name") or "the user").strip()
    CONTACT_EMAIL = (cfg.get("user", {}).get("contact_email") or None)
    CAPABILITIES_CONFIG.clear()
    CAPABILITIES_CONFIG.update(CAPABILITY_DEFAULTS)
    CAPABILITIES_CONFIG.update(
        {k: str(v).lower() for k, v in (cfg.get("capabilities") or {}).items()})
    PORTFOLIO_DB_PATH = os.path.expanduser(
        (cfg.get("paths") or {}).get("portfolio_db") or "") or None
    env_path = os.path.join(TICKERLENS_HOME, ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")
