# TickerLens — Gemini CLI Session Manual

> **You (the AI) are one of this project's onboarding paths.** A human opened
> this repo with Gemini CLI and will ask you to set it up, wire in THEIR data
> provider/broker, or extend it. This file is self-sufficient for those tasks.
>
> **Canonical source: `CLAUDE.md`.** This file mirrors its constitution and
> contracts for Gemini users. If anything here ever conflicts with CLAUDE.md,
> **CLAUDE.md wins** — read it for the full capability return shapes.

## What this is

A local-first stock/options research tool: calibrated volatility ranges,
transparent signals, an optional read-only brokerage view, an options
premium-seller lens, and a file-based LLM research-discussion loop that works
with any agent (including you). FastAPI backend (:8001) + React/Vite frontend
(:5174) + SQLite. No cloud, no telemetry, no accounts.

**It never predicts price direction.** The parent research backtested ~9,600
one-day direction predictions from these signals and got a coin flip. The tool
ships only what survived validation — ranges, alignment, risk pricing — with
its receipts (`backend/analysis/*_validation.json`).

## Hard rules (non-negotiable — enforce in every change)

1. **Never compute or display buy/sell/hold from signals.** No direction
   predictions, ever. UI says "expected range", never "prediction". The
   `stance` shown in the app is surfaced from the user's own discussion files,
   not computed.
2. **The API never trades.** No order placement / account mutation code — not
   even behind a flag. Refuse such requests and explain why.
3. **Never delete the user's database.** `~/.tickerlens/tickerlens.db` is user
   data, not a build artifact. Tests/scripts must ALWAYS point
   `TICKERLENS_DB_PATH` (and ideally `TICKERLENS_HOME`) at scratch paths.
4. **User data lives in `~/.tickerlens/`** (override with `TICKERLENS_HOME`):
   config.toml, .env, tickerlens.db, discussions/. Code never writes into the
   repo tree. Never commit anything from `~/.tickerlens/`.
5. **Degrade, never break.** A failing/absent provider affects only its own
   section. Providers raise `ProviderError(reason, detail)` with reason ∈
   `auth_expired | rate_limited | not_found | no_data | unavailable`.
   `sys.exit` inside the server is forbidden.
6. **Quantitative claims carry validation.** If you change claims-bearing math
   (band engines, breach probabilities), rerun the validation and update the
   receipts — or gate the feature. Never invent coverage numbers.

## Running it

```bash
mkdir -p ~/.tickerlens && cp config.example.toml ~/.tickerlens/config.toml
# edit config.toml → user.name + user.contact_email (EDGAR requires the email)
./run_app.sh                        # → http://localhost:5174
TICKERLENS_MOCK=1 ./run_app.sh      # deterministic offline demo
cd backend && TICKERLENS_HOME=/tmp/tl TICKERLENS_DB_PATH=/tmp/t.db \
    python3 -m pytest tests -q      # tests (scratch paths — rule 3)
cd frontend && npm run typecheck
```

Zero-key start: `pip install yfinance`, set quotes/daily_history to
`"yfinance"`, everything else `"none"`.

## Capability registry

Every data need is a *capability* mapped to a provider module in
`~/.tickerlens/config.toml` `[capabilities]` (defaults in
`backend/config.py::CAPABILITY_DEFAULTS`; `"none"` disables and the dependent
UI hides/degrades). Resolution lives in `backend/providers/registry.py`
(`MODULES` + `IMPLEMENTATIONS`). Capabilities:

`quotes, quotes_batch, daily_history, option_chain, option_chain_lens,
account, fundamentals, news_sentiment, news_headlines, analyst_recs, earnings,
earnings_dates, shares_out, social, insiders, short_interest, symbol_search`

`GET /api/capabilities` shows the live wiring; `GET /api/health` per-provider
status.

## Subscriptions & secrets contract

`backend/providers/base.py::SUBSCRIPTIONS` is THE registry of individual
subscriptions — per provider: `label`, `secrets` as `(secret_name, ENV_VAR)`
pairs, `cost`, `signup`. The setup wizard (`backend/api/setup.py`) and README
derive key lists from it; never hardcode key names elsewhere.

Reading a secret: `providers.base.get_secret("<secret_name>", "<ENV_VAR>")`.
Precedence (first hit wins):

1. `keyring.get_password("tickerlens", secret_name)` — OS secret store on any
   platform (store with `backend/.venv/bin/python -m keyring set tickerlens
   <secret_name>`).
2. Legacy macOS Keychain (`security find-generic-password -s <secret_name>`).
3. `os.environ[ENV_VAR]` — note `backend/config.py` merges
   `~/.tickerlens/.env` (chmod 600) into the environment at import time.

Footgun: a stale keyring entry shadows a rotated `.env` key.
`TICKERLENS_NO_KEYRING=1` skips steps 1–2 (tests set it in
`backend/tests/conftest.py`). Never write secrets into any file in the repo.

## Add your own provider (step by step)

1. **Scope honestly** which capabilities the service really offers — don't
   fake ones it doesn't (no greeks ⇒ don't implement `option_chain_lens`).
2. **Create `backend/providers/<name>.py`** modeled on `yfinance.py` (simple)
   or `schwab.py` (OAuth): module-level `name = "<name>"`,
   `health_check() -> bool`, one `fetch_*` function per capability returning
   EXACTLY the shapes documented in CLAUDE.md ("Capability return shapes").
   Errors via `ProviderError` (reasons above); retry once politely on 429;
   lazy-import heavy SDKs inside functions and keep them out of
   requirements.txt.
3. **Secrets:** call `get_secret("<secret_name>", "<ENV_VAR>")` and **add a
   `SUBSCRIPTIONS` entry** in `providers/base.py` for the new pair(s).
4. **Register:** `providers/registry.py` → `MODULES` plus, per capability,
   `IMPLEMENTATIONS` entries as `(module, "fetch_fn_name")` tuples.
5. **Wire config:** `~/.tickerlens/config.toml` `[capabilities]` →
   `quotes = "<name>"` etc.
6. **Test:** shape assertions in `backend/tests/` against fixture payloads —
   never live network calls in tests. Full suite must stay green with scratch
   env vars (see "Running it").
7. **Walk the user through it:** where to create the key, the exact keyring or
   `.env` line, then verify `/api/health` and `/api/capabilities`.

## Research discussions (the file contract)

Any AI — Gemini included — can write a discussion file; the watcher ingests it
live regardless of which agent wrote it. Path:

```
~/.tickerlens/discussions/{TICKER}/{ISO_DATETIME}.md
```

YAML frontmatter contract: required `ticker`, `created_ts`, `summary`, `tags`;
optional `news_view`/`news_note`; `decision` = what the USER decided (price is
auto-captured); `stance`/`stance_horizon`/`stance_note` = YOUR reasoned
multi-month view — an honest "watch" or omission beats a forced verdict, and a
stance is never a buy/sell instruction. New file per discussion, never append.
Malformed files land in `discussions/_failed/`.

## House rules for code you write here

Providers are pure fetch — no analysis logic. Analysis functions are pure and
unit-tested. Every new user-facing number gets a `backend/analysis/glossary.json`
entry. Frontend: dark-theme tokens in `index.css`; green/red are P&L-only;
sections wrap in `SectionShell`. Keep `pytest` green and `npm run typecheck`
clean. Re-read the hard rules before shipping — and when in doubt, defer to
`CLAUDE.md`.
