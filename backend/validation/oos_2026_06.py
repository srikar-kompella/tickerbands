"""
validation/oos_2026_06.py — forward OUT-OF-SAMPLE check of the shipped band
and breach estimator, on trading days the engine choice never saw.

Why: the flat-20d engine was chosen (band_validation.json) on a bar cache that
runs 2024-01-02 → 2026-06-05, overlapping the 9,620-row coverage replay
(2024-07-01 → 2026-06-01). So "80.5% coverage" is partly in-sample. This
script re-evaluates the FROZEN constructions on days after that, with no
re-fitting of anything.

─── PRE-REGISTRATION (committed before any out-of-sample data was fetched) ───
Frozen code: the functions in analysis/vol_bands.py and analysis/breach.py at
repo HEAD (recorded in the output JSON). Nothing here is tuned; z=1.28,
window=20, EWMA λ=0.94, conformal window=250, breach window=500 are the
shipped constants, imported, not copied.

Universe: the 20 tickers of the original replay (band_coverage.json):
  AAPL AVGO COIN COST GOOGL HIMS IWM JPM LLY META MSFT NVDA PHYS PSLV QQQ
  SPY TLT TSLA XLE XOM
Data: Yahoo daily bars via yfinance, auto_adjust=False (split-adjusted,
  NOT dividend-adjusted closes) + dividend events — the same source and
  adjustment convention the original bar cache used (experiment
  scripts/backtest.py --source yfinance). Fetched from 2023-06-01 so every
  eval day has ≥500 trailing N-day returns.

BAND (primary = construction on main today):
  For each ticker and each eval day t (the day whose close is being tested):
    bars = completed bars up to t-1;  divs = events with ex-date ≤ t-1
    adj  = adjust_closes_for_dividends(bars, divs)      (main, BUG-B fix)
    anchor = close[t-1] − (dividend with ex-date == t, else 0)
    flat20: band(anchor, realized_vol(adj, 20))
    ewma:   band_with_engine(anchor, adj, "ewma")
    conformal: band_with_engine(anchor, raw closes, "conformal")  (as main)
    covered ⇔ |close[t]/anchor − 1|·100 ≤ half_width_pct
  Secondary (original replay construction, for comparability): raw closes,
  anchor = close[t-1], no dividend handling.
  Windows: PRIMARY eval days 2026-06-02 → latest close (as requested).
    SENSITIVITY: 2026-06-08 → latest (strictly after the engine-selection
    cache's last bar, 2026-06-05).
  Uncertainty: Wilson 95% binomial interval (assumes independence — it does
    NOT hold across tickers on the same day), plus a date-clustered bootstrap
    95% interval (resample trading days with replacement, 2,000 reps, seed 0).

BREACH (frozen trailing-500d empirical estimator, breach_prob_above):
  Prediction at close of day t (t ≥ 2026-06-02, t+N ≤ latest close), using
  raw split-adjusted closes ≤ t (as the premium lens does). Outcome:
  close[t+N]/close[t] − 1 > r.
  Thresholds r ∈ {−0.05, −0.025, 0, +0.025, +0.05}. The ORIGINAL study did not
  record its thresholds (≈3 per ticker-day inferred from its row counts);
  this set is fixed here before seeing data, so this is a frozen-estimator
  test, not an exact re-run of the original sampling.
  Buckets: the original's edges [0,.05,.15,.30,.50,.70,.85,.95,1.01].
  PRIMARY: non-overlapping prediction days (every N-th eval day per ticker),
    deviation = |realized freq − mean predicted| per bucket; report max over
    buckets with n ≥ 30 and every bucket's n.
  SECONDARY (original's definition, for comparability): overlapping days,
    |realized − bucket midpoint|.
  Horizons 5 and 10 days. 21 days is reported only if its non-overlapping
    total n ≥ 300; otherwise marked insufficient, with n.

No result is suppressed. If coverage misses 80%, that is the result.
───────────────────────────────────────────────────────────────────────────────
Run (from backend/):  python validation/oos_2026_06.py
Writes: analysis/oos_2026-06_validation.json
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import random
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TICKERLENS_HOME", "/tmp/tickerlens-oos-home")  # never read a user instance

from analysis import breach, vol_bands  # noqa: E402  (frozen constructions)

TICKERS = ["AAPL", "AVGO", "COIN", "COST", "GOOGL", "HIMS", "IWM", "JPM", "LLY", "META",
           "MSFT", "NVDA", "PHYS", "PSLV", "QQQ", "SPY", "TLT", "TSLA", "XLE", "XOM"]
FETCH_START = "2023-06-01"
EVAL_START = "2026-06-02"
STRICT_START = "2026-06-08"
THRESHOLDS = [-0.05, -0.025, 0.0, 0.025, 0.05]
EDGES = [0, .05, .15, .30, .50, .70, .85, .95, 1.01]
MIN_BUCKET_N = 30
MIN_NONOV_N_21D = 300
BOOT_REPS, BOOT_SEED = 2000, 0
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "analysis", "oos_2026-06_validation.json")


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    if n == 0:
        return [float("nan"), float("nan")]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(100 * (c - h), 2), round(100 * (c + h), 2)]


def fetch() -> tuple[dict, dict, str]:
    import yfinance as yf
    end = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    bars, divs = {}, {}
    for sym in TICKERS:
        h = yf.Ticker(sym).history(start=FETCH_START, end=end, auto_adjust=False,
                                   actions=True)
        if h is None or h.empty:
            raise SystemExit(f"no data for {sym}")
        bars[sym] = [{"date": idx.date().isoformat(), "close": float(r["Close"])}
                     for idx, r in h.iterrows() if r["Close"] == r["Close"]]
        divs[sym] = [{"date": idx.date().isoformat(), "amount": float(r["Dividends"])}
                     for idx, r in h.iterrows() if float(r.get("Dividends", 0) or 0) > 0]
    return bars, divs, yf.__version__


def series_hash(rows: list[dict]) -> str:
    """SHA-256 of the derived series (date,close rounded to 6 dp) — lets anyone
    who regenerates the data verify they got byte-identical inputs."""
    blob = "\n".join(f"{r['date']},{r['close']:.6f}" for r in rows).encode()
    return hashlib.sha256(blob).hexdigest()


def band_records(bars: list[dict], divs: list[dict], start: str) -> list[dict]:
    out = []
    dates = [b["date"] for b in bars]
    for i in range(1, len(bars)):
        t = dates[i]
        if t < start:
            continue
        hist = bars[:i]                                   # completed bars ≤ t-1
        if len(hist) < 252:
            continue
        past_divs = [d for d in divs if d["date"] <= hist[-1]["date"]]
        adj = vol_bands.adjust_closes_for_dividends(hist, past_divs)
        raw = [b["close"] for b in hist]
        ex_amt = sum(d["amount"] for d in divs if d["date"] == t)
        anchor = raw[-1] - ex_amt
        close_t = bars[i]["close"]
        rec = {"date": t}
        for eng, closes in (("flat20", adj), ("ewma", adj), ("conformal", raw)):
            b = vol_bands.band_with_engine(anchor, closes, eng)
            move = abs(close_t / anchor - 1) * 100
            rec[eng] = (move <= b["half_width_pct"], b["half_width_pct"])
        # secondary: original replay construction (raw, no dividend handling)
        b0 = vol_bands.band(raw[-1], vol_bands.realized_vol(raw, 20))
        rec["orig_flat20"] = (abs(close_t / raw[-1] - 1) * 100 <= b0["half_width_pct"],
                              b0["half_width_pct"])
        out.append(rec)
    return out


def summarize_band(recs_by_sym: dict, key: str, start: str) -> dict:
    per, all_rows = {}, []
    for sym, recs in recs_by_sym.items():
        rows = [r for r in recs if r["date"] >= start]
        k = sum(1 for r in rows if r[key][0])
        per[sym] = {"covered_pct": round(100 * k / len(rows), 2) if rows else None,
                    "days": len(rows)}
        all_rows += rows
    n = len(all_rows)
    k = sum(1 for r in all_rows if r[key][0])
    # date-clustered bootstrap
    by_date: dict[str, list[bool]] = {}
    for r in all_rows:
        by_date.setdefault(r["date"], []).append(r[key][0])
    days = sorted(by_date)
    rng = random.Random(BOOT_SEED)
    boots = []
    for _ in range(BOOT_REPS):
        sample = [by_date[days[rng.randrange(len(days))]] for _ in days]
        tot = sum(len(s) for s in sample)
        boots.append(100 * sum(sum(s) for s in sample) / tot)
    boots.sort()
    return {
        "coverage_pct": round(100 * k / n, 2) if n else None,
        "n_ticker_days": n,
        "n_trading_days": len(days),
        "wilson95_pct": wilson(k, n),
        "date_cluster_bootstrap95_pct": [round(boots[int(.025 * BOOT_REPS)], 2),
                                         round(boots[int(.975 * BOOT_REPS) - 1], 2)],
        "mean_halfwidth_pct": round(sum(r[key][1] for r in all_rows) / n, 3) if n else None,
        "per_ticker": per,
    }


def breach_eval(bars_by_sym: dict, N: int, last_date: str) -> dict:
    over, nonov = [], []
    for sym, bars in bars_by_sym.items():
        closes = [b["close"] for b in bars]
        dates = [b["date"] for b in bars]
        eval_idx = [i for i, d in enumerate(dates) if d >= EVAL_START and i + N < len(bars)]
        nonov_set = set(eval_idx[::N])
        for i in eval_idx:
            hist = closes[: i + 1]
            realized_ret = closes[i + N] / closes[i] - 1
            for r in THRESHOLDS:
                p = breach.breach_prob_above(hist, N, r)
                if p is None:
                    continue
                row = (p, 1 if realized_ret > r else 0)
                over.append(row)
                if i in nonov_set:
                    nonov.append(row)

    def bucketize(rows, ref):
        out = []
        for lo, hi in zip(EDGES, EDGES[1:]):
            b = [x for x in rows if lo <= x[0] < hi]
            if not b:
                out.append({"bucket": f"{lo:.2f}-{hi:.2f}", "n": 0})
                continue
            mean_pred = sum(x[0] for x in b) / len(b)
            realized = sum(x[1] for x in b) / len(b)
            target = mean_pred if ref == "mean" else (lo + min(hi, 1.0)) / 2
            out.append({"bucket": f"{lo:.2f}-{hi:.2f}", "n": len(b),
                        "mean_pred": round(mean_pred, 3), "realized": round(realized, 3),
                        "dev_pp": round(100 * abs(realized - target), 1)})
        return out

    prim = bucketize(nonov, "mean")
    sec = bucketize(over, "mid")
    elig = [b for b in prim if b["n"] >= MIN_BUCKET_N]
    return {
        "horizon_days": N,
        "nonoverlap_total_n": len(nonov),
        "overlap_total_n": len(over),
        "primary_nonoverlap_vs_mean_pred": prim,
        "primary_max_dev_pp_buckets_n_ge_30": max((b["dev_pp"] for b in elig), default=None),
        "secondary_overlap_vs_midpoint": sec,
        "secondary_max_dev_pp": max((b["dev_pp"] for b in sec if b["n"]), default=None),
    }


def main() -> None:
    run_ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                          cwd=os.path.dirname(OUT)).stdout.strip() or "unknown"
    bars, divs, yfv = fetch()
    last = min(b[-1]["date"] for b in bars.values())
    recs = {s: band_records(bars[s], divs[s], EVAL_START) for s in TICKERS}

    band_out = {}
    for label, start in (("primary_2026-06-02", EVAL_START), ("strict_2026-06-08", STRICT_START)):
        band_out[label] = {eng: summarize_band(recs, eng, start)
                           for eng in ("flat20", "ewma", "conformal", "orig_flat20")}

    breach_out = {}
    for N in (5, 10, 21):
        res = breach_eval(bars, N, last)
        if N == 21 and res["nonoverlap_total_n"] < MIN_NONOV_N_21D:
            res = {"horizon_days": 21, "status": "INSUFFICIENT",
                   "nonoverlap_total_n": res["nonoverlap_total_n"],
                   "note": f"non-overlapping n < {MIN_NONOV_N_21D} (pre-registered); no verdict"}
        breach_out[str(N)] = res

    out = {
        "note": ("Forward out-of-sample check of the FROZEN band + breach constructions "
                 "on days after the engine-selection data. Pre-registration is the "
                 "docstring of backend/validation/oos_2026_06.py (committed before data fetch)."),
        "run_utc": run_ts,
        "frozen_constructions_commit": head,
        "eval_window": {"primary_start": EVAL_START, "strict_start": STRICT_START,
                        "end_latest_close": last},
        "engine_selection_data_last_bar": "2026-06-05",
        "tickers": TICKERS,
        "data_source": f"Yahoo Finance via yfinance {yfv}, auto_adjust=False, actions=True",
        "coverage_target_pct": 80.0,
        "band": band_out,
        "breach": breach_out,
        "derived_series_sha256": {s: series_hash(bars[s]) for s in TICKERS},
        "caveats": [
            "Wilson interval assumes independence; same-day moves are correlated across "
            "tickers, so the date-clustered bootstrap interval is the honest one.",
            "Breach thresholds were not recorded by the original study; this run uses a "
            "pre-registered set, so it tests the frozen estimator, not an exact re-run.",
            "One regime (Jun–Oct 2026). Out-of-sample on time, not on tickers.",
        ],
    }
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps({k: band_out[k]["flat20"]["coverage_pct"] for k in band_out}))


if __name__ == "__main__":
    main()
