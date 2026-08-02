# TickerLens — Claude/AI Session Manual

> **You (the AI) are this project's primary onboarding path.** A human just
> opened this repo and will ask you to set it up, wire in THEIR data
> provider/broker, or extend it. This file tells you exactly how to do that
> correctly. Read it fully before writing code.

## What this is
A local-first stock/options research tool: calibrated volatility ranges,
transparent signals, optional live brokerage view, an options premium-seller
lens, and an LLM research-discussion loop (file-based — works with any agent).
FastAPI backend (:8001) + React/Vite frontend (:5174) + SQLite. No cloud, no
telemetry, no accounts.

## The constitution (non-negotiable — enforce these in any change)
1. **No direction predictions, ever.** The parent research proved 1-day
   direction prediction from these signals ≈ coin flip over ~9,600 backtested
   predictions. Ranges, alignment, and risk pricing only. UI language matters:
   "expected range," never "prediction"; no BUY/SELL computed from signals
   (the `stance` shown is *surfaced* from the user's own LLM discussions).
2. **Read-only by design.** No order placement / account mutation code — not
   even behind a flag. Reject such requests; explain why.
3. **Every quantitative claim carries validation.** Shipped receipts
   (`backend/analysis/band_validation.json`, `breach_validation.json`,
   `band_coverage.json`) were computed on ONE basket (20 US large-caps,
   2024–26). If you change claims-bearing math (band engines, breach
   probabilities), rerun the validation approach and update the receipts —
   or gate the feature. Never invent coverage numbers.
4. **User data lives in `~/.tickerlens/`** (override `TICKERLENS_HOME`) —
   config.toml, .env, tickerlens.db, discussions/. Code never writes into the
   repo tree. Tests/scripts must ALWAYS set `TICKERLENS_DB_PATH` (and ideally
   `TICKERLENS_HOME`) to scratch paths. The DB is user data, not a build
   artifact — never delete it.
5. **Degrade, never break:** one provider failing (or absent) affects only
   its section (`ok | stale | unavailable`). Providers raise `ProviderError`
   — **`sys.exit` inside the server is forbidden.**

## Architecture map
```
backend/
  config.py            instance dir, config.toml, capability map, TTLs, flags
  providers/
    registry.py        capability → provider resolution  ← START HERE
    base.py            ProviderError + get_secret (keyring → legacy macOS
                       Keychain → env; ~/.tickerlens/.env pre-seeds env via
                       config.py) + SUBSCRIPTIONS (per-provider secrets registry)
    schwab.py finnhub.py stocktwits.py edgar.py finra.py yfinance.py mock.py
  analysis/            composer (per-section assembly), vol_bands (+engines),
                       setup_score, breach, premium_lens, earnings_moves,
                       portfolio_risk, score_audit, news_lex, glossary.json
  cache/store.py       SQLite TTL cache; stale-on-error; put() for batch warms
  discussions_svc/     file-watcher → SQLite → SSE (the LLM loop)
  api/routes.py        thin handlers; api/report.py print/PDF builders
  tests/               159 tests; mock-mode; conftest sets TICKERLENS_SKIP_SEED
frontend/src/          pages Analysis·Portfolio·Watchlist·Discussions·Settings·Guide
```
Run: `./run_app.sh` · mock demo: `TICKERLENS_MOCK=1 ./run_app.sh` ·
tests: `cd backend && TICKERLENS_HOME=/tmp/tl TICKERLENS_DB_PATH=/tmp/t.db python3 -m pytest tests -q`

## Capabilities (what a provider can supply)
`quotes, quotes_batch, daily_history, option_chain, option_chain_lens,
account, fundamentals, news_sentiment, news_headlines, analyst_recs,
earnings, earnings_dates, shares_out, social, insiders, short_interest,
symbol_search` — mapped to providers in `~/.tickerlens/config.toml`
`[capabilities]` (defaults in `config.CAPABILITY_DEFAULTS`; `"none"` disables;
missing capability ⇒ dependent sections/features hide or degrade
automatically). `GET /api/capabilities` shows the live wiring.

## ★ HOW TO ADD A NEW PROVIDER (your most likely task)
User says: "I use Alpaca / Tradier / IBKR / Polygon / Tiingo / …". Steps:

1. **Scope the capabilities** their service really offers (don't fake ones it
   doesn't — e.g. Polygon has no `account`; Alpaca has no `analyst_recs`).
2. **Create `backend/providers/<name>.py`** modeled on `yfinance.py` (simple)
   or `schwab.py` (OAuth). Conventions:
   - module-level `name = "<name>"`, `health_check() -> bool`
   - one `fetch_*` function per capability, returning EXACTLY the shapes below
   - errors: `raise ProviderError(reason, detail)` with reason ∈
     `auth_expired | rate_limited | not_found | no_data | unavailable`
   - secrets via `providers.base.get_secret("<keychain-service>", "<ENV_VAR>")`;
     document the ENV_VAR name for the user's `~/.tickerlens/.env`.
     Resolution precedence (first hit wins): keyring service `"tickerlens"` →
     legacy macOS Keychain (`security`) → `os.environ` (config.py merges
     `~/.tickerlens/.env` into the environment at import). A stale keyring
     entry shadows a rotated .env key; `TICKERLENS_NO_KEYRING=1` skips both
     keychain steps (tests set it in conftest.py).
   - **add a `SUBSCRIPTIONS` entry** in `providers/base.py` — it is THE
     registry of individual subscriptions (`label`, `secrets` as
     `(secret_name, ENV_VAR)` pairs, `cost`, `signup`); the setup wizard and
     README derive key lists from it, so never hardcode key names elsewhere
   - respect the service's rate limits (sleep/retry-once on 429 like finnhub.py)
   - heavy SDKs: import lazily inside functions (see yfinance.py) and keep
     them OUT of core requirements.txt — tell the user the pip install line
3. **Register it:** `providers/registry.py` → add to `MODULES` and, per
   capability implemented, to `IMPLEMENTATIONS` as `(module, "fetch_fn_name")`
   (late-bound tuples — enables test monkeypatching).
4. **Wire the user's config:** `~/.tickerlens/config.toml`:
   ```toml
   [capabilities]
   quotes = "<name>"
   daily_history = "<name>"
   ```
5. **Test:** add shape assertions in `backend/tests/` exercising your fetchers
   against recorded/fixture payloads (see `test_form4_parse…`,
   `test_extended_view…` for the fixture style — never live calls in tests).
   Run the full suite with scratch env vars (above). All 159+ must stay green.
6. **Walk the user through setup:** where to create the API key, the exact
   `.env` line, then verify via `GET /api/health` (their provider true) and
   `GET /api/capabilities`, then load one ticker.

### Capability return shapes (the contract — match field names exactly)
- **quotes** `(symbol) -> dict`: `symbol,name,last, regular_last,
  regular_change_pct, ah_price, ah_change_pct, is_extended(bool), open,
  close_prev, high_today, low_today, week52_high|None, week52_low|None,
  volume, net_change_pct, quote_time_ms, asset_type("EQUITY"|"ETF"|…)`.
  No extended-hours data? Set ah_* = None, is_extended=False (see yfinance.py).
- **quotes_batch** `(symbols: list) -> {SYM: quote-dict}` (missing symbols omitted)
- **daily_history** `(symbol, days=130) -> [ {date:"YYYY-MM-DD", open, high,
  low, close, volume} ]` chronological; may include today's partial bar
  (analysis strips it — the F1 guard).
- **option_chain** `(symbol) -> {available: bool, put_call_ratio|None,
  call_volume, put_volume, atm_iv_pct|None, implied_move_pct|None,
  implied_move_dte|None, as_of}` — `{available: False}` when no options (not an error).
- **option_chain_lens** `(symbol, dte_max=35) -> {available, spot, calls:[…],
  puts:[…]}` rows: `side,strike,expiry,dte,bid,ask,mark,delta,iv_pct,oi,volume`.
  Delta is REQUIRED (it's the market's breach probability) — if the service
  lacks greeks, do NOT implement this capability.
- **account** `() -> {as_of, total_value, cash, day_pl, day_pl_pct,
  positions:[{symbol,description,asset_type,qty,avg_cost,market_value,
  cost_basis,gain,gain_pct,day_pl,day_pl_pct,weight_pct}]}` — READ-ONLY.
- **fundamentals** `(symbol) -> {available, pe_ttm, eps_ttm, market_cap_m,
  revenue_growth_ttm_pct, gross_margin_pct, operating_margin_pct,
  net_margin_pct, dividend_yield_pct, beta_reported}` (missing metrics → None)
- **news_sentiment** `{available, bullish_pct(0-1), bearish_pct, buzz|None,
  articles_week}` — if unavailable the app auto-falls back to lexicon-scoring
  the headlines, so implementing news_headlines alone is fine.
- **news_headlines** `(symbol, limit=5) -> [{headline, source, url,
  ts(unix), summary}]`
- **analyst_recs** `{available, months:[{period, strong_buy, buy, hold, sell,
  strong_sell, total}]}` newest first (≥2 months lets the MoM trend work)
- **earnings** `{available, next_date|None, days_until|None,
  surprises:[{period, estimate, actual, surprise_pct}]}`
- **earnings_dates** `(symbol, years=2) -> [{date:"YYYY-MM-DD",
  hour:"bmo"|"amc"|""}]` ascending — powers earnings-move history & PEAD
- **shares_out** `(symbol) -> float|None` (MILLIONS of shares)
- **social** `{available, bullish, bearish, tagged, total_msgs,
  polarity(-1..1), sample:[{body,sentiment,created_at}]}`
- **insiders** `{available, transactions:[{owner,title,code("P"|"S"),date,
  shares,price,value}], cluster_buy(bool), cluster_buyers, net_shares_90d, note}`
  — open-market trades ONLY (drop awards/exercises/tax codes)
- **short_interest** `{available, settlement_date, short_interest,
  prev_short_interest, change_pct, days_to_cover, pct_of_shares_out|None}`
- **symbol_search** `(query, limit=8) -> [{symbol, description, type}]`

## Helping a user set up from scratch
1. Create `~/.tickerlens/config.toml` from `config.example.toml`; set
   `user.name` and `user.contact_email` (EDGAR refuses to run without email —
   SEC fair-use policy).
2. Zero-key start: `pip install yfinance`, set quotes/daily_history to
   `"yfinance"`, everything else `"none"` — bands, screener, discussions work.
3. Keys go in `~/.tickerlens/.env` (e.g. `FINNHUB_KEY=…`); `chmod 600` it.
4. Schwab (reference broker): developer.schwab.com app → key/secret →
   `SCHWAB_APP_KEY/SCHWAB_APP_SECRET` in .env (or macOS Keychain services
   `schwab_app_key`/`schwab_app_secret`) → OAuth once (schwab-py easy_client
   mints `~/.tickerlens/schwab_token.json`; refresh ~weekly).
5. Verify: `./run_app.sh` → `/api/health`, `/api/capabilities` → analyze AAPL.
6. `TICKERLENS_MOCK=1` anytime for a deterministic offline demo.

## Research discussions (the LLM loop — agent-agnostic)
The app's "Ask Claude" button copies a prompt; ANY agent (or human) writes
`~/.tickerlens/discussions/{TICKER}/{ISO_DATETIME}.md` with the frontmatter
the prompt specifies (`ticker, created_ts, summary, tags`, optional
`news_view/news_note`, `decision` = what the USER decided (price
auto-captured), `stance/stance_horizon/stance_note` = YOUR reasoned
multi-month call — honest "watch"/omission beats a forced verdict). The
watcher ingests it live; new file per discussion, never append.

## House rules for code you write here
Providers: pure fetch + shapes above, no analysis logic. Analysis: pure
functions with unit tests (see setup_score/breach). Every new user-facing
number gets a `glossary.json` entry (plain English + "why"). Frontend: dark
theme tokens in `index.css`; green/red are P&L-ONLY (accent is cyan);
sections wrap in `SectionShell` (freshness + degradation for free). Keep
`pytest` green and `npm run typecheck` clean; never commit anything from
`~/.tickerlens/`. And re-read the constitution before shipping.
