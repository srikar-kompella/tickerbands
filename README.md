# TickerLens 🔭

**A local-first research tool for US stocks & options — with receipts.**
Bring your own data providers and broker credentials; everything runs on your
machine. No cloud, no telemetry, no accounts.

For any ticker: a **calibrated 80% expected range** (three volatility engines,
each labeled with its measured backtest coverage), fundamentals, options
positioning, news/social sentiment, SEC insider trades, FINRA short interest,
earnings expected-move vs history, a transparent tunable Setup Score — plus a
**premium-seller's lens** (market's breach probability vs the stock's own
history, per strike), a read-only **brokerage portfolio view** with
account-level risk math, a watchlist screener with a printable morning sheet,
and an **LLM research-discussion loop** that works with any agent (Claude
Code, Cursor, aider — it's just markdown files).

**What it will never do: predict price direction.** This project grew out of
an experiment that tested exactly that across ~9,600 backtested predictions —
and failed honestly. The tool is built around what survived validation, and
ships its receipts (`backend/analysis/*_validation.json`) — regenerate them
on your own symbols; they were computed on one 20-ticker basket.

## Quick start (zero API keys)

Requirements: Python 3.11+, Node 18+.

```bash
git clone <this repo> && cd tickerlens
mkdir -p ~/.tickerlens && cp config.example.toml ~/.tickerlens/config.toml
# edit ~/.tickerlens/config.toml → set user.name + user.contact_email
pip install yfinance          # tier-0 price data, no signup
./run_app.sh                  # → http://localhost:5174
```

Try it with fake data first: `TICKERLENS_MOCK=1 ./run_app.sh`.

## Bring your own providers

Every data need is a *capability* (quotes, history, option chain, account,
news, …) mapped to a provider in `~/.tickerlens/config.toml`. Bundled:
**Schwab** (reference broker: quotes/history/options/account, read-only),
**Finnhub** (free key), **StockTwits**, **SEC EDGAR**, **FINRA**,
**yfinance** (zero-key). Anything unconfigured degrades cleanly — no options
provider simply means no options features, not errors. Secrets live in
`~/.tickerlens/.env`; the repo never sees them.

## Subscriptions & API keys

The registry of individual subscriptions lives in
`backend/providers/base.py` → `SUBSCRIPTIONS` (single source of truth; the
setup wizard derives its key prompts from it). What each bundled provider
needs:

| Provider | Secrets (name → env fallback) | Cost | Sign up |
|---|---|---|---|
| schwab | `schwab_app_key` → `SCHWAB_APP_KEY`, `schwab_app_secret` → `SCHWAB_APP_SECRET` | free with a Schwab brokerage account | developer.schwab.com |
| finnhub | `finnhub_api_key` → `FINNHUB_KEY` | free tier (60 calls/min) | finnhub.io/register |
| yfinance | none (tier-0) | free | `pip install yfinance` |
| edgar | none — SEC requires a contact email (`user.contact_email` in config.toml) | free (fair-use) | — |
| finra | none | free | — |
| stocktwits | none | free | — |

**Storing secrets — most secure first.** On any OS, put keys in the operating
system's secret store via [keyring](https://pypi.org/project/keyring/) under
the service name `tickerlens`:

```bash
backend/.venv/bin/python -m keyring set tickerlens <secret_name>   # macOS Keychain / Linux Secret Service
```

```bat
backend\.venv\Scripts\python -m keyring set tickerlens <secret_name>   # Windows Credential Manager
```

Legacy macOS entries created with
`security add-generic-password -s <secret_name> -a "$USER" -w` are still
honored. Fallback: plain env vars, or `~/.tickerlens/.env` (written by the
setup wizard, `chmod 600`) — config.py merges `.env` into the process
environment at startup.

**Precedence** (first hit wins): keyring → legacy macOS Keychain → environment
(`.env` feeds the environment). Footgun: if you rotate a key in `.env` but a
stale copy still sits in keyring, the stale keyring entry **wins** — delete it
(`python -m keyring del tickerlens <secret_name>`) or set
`TICKERLENS_NO_KEYRING=1` to bypass the OS stores entirely.

**Different broker or data service?** Open this repo with an AI coding agent
and point it at **`CLAUDE.md`** — it contains the full provider contract
(capability shapes, conventions, tests) so the agent can implement your
service and walk you through setup. That file is the contributor manual,
written for AI-assisted onboarding on purpose.

## The honest parts, briefly

Per-section freshness + graceful degradation everywhere; validation receipts
cite their basket and window; the Setup Score carries a permanent disclaimer;
probabilities in the options lens display their measured error and flag the
tail zone where history flatters; the app is read-only by construction — it
cannot place orders. Research & education only. **Not investment advice.**

## Docs

- `CLAUDE.md` — AI-session manual: architecture, provider contract, setup help (canonical)
- `GEMINI.md` — the same constitution + guides for Gemini CLI users
- `OSS_DESIGN.md` — the architecture rationale
- `DEMO.md` — temporary public-URL demos (Cloudflare quick tunnel)
- In-app **Guide** tab — user manual + full glossary

License: Apache-2.0 (see LICENSE, NOTICE).

## Importing an existing instance

Moving from another TickerLens installation (or a private fork)? One command
migrates your watchlist, score/portfolio/VRP history, discussions (with
decision prices and stances intact), discovery candidates, settings, private
notes, and your Schwab token — into `~/.tickerlens`, never into this repo:

```bash
cd backend
python3 migrate_instance.py \
  --source-db  /path/to/old/backend/tickerlens.db \
  --source-discussions /path/to/old/discussions \
  --source-discoveries /path/to/old/discoveries \
  --source-repo /path/to/old \
  --source-token /path/to/schwab_token.json
```

The source is opened read-only, the destination DB is backed up first, and
reruns are idempotent. Add `--dry-run` to preview.
