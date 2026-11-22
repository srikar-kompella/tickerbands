# Research report — field audit

Every number the research report (`/api/analysis/<TICKER>/report`) renders, where it comes from, when it is true, which price it uses, how it is computed, and what checks it before it is shown.

**Rule:** a blank with a visible warning is acceptable; a wrong number is not. Any field that fails a check renders `—` with a ⚠ chip naming the two numbers that disagree.

**Code:** `backend/analysis/price_basis.py` (the price), `backend/analysis/report_view.py` (fields + checks), `backend/analysis/news_relevance.py` (headlines, news-check flags), `backend/api/report.py` (rendering only — it computes nothing).

## The report price

Chosen once per report by `price_basis.build()`:

| Market state (NY clock, NYSE holiday calendar 2025–2027) | Report price | Day move |
|---|---|---|
| Open (9:30–16:00, 13:00 on early-close days) | live regular-session last (broker quote; withheld if older than 10 min or served from a stale cache) | live ÷ prior official close − 1 |
| Pre-market, after hours, weekend, holiday | official close of the last completed regular session (daily bar; the broker's regular-session last print when the bar is not yet published) | that close ÷ the prior official close − 1 |

An extended-hours quote is shown only as its own labeled line ("after hours, 8:00 PM ET Fri"). The word "today" appears only when the session really is today.

## Fields

As-of column: when the value is true. Basis column: which price it uses (**RP** = report price).

| Key | Label | Source | As of | Basis | Formula | Check |
|---|---|---|---|---|---|---|
| `price` | Price | daily bars (official close) or broker live quote | price label (e.g. "Official close · Fri Nov 20, 2026, 4:00 PM ET") | RP | see table above | quote age / cache status (live only) |
| `after_hours` | Extended hours | broker quote, extended session | quote time | not used for anything | ext. last ÷ official close − 1 | — |
| `day_move` | Day move | daily bars / live quote | session date | RP vs prior official close | RP ÷ (prior close − distribution going ex that day) − 1 | equals price ÷ prior close (±0.01pp) |
| `day_move_sigma` | Move in σ | TickerBands | session date | — | move % ÷ daily σ; σ = 20-day realized vol through the PRIOR session (the band that applied); outside when \|z\| ≥ 1.28 | z = move ÷ σ (±0.05); band status agrees with \|z\| |
| `band_next` | 80% expected range | TickerBands engine (flat20/ewma/conformal) | anchor date | official close (next session) or prior close (today's session when open) | anchor × (1 ± 1.28σ) | warns when an extended-hours price is already outside it |
| `market_cap` | Market cap | shares outstanding (provider profile) | RP time | RP | RP × shares outstanding | within 1% of price × shares; vendor figure at another price flagged |
| `eps_ttm` | EPS (TTM) | sum of last 4 reported quarters' diluted EPS (statements) → Yahoo trailingEps → Finnhub | latest quarter end | — | Σ 4 quarters | sources disagreeing >50% → withheld; >5% → noted |
| `pe_ttm` | P/E (TTM) | derived | RP time | RP | RP ÷ TTM EPS | within 1% of price ÷ EPS; non-operating-items flag |
| `fcf_ttm` | Free cash flow (TTM) | statements | latest quarter end | — | Σ 4 quarters (OCF − capex) | missing-latest-quarter warning |
| `p_fcf` | P/FCF (TTM) | derived | RP time | RP | market cap ÷ TTM FCF | within 1% of market cap ÷ FCF |
| `ev` | Enterprise value | market cap + balance sheet | balance-sheet date | RP | market cap + total debt − cash & ST investments − marketable securities | exact identity (±0.1%) |
| `net_cash` | Net cash | balance sheet | balance-sheet date | — | cash & ST investments + marketable securities − debt (breakdown shown) | — |
| `gross_margin` | Gross margin (TTM) | statements (vendor fallback, labeled) | latest quarter end | — | TTM gross profit ÷ TTM revenue | missing-quarter warning |
| `operating_margin` | Operating margin (TTM) | statements | latest quarter end | — | TTM operating income ÷ TTM revenue | missing-quarter warning |
| `net_margin` | Net margin (TTM) | statements | latest quarter end | — | TTM net income ÷ TTM revenue | non-operating ≥ 5% of pretax → flag |
| `net_margin_q` | Net margin (latest quarter) | statements | quarter end | — | quarter NI ÷ quarter revenue | — |
| `revenue_growth` | Revenue growth (TTM YoY) | Finnhub vendor metric (not recomputed) | vendor fetch date | — | vendor | refetched / flagged when fetched before the last earnings release |
| `revenue_growth_q` | Revenue growth (latest quarter YoY) | statements (SEC filing or Yahoo, per statement) | quarter end | — | quarter revenue ÷ same quarter a year earlier − 1 | needs both quarters 345–385 days apart |
| `div_yield_forward` | Dividend yield (forward) | distribution history | history as-of | RP | latest declared distribution × payments/yr ÷ RP | irregular schedule → not computed |
| `div_yield_trailing` | Dividend yield (trailing 12 mo) | distribution history | history as-of | RP | Σ distributions ex-dated in last 365 days ÷ RP | — |
| `beta` | Beta vs SPY | TickerBands, daily bars | last session | — | OLS slope, 60 daily returns vs SPY | the only beta shown; vendor beta suppressed |
| `correlation` | Correlation to SPY | TickerBands | last session | — | Pearson, 60 daily returns | — |
| `week52` | 52-week position | broker 52w range | quote | RP | (RP − low) ÷ (high − low) | below-low flagged |
| `rv20` | Realized vol (20d) | TickerBands | last session | — | stdev of 20 returns × √252 | — |
| `put_call` | Put/Call (session volume) | broker option chain | chain time | — | put vol ÷ call vol | closed-market note |
| `atm_iv` | ATM implied vol | broker option chain | chain time; **restated to the last close** when read over a closed market | — | mean ATM call/put IV; over a closed market × √(time to expiry at read ÷ at the close), in hours to the expiry's 4 PM ET close | restated value carries a ⚠ naming the broker's own figure (the broker re-solves IV from frozen marks as the clock runs: NVDA read 115.5% Sat and 132.5% Sun on the same ±8.05% straddle; both restate to ≈98.7%) |
| `implied_move` | Implied move (n sessions) | broker option chain | chain time | spot at chain time | ATM straddle ÷ spot | labeled in trading sessions to the expiry (named in the basis), not calendar days — a Friday-close straddle to Monday is 1 session whether read Sat or Sun |
| `implied_vs_band` | Implied move vs our band | derived | chain time | — | implied ÷ (band half-width × √sessions to expiry) | same rule in the Watchlist column |
| `news_sentiment` | News sentiment | Finnhub aggregate or lexicon over RELEVANT headlines | fetch | — | bullish vs bearish share, n shown | withheld below 10 articles |
| `headlines` | Headlines | Finnhub company news, relevance-filtered | window shown | — | kept only items naming the company | count kept/fetched shown |
| `regulatory` | Regulatory & legal pass | SEC EDGAR submissions (8-K items named) + relevance-filtered headlines | window end | — | 8-Ks since the window start; headlines whose HEADLINE (not only the blurb) matches SEC/DOJ/AG/subpoena/probe/class action/export rules/restatement; reports first, commentary (questions, "what it means for you", listicles) last | red-flag 8-K items (1.05, 2.06, 3.01, 4.01, 4.02) raise a chip; an empty headline scan says the feed is not a full search |
| `move_cause` | Reported cause of the move | relevant headlines ±1 day of the session + SEC filings | session date | — | keyword candidates, not proof; ranked: a regulator/probe/lawsuit named in the headline, then another cause word in the headline, then blurb-only; the session's own date first; commentary pushed last | only after a ≥2σ session; none found → "cause not found — verify before acting" |
| `claude_news` | Research news check | stored discussion (written by the research session) | its timestamp | — | not computed | flagged if written before any completed ≥2σ session; if a ≥2σ session inside its news window is left without a cause (`move_cause: not_found`; a v5 note with anything other than `move_cause: found`; "no material news" wording in a note from before v5); if its research was partial/failed or its `research_status` / `move_cause` is unreadable; if it is not the newest note's check (that note's `news_view` is absent or unreadable); or if it is ≥3 days old. Judged against every ≥2σ session in the daily bars, so the flag stays after later ordinary sessions |
| `claude_stance` | Research stance | stored discussion | its timestamp | — | not computed | flagged if it predates any completed ≥2σ session or its own discussion has a failed news check; v5 checks add: superseded by a later note, a newer note whose stance is unreadable or (v5) missing, `owned: no` on a position the account holds, research incomplete, hold/watch with no upgrade/downgrade conditions, `review_by` passed or unreadable, cause of a ≥2σ move not found, `price_used` unreadable / an extended-hours price / more than 0.5% from the session close when written with the market closed (3% or inside the day's range when written during the session), required v5 fields missing or unreadable, a step 3 trigger met. `sell` on a name not owned displays as AVOID (the note's `owned`, else the account's position data; a v5 note with neither is shown as AVOID). Conditions, review date and confidence print under it. See `docs/RESEARCH_PROMPT.md` |
| `social` | Social (StockTwits) | StockTwits stream | fetch | — | (bull − bear) ÷ tagged, n shown | withheld below 10 tagged messages |
| `analyst_actions` | Analyst rating changes (30 days) | Yahoo dated rating actions (report only) | report date | — | count of downgrades/upgrades, newest listed | never feeds the Setup Score |
| `analyst_mix` | Analyst mix | Finnhub monthly snapshot | snapshot month | — | counts by rating | snapshot older than 62 days → not shown; identical to the prior month → flagged stale (with Yahoo's count of rating changes since) |
| `earnings_next` | Next earnings | Finnhub calendar + Yahoo `isEarningsDateEstimate` | report date | — | next scheduled date, days recomputed at render | must be tagged confirmed/estimated; past dates dropped; source disagreement shown |
| `position_value` | Position value | broker share count | RP time | RP | shares × RP (other share class linked, not revalued) | equals shares × RP |
| `position_pl` | Unrealized P/L | broker cost basis | RP time | RP | value − cost basis | follows position_value |
| `position_day_pl` | Day P/L | derived | session | official closes | shares × (RP − prior close) | \|day P/L\| ≤ position value; broker figure shown only as a note, rejected if larger than the position |
| `position_weight` | Portfolio weight | broker account total | account time | RP | value ÷ (account total − broker mark + revalued position) | follows position_value |

## Other rendered elements

| Element | Source | Notes |
|---|---|---|
| Data-as-of block | `report_view.data_as_of` | price time, statement period (+ missing-quarter warning), headline window, research news-check time, required news window |
| Setup Score table | `analysis/setup_score.py` | empty feeds contribute nothing ("no data"); sample size shown; news < 10 articles and social < 10 tagged messages withheld; analyst trend needs two snapshots, the latest ≤ 62 days old. The value lens never feeds it. |
| Sparkline | last 30 completed daily closes + band | — |
| Band coverage line | `band_coverage.json` | basket-wide figure labeled as such |
| Research discussions | stored markdown | each carries "Written <date> — figures are as of that date" and an OK / STALE / INVALID / SUPERSEDED status chip: SUPERSEDED when a later note lists it in `supersedes`; INVALID when research was partial/failed, the cause of a ≥2σ move was not found or (v5) not stated, or "no material news" right after one; STALE when a ≥2σ session closed after it was written. Each note is judged against every ≥2σ session in the daily bars, not only the last session (`docs/RESEARCH_PROMPT.md`) |
| Recorded decisions | decision journal | "At report price" column uses the report price |

## Statements: sources and freshness

Primary source: **SEC XBRL company facts** (the numbers as filed in the 10-Q/10-K, with form and filing date). Fallback: Yahoo statements. The two are merged **per statement** (income statement, cash flow, balance sheet): for each, the source whose latest *complete* period is newest wins; SEC wins ties. Each field is labeled with its own statement's period and source.

TTM from filings: `TTM = YTD + prior FY − prior-year YTD` (10-Q cash flows are year-to-date only); quarters are the 3-month facts, or YTD differences (Q4 = FY − 9 months).

When the last earnings release postdates the cached copy (or the copy predates the current format), both sources are refetched before any TTM figure is computed. Any statement whose period ends more than 95 days before the release carries "does not include the latest quarter" on every field that depends on it. If the release itself is on EDGAR (8-K item 2.02), the warning says the results are only in that release until the 10-Q is filed, and the research prompt tells the session to read it.

Balance-sheet notes: SEC debt = long-term debt (current + noncurrent) + short-term borrowings, **excluding leases**; Yahoo "Total Debt" may include leases. Marketable equity securities are reported only from the SEC tag `EquitySecuritiesFvNiCurrent` — Yahoo's investment rows mix categories, so they are not used.
