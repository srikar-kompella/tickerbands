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

- `CLAUDE.md` — AI-session manual: architecture, provider contract, setup help
- `OSS_DESIGN.md` — the architecture rationale
- `DEMO.md` — temporary public-URL demos (Cloudflare quick tunnel)
- In-app **Guide** tab — user manual + full glossary

License: intended Apache-2.0 — add the LICENSE file before publishing.
