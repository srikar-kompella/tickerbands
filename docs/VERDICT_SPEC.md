# Verdict — specification

One ownership-independent verdict per security, set by the research session from a fixed rulebook, validated and displayed by the app. This replaces the ownership-dependent stance (buy / hold / trim / sell / watch) as the headline from prompt version 6.

**Principles that do not change:** the app never predicts short-term direction and never computes a buy/sell call from signals. The app supplies the rulebook (in the prompt) and the inputs (data); the research session applies them and writes the verdict; the app checks that the verdict follows the rulebook and shows it. Price trend is never a rule.

## 1. What is shown

`<Verdict> (<Value> · <Risk>)` — for example `Stretched (Expensive · Watch)`.

| Field | Values (frontmatter value → display) |
|---|---|
| `verdict` | `attractive` → Attractive · `fair` → Fair · `stretched` → Stretched · `avoid` → Avoid · `unrated` → Unrated |
| `value_grade` | `cheap` → Cheap · `fair` → Fair · `expensive` → Expensive · `paused` → Value paused |
| `risk_grade` | `clean` → Clean · `watch` → Watch · `red_flag` → Red flag |

The verdict describes the security, the same for every user. What it means for a given user is a separate, derived line (section 5).

## 2. Risk items — three kinds

| Kind | Frontmatter list | What belongs | Effect |
|---|---|---|---|
| Hard — a confirmed finding or event | `hard_flags` | restatement / non-reliance (8-K 4.02) · auditor resignation or dismissal (8-K 4.01) · late-filing notice (NT 10-Q / NT 10-K) · going-concern warning · delisting notice (8-K 3.01) · Wells notice · charges or a complaint filed by a regulator · regulator consent order · debt default or bankruptcy · for funds: closure, liquidation, merger, distribution suspended, strategy or index change · for banks: capital below "well capitalized", dividend suspended · for REITs: dividend cut | any one → `risk_grade: red_flag`, `verdict: avoid` |
| Soft — a confirmed fact about the business | `soft_signals` | see the rulebook (section 3); each must be confirmed by a filing, the company, or two periods of data; each entry starts with its rule key in brackets, e.g. `[ni_over_fcf] …` (keys in section 3) | 1 → shown only · 2 → `risk_grade: watch` and the verdict drops one step · 3 or more → `red_flag`, `avoid` |
| Watch-only — unconfirmed or still open | `watch_items` | press reports from unnamed sources · items whose only sources could not be opened ("not verified") · open investigations, subpoenas, civil investigative demands, requests for testimony and inquiries (even confirmed ones — an investigation is not a finding) · routine class actions after a price drop · lawsuits or regulator matters about a fund's sponsor rather than the fund itself · a filing later than the company's usual timing but inside its deadline · an upcoming lock-up expiry or similar calendar risk | never changes the verdict; never counts toward a red flag; with 0–1 soft signals, one or more watch items set `risk_grade: watch` for display only |

A regulatory or legal item is **hard** only when there are charges or a complaint filed by a regulator, a consent order, a Wells notice, or the company's own filing confirming the finding; anything short of that is watch-only. A watch item that becomes a finding moves to `hard_flags` in the next note; one that is dropped, denied or retracted is removed.

Summary: any hard → `red_flag` · 3+ soft → `red_flag` · 2 soft → `watch` · 0–1 soft with watch items → `watch` · otherwise `clean`.

**Data-quality gate.** A rule cannot fire on: a figure the report withholds or flags; a vendor figure that contradicts the filings; profit that includes one-time gains or tax benefits (use the adjusted figure and say so); fewer than four quarters of history. **Persistence.** A soft signal from financial data must hold for two consecutive quarters; from market data, five sessions.

## 3. Default rulebook (applies when the user's notes give no rule for the same thing)

Each soft signal has a **rule key**; the session starts each `soft_signals` entry with it in brackets (`[revenue_decline] Revenue down 4% and 6% y/y in the two quarters to 2026-09-30 (10-Q)`). The app computes what it can from filed statements in `rule_inputs` (section 6) and checks the session's list against it (section 8).

**Stocks — soft signals.** Unless stated otherwise, each is computed over the **last two reported quarters** and must hold in **both**:

