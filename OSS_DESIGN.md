# TickerLens as an Open-Source, Local-First Product — Design

> Goal: anyone clones/installs, brings THEIR OWN credentials (their broker,
> their free/paid data keys), runs entirely locally. the maintainer's instance keeps
> Schwab; others plug in what they have — including nothing.

## 1. The core abstraction: capabilities, not vendors

Today the composer knows "Schwab does quotes." Tomorrow it asks a REGISTRY for
a *capability*; a user config maps capabilities to installed providers:

```toml
# ~/.tickerlens/config.toml
[user]
name = "Jane"                 # used in prompts ("discussing with {name}")
contact_email = "j@x.com"     # REQUIRED for SEC EDGAR User-Agent

[capabilities]
quotes          = "schwab"    # or yfinance | alpaca | polygon
daily_history   = "schwab"
option_chain    = "schwab"    # or "none" → premium lens + implied-move hide
account         = "schwab"    # or "csv" | "none" → Portfolio manual/hidden
fundamentals    = "finnhub"
news_headlines  = "finnhub"
analyst_recs    = "finnhub"
earnings        = "finnhub"
social          = "stocktwits"
insiders        = "edgar"
short_interest  = "finra"
symbol_search   = "finnhub"
```

Each provider module ships a small manifest: capabilities implemented, auth
needed (none | api_key | oauth), rate limits, and its mock fixtures. Missing
capability ≠ error — it flows through the EXISTING per-section degradation
contract (ok/stale/unavailable) and feature gating: no option_chain → lens
never renders; no account → Portfolio tab offers CSV import or hides; no
analyst provider → the score reweights (already does). **The degradation
machinery we built for API failures IS the multi-provider story.**

`GET /api/capabilities` tells the frontend what exists, which provider backs
it, and its health — the UI renders accordingly and shows "via Schwab" /
"via yfinance" chips for transparency.

## 2. Provider tiers shipped with the repo

- **Tier 0 — zero signup (the first-run experience):** yfinance (quotes,
  history, fundamentals-ish, earnings; labeled "unofficial"), StockTwits,
  SEC EDGAR, FINRA, mock. Clone → run → working app with bands, screener,
  discussions, partial score. No keys. This is what makes adoption real.
- **Tier 1 — free keys:** Finnhub (sentiment/recs/earnings/search),
  Alpha Vantage, Polygon free.
- **Tier 2 — brokers (BYO):** Schwab (reference implementation, ships),
  then community: Alpaca (easiest, free paper accounts), Tradier
  (options-friendly), IBKR. NOT bundled: anything violating a broker's ToS
  (unofficial Robinhood etc.) — project policy.

Community contribution = one file in `providers/` + manifest + mock fixtures
+ passing the capability CONTRACT TESTS (an abstract pytest suite per
capability asserting response shape/semantics — we already do this informally
with mocks; formalize as the CI gate). `PROVIDERS.md` documents the spec.

## 3. Instance directory — user data OUT of the repo tree

`~/.tickerlens/` (override: `TICKERLENS_HOME`):
```
config.toml     # capability mapping + user identity (no secrets)
.env            # secrets fallback (chmod 600)
tickerlens.db   # THE database
discussions/    # markdown files (the LLM loop)
```
Why this matters beyond tidiness: `git pull` can never touch user data, the
repo stays clean for contributors, and the entire "a script deleted the DB"
accident class dies structurally — user data no longer lives where code runs.
Legacy in-repo layout stays supported for existing instances (the maintainer's).

**Secrets resolution order:** env vars → `~/.tickerlens/.env` → OS keyring
via the `keyring` lib (macOS Keychain / Windows Credential Manager / Linux
Secret Service — replaces our mac-only `security` calls) → setup wizard
prompt. Secrets never in config.toml.

## 4. First-run setup wizard (web, not docs)

On boot with no config: frontend routes to `/setup` — pick a provider per
capability (only implemented ones shown), paste keys, per-provider **Test
connection** button (existing health_check pattern), write config.toml.
Schwab's OAuth dance becomes a guided step (port of schwab_auth). CLI
equivalent (`tickerlens init`) for headless people.

## 5. The honesty machinery must travel — or it becomes false advertising

Our coverage/breach receipts were validated on ONE basket (20 US large-caps,
2024–26). Shipping those numbers as if universal would be exactly the
survivorship-marketing this project exists to reject. Therefore:
- `tickerlens validate` — first-class command that replays band engines and
  breach calibration ON THE USER'S OWN watchlist/history and regenerates
  `band_validation.json` / `breach_validation.json` locally.
- UI receipts always cite basket + window; "revalidate on your symbols"
  becomes a Settings button.
- The direction-prediction prohibition graduates from CLAUDE.md into a
  project constitution (`PRINCIPLES.md`): no direction predictions, read-only
  by design (no order placement code, ever), local-only (no telemetry, no
  accounts, no cloud), every quantitative claim carries its validation.

## 6. LLM-agnostic discussions

The "Ask Claude" loop is already just a file contract: markdown + frontmatter
into `discussions/`. Rename in docs to "research discussions": works with
Claude Code, Cursor, aider, a human in a text editor — anything that can
write a file. Prompt template moves to a configurable template with
`{user.name}` substitution. Claude stays the reference workflow.

## 7. Packaging & licensing

- **License:** Apache-2.0 (explicit patent grant + liability posture suit a
  finance tool) + NOTICE + the not-advice disclaimer already in the UI/README.
- **Install paths:** (a) git clone + `./run_app.sh` (works today; make it
  Linux-friendly, `open` → `xdg-open` fallback); (b) `pipx install
  tickerlens` + `tickerlens serve` — FastAPI serves the BUILT frontend
  (vite build → static) so non-dev users never touch npm; (c) Dockerfile as
  a distribution nicety (still local-only).
- **Public repo = fresh curated export, NOT this repo flipped public.**
  This repo's history/WORKLOG/DESIGN_QUESTIONS/seed file contain the maintainer's
  positions, watchlist, and decisions. Publishing = new repo, squashed
  history, generic seed, personal docs excluded. Non-negotiable privacy line.

## 8. Migration phases (each shippable, instance keeps working)

- **A — de-personalize + instance dir** (S): config.toml loader, secrets
  resolver w/ `keyring`, `{user.name}`/contact_email in prompts & EDGAR UA,
  TICKERLENS_HOME with legacy fallback, generic seed handling.
- **B — capability registry** (M): manifests, registry, composer/routes ask
  capabilities, `/api/capabilities`, UI gating + provider chips.
- **C — Tier-0 + wizard** (M): yfinance provider (labeled), portfolio CSV
  mode, `/setup` wizard + `tickerlens init`, cross-platform run scripts.
- **D — community + distribution** (M): contract-test harness, PROVIDERS.md,
  CONTRIBUTING.md, PRINCIPLES.md, LICENSE/NOTICE, `tickerlens validate`,
  pipx packaging + built-frontend serving, Dockerfile, stranger-facing README,
  curated public repo export.

Order of value: A is hygiene we want anyway; B is the architecture; C is
adoption; D is community. A+B first.
