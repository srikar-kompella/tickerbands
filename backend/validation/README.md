# Validation

## `oos_2026_06.py` → `analysis/oos_2026-06_validation.json`

Forward out-of-sample check of the shipped band and breach estimator on days
after the data used to choose the band engine (that cache ended 2026-06-05).
The method is **pre-registered in the script's docstring**; the script was
committed (6e766a3, 2026-10-12 00:12 UTC) before any out-of-sample data was
fetched, then run once. Nothing was tuned.

Reproduce: `cd backend && python validation/oos_2026_06.py` (needs
`yfinance`). Compare `derived_series_sha256` in the JSON to confirm you fetched
identical inputs; Yahoo occasionally revises history, which would change both.

### Results (run 2026-10-12 00:12 UTC, window 2026-06-02 → 2026-10-09)

| Construction | Coverage | 95% CI, date-clustered bootstrap | Mean half-width |
|---|---|---|---|
| flat-20d (shipped default) | **82.8%** | 80.3–85.1% | ±2.79% |
| EWMA λ=0.94 | 83.7% | 81.4–85.8% | ±2.82% |
| conformal (250d) | 79.4% | 76.7–82.0% | ±2.50% |

n = 1,820 ticker-days (20 tickers × 91 trading days). Strictly-unseen window
(from 2026-06-08): flat-20d 83.3%.

**Plain reading:** out of sample, the shipped band **over-covered**: it was
wider than an 80% band should be, and 80% lies just outside the interval.
That is a miss in the conservative direction, but it is a miss. The engine
ranking from the in-sample study did **not** hold: conformal was closest to
target and narrowest. No engine change is made on the strength of one
four-month window.

Breach estimator (trailing-500d empirical), max calibration deviation over
buckets with n ≥ 30, non-overlapping windows: **5d 2.7 pp, 10d 5.4 pp, 21d
10.6 pp**. 21d remains the weak horizon, as the original study found.

### Caveats written after seeing the results (not part of the pre-registration)

- Breach n counts rows of ticker × window × threshold. The five thresholds
  share one return path, so independent windows are n/5: 360 (5d), 180 (10d),
  80 (21d). The 21d figure rests on ~4 windows per ticker.
- One regime (Jun–Oct 2026), the same 20 tickers. Out-of-sample in time, not
  in tickers.
- The Wilson interval in the JSON assumes independence, which same-day
  cross-ticker correlation violates; use the bootstrap interval.