| Key | Fires when |
|---|---|
| `revenue_decline` | revenue down year on year in both quarters |
| `op_margin_decline` | operating margin down more than 3 points year on year in both |
| `fcf_negative` | free cash flow negative in both (skipped when revenue is growing more than 25% year on year) |
| `receivables_vs_revenue` | receivables growth year on year exceeds revenue growth by more than 20 points in both |
| `inventory_vs_revenue` | inventory growth year on year exceeds revenue growth by more than 20 points in both |
| `ni_over_fcf` | net income (GAAP, less flagged one-time gains and tax benefits) exceeds free cash flow by more than 25% in both |
| `net_debt_ebitda` | net debt above 3× trailing EBITDA and higher than a year earlier |
| `interest_coverage` | operating income ÷ interest expense (trailing four quarters) below 3× |
| `dilution` | diluted share count up more than 5% year on year (not counted in the first four quarters after a listing) |
| `guidance_cut` | guidance cut announced in the last two quarters |
| `dividend_cut` | dividend cut announced in the last two quarters |
| `exec_exit` | abrupt CEO or CFO exit — counts for 12 months or until a permanent successor is named |
| `insider_selling` | three or more insiders selling outside 10b5-1 plans within 90 days |

**Funds — soft signals.** `distribution_cut` — latest distribution more than 10% below the 3-month average (monthly payers) or the average of the last four payments (quarterly and other payers) · `roc_nav` — distributions mostly return of capital for two quarters while NAV falls (not alone — some funds do this by design) · `nav_vs_index` — NAV total return trailing its index by more than 3 points over 12 months · `fee_increase` · `assets_decline` — assets down more than 30% · `nav_discount` — price persistently off NAV by more than 2% (ETFs) or 10% (closed-end funds). Leveraged or inverse funds carry a standing watch item about holding period.

**Banks — soft signals.** `npl_rise` — nonperforming loans up more than 50% year on year and above 1% of loans · `charge_offs` — net charge-offs rising two quarters · `capital_ratio` — core capital ratio down more than 1 point · `deposit_decline` — deposits down more than 5% in a quarter · `nim_decline` — net interest margin down two quarters · `efficiency_ratio` — non-interest expense ÷ revenue above 70% · `cre_concentration` — commercial real estate loans above 3× capital. The stock rules `revenue_decline` and `dilution` apply to banks as written.

**REITs — soft signals.** `payout_ffo` — payout above 100% of FFO/AFFO for two quarters · `occupancy` — occupancy falling · `reit_leverage` — net debt above 7× EBITDA.

**New listings.** A new listing is a security with **fewer than 252 completed sessions since its first trade** (about one year); the app sends `listed_under_a_year` and `sessions_listed` (`is_new_listing` stays the separate < 21-session flag for price history). For **stocks and banks** listed under a year, the verdict cannot be better than `fair` until one quarter of results as a public company is filed. **Funds have no cap.** Lock-up expiry within 30 days and the first earnings date are watch items. Valuation is against peers when there are fewer than three year-end points.

**ADRs / non-USD filers.** While share count or currency is unreconciled (the report withholds market cap), `value_grade: paused`.

## 4. Value grade and the verdict

**Main measure:** price to free cash flow; when free cash flow is negative or distorted, EV/EBIT or P/E; **loss-making companies or a negative measure:** price to sales (`p_sales`; EV/sales where net debt is known) against its own five-year history; banks: price to tangible book; funds: distribution rate versus its own history (broad index funds: the index's valuation). The app supplies `valuation_range` (section 6) — the measure now and at each of the last five fiscal year-ends.

| `value_grade` | Multiples (P/FCF, EV/EBIT, P/E, P/S, P/TBV) | Yield-type measures (fund distribution rate — direction inverted) |
|---|---|---|
| `cheap` | now ≥ 15% below its own five-year median | rate now ≥ 15% above its own five-year median |
| `expensive` | now above its own five-year high, or more than 1.5× the median | rate now below its own five-year low, or under two-thirds of the median |
| `fair` | in between | in between |
| `paused` | the measure cannot be computed reliably — data problems only (ADR/currency unreconciled, withheld inputs). **Never** because profits or cash flow are negative: use `p_sales` instead | same |

**Peers fallback.** With fewer than three year-end points (or `valuation_range` absent), grade against named peers from research: `cheap` when the measure is ≥ 25% below the peer median, `expensive` when ≥ 25% above, else `fair` (for a yield-type measure, inverted). Write the peers, their median and the gap in `value_basis`.

**Two hints.** When current profit or cash flow is more than double its five-year average, the app states two grades: `grade_hint` on the reported figure and `normalized_grade_hint` on the five-year average, and `grade` = the **less favorable** of the two (cheap + fair → fair; fair + expensive → expensive; cheap + expensive → expensive). Without normalization `grade` = `grade_hint`. `value_grade` should equal `grade`; the session may depart only with a stated reason in `value_basis` (one-time items, peers, a changed business — words such as "normalized", "one-time", "non-recurring", "peer", "instead of", "rather than", "depart", "override"). A two-step departure (cheap where `grade` is expensive, or the reverse) is always flagged. No normalization when the five-year average is ≤ 0 or any of those years was negative (a turnaround has no meaningful average): the reported grade stands alone. A year-end multiple more than 5× the median of the other years (a near-zero profit or cash-flow year) is left out of median / low / high and named in the warnings.

**Baseline:** cheap → attractive · fair → fair · expensive → stretched. **Steps** (best to worst): attractive → fair → stretched → avoid.

**Final verdict — apply in this order, first match wins:**

1. `research_status: failed` and **no** hard flag → `unrated`.
2. Any hard flag → `avoid` (even when research failed or value is paused).
3. 3 or more soft signals → `avoid`.
4. `value_grade: paused` (or too little clean data) → `unrated`.
5. Baseline from `value_grade`; with exactly 2 soft signals, one step below the baseline.
6. New-listing cap last (stocks and banks listed under a year: not better than `fair` until one public quarter is filed).

For `unrated`, `verdict_note` starts "research incomplete" when research is incomplete, and "value paused" when the value grade is paused.

## 5. What it means for the user (derived by the app, not written by the session)

| Verdict | If the user owns it | If not |
|---|---|---|
| Attractive | Room to add | Candidate to buy |
| Fair | Hold | Wait |
| Stretched | Consider trimming | Pass |
| Avoid | Consider exiting | Pass |
| Unrated | Do not act on it | Do not act on it |

Ownership comes from the account (as today: `account_owned`, linked share classes count), else the note's `owned`, else both lines are shown. When the user's own notes hold rules, the session writes `owner_note` — one line saying what those rules imply and whether they override the default (for example "your rule — trim above 10% of the account — is not met: hold"). The app returns the derived line and the note as separate fields (`owner_line` without the note, `owner_note` on its own — in `claude_stance`, screener rows and the report field) and prints the note once, after the derived line. The user's rules never change `verdict`.

## 6. App inputs — `valuation_range` (analysis payload, also a report field group "valuation")

```json
{"measure": "p_fcf" | "ev_ebit" | "pe" | "p_sales" | "p_tbv" | "distribution_rate",
 "label": "Price to free cash flow",
 "direction": "lower_is_cheaper" | "higher_is_cheaper",     // higher_is_cheaper for distribution_rate
 "now": 20.4, "now_basis": "report price; TTM through 2026-07-26",
 "points": [{"fy_end": "2026-01-25", "value": 38.2}, ...],   // up to 5 fiscal year-ends, newest first
 "median": 45.1, "low": 30.2, "high": 71.0,
 "grade_hint": "cheap" | "fair" | "expensive" | null,      // arithmetic of section 4 on the reported figure
 "normalized": false,                                       // true when current profit / cash flow > 2× its five-year average
 "now_normalized": null, "normalized_grade_hint": null,     // the measure and its grade on that average (null unless normalized)
 "grade": "cheap" | "fair" | "expensive" | null,           // the less favorable of grade_hint and normalized_grade_hint (= grade_hint without normalization)
 "excluded_points": [{"fy_end": "2021-12-31", "value": 1960.25}],  // only when a year was left out (> 5× the others' median)
 "warnings": ["..."], "status": "ok" | "withheld" | "no_data"}
```

`p_sales` is market cap ÷ TTM revenue now (year-end points: year-end market cap ÷ that fiscal year's revenue). It is the measure when free cash flow, EBIT and earnings are all negative or unavailable.

`grade_hint`, `normalized_grade_hint` and `grade` are the arithmetic only. `value_grade` should equal `grade`; a different `value_grade` needs a departure reason in `value_basis` (section 4) and a two-step departure is always flagged. `warnings` are written for the reader (numbers to one decimal with "x" or "%"; repeated per-year notes merged into one line), not as instructions to the session — the prompt carries its own instructions.

## 6b. App inputs — `rule_inputs` (analysis payload, also a report field group "rules")

The app's arithmetic for each data-driven soft signal of section 3 that the filed statements support (SEC XBRL quarters; fund distribution history). Inputs and hints only — the session writes `soft_signals`.

```json
{"kind": "stock" | "bank" | "fund",
 "rules": [
   {"key": "ni_over_fcf",
    "label": "Net income above free cash flow by more than 25%",
    "values": [{"period": "2026-09-30", "net_income": 1.2e9, "net_income_adj": 1.1e9, "fcf": 0.7e9, "ratio": 1.57},
               {"period": "2026-06-30", ...}],     // the two quarters' figures, newest first
    "threshold": "> 1.25 in both quarters",
    "fires": true | false | null,                  // null = cannot be computed (missing, withheld or flagged input)
    "basis": "SEC 10-Q quarters; net income less flagged one-time items",
    "warnings": ["..."]}],
 "as_of": "2026-09-30"}
```

Keys computed today: stocks and banks — `revenue_decline`, `op_margin_decline`, `fcf_negative`, `receivables_vs_revenue`, `inventory_vs_revenue`, `ni_over_fcf`, `net_debt_ebitda`, `interest_coverage`, `dilution`; banks also `efficiency_ratio` when computable (other bank rules: `fires: null`, "not in filed data"); funds — `distribution_cut`. Keys that need events (`guidance_cut`, `dividend_cut`, `exec_exit`, `insider_selling`) are not computed — the session applies them from research.

**Data-quality gate:** a rule whose inputs are missing, withheld, or flagged gets `fires: null` and the reason in `warnings`; profit used in `ni_over_fcf` is adjusted for the app's flagged one-time gains / tax benefits.

## 7. Frontmatter (prompt version 6)

New keys: `verdict`, `value_grade`, `risk_grade` (enums above; required), `value_basis` (string; required: the measure, the numbers, and the comparison used), `hard_flags`, `soft_signals`, `watch_items` (lists of strings, each with its source and date; optional, deleted when empty), `owner_note` (string; optional).

Kept: everything in v5 except `stance` and `stance_note`, which v6 notes no longer write — `verdict_note` (one line of reasoning) replaces `stance_note`; `stance_horizon` stays as `horizon`-like context under its existing key; `stance_confidence` stays under its existing key; `upgrade_if` / `downgrade_if` / `review_by` stay and are required for every verdict except `unrated`. `proposed_triggers`, `plan_triggers_met`, `plan_triggers_unknown` stay (the default rulebook items the session checked go in `soft_signals` / `hard_flags` / `watch_items`, not in `proposed_triggers`).

**Legacy stance for older code paths** (scorecard, sorting): the parser derives `stance` for a v6 note — attractive → buy · fair → hold · stretched → trim · avoid → sell · unrated → watch. Notes from before v6 keep their own stance and are shown with it (labelled "earlier format") until a v6 note replaces them.

## 8. App checks (flags beside the verdict)

- `verdict` must follow section 4 (rule order 1–6) from `research_status`, `value_grade`, the counts of `hard_flags` and `soft_signals`, and the new-listing cap (`listed_under_a_year`, stocks and banks only): otherwise "verdict does not follow the rulebook: expected X from …".
- `risk_grade` must match the lists (hard → red_flag; 3+ soft → red_flag; 2 soft → watch; 0–1 soft with watch items → watch; else clean).
- `value_grade` vs `valuation_range.grade` (see sections 4 and 6): a different grade without a departure reason is flagged; a two-step departure is always flagged.
- `soft_signals` vs `rule_inputs`: an entry whose `[key]` has `fires: false`, or no entry for a key with `fires: true`, raises "soft signal X does not match the app's arithmetic".
- required v6 fields present and readable; `upgrade_if` / `downgrade_if` / `review_by` present unless unrated.
- everything v5 already checks (price used, research status, move cause, review date, superseded, ownership mismatch) stays.

## 9. Trust label (replaces the four status words in the UI; internal statuses stay)

| Internal status | Label | Meaning |
|---|---|---|
| OK | Current | fine to rely on |
| STALE | Out of date | something happened since it was written — re-run research |
| INVALID | Don't use | the research failed or missed a large move |
| SUPERSEDED | (hidden from lists; reachable in the timeline as "Replaced") | a later note replaced it |

Payload: each note/stance row carries `trust` = "current" | "out_of_date" | "dont_use" | "replaced" beside the existing `status`; `claude_news` carries the `trust` of the note its news check comes from.
