"""
backtest_regime.py
Run the regime classifier over every trading day in 2024–2025 using
historical Fyers data. Uses the exact same classify_from_data() logic
as the live system — no reimplementation.

Usage:
    python fyers_login.py          # refresh token first
    python backtest_regime.py      # first run ~8-10 min (API fetches)
    python backtest_regime.py      # subsequent runs instant (cache)

Output:
    backtest_regime_results.csv    — one row per trading day
    (summary distribution printed to stdout)

news_score is set to 0 for all historical days (no historical news data).
This means NO_TRADE counts represent market-condition blocks only.
"""

import csv
import sys
from datetime import date
from collections import Counter

from v3_utils import get_fyers_session
from v3_config import NIFTY_SYMBOL, VIX_SYMBOL
from v3_regime import classify_from_data
from fetch_historical_data import (
    fetch_day_candles,
    get_morning_candles,
    get_vix_at_11,
    iter_trading_days,
    count_cached_days,
    CACHE_DIR,
)

OUTPUT_CSV = "backtest_regime_results.csv"

COLUMNS = [
    "date", "regime", "reason",
    "spot", "morning_high", "morning_low", "morning_range",
    "position_score", "vix",
]

START = date(2024, 1, 1)
END   = date(2025, 12, 31)


def run_backtest(fyers):
    days  = list(iter_trading_days(START, END))
    total = len(days)

    already_cached = count_cached_days(NIFTY_SYMBOL)
    if already_cached:
        print(f"Cache: {already_cached} NIFTY days already on disk — those skip the API.")
    print(f"Scanning {total} weekdays ({START} → {END}). "
          f"Holidays/gaps return 0 candles and are skipped.\n")

    rows     = []
    skipped  = 0
    api_hits = 0

    for i, date_str in enumerate(days, 1):
        nifty_raw = fetch_day_candles(fyers, NIFTY_SYMBOL, date_str)
        vix_raw   = fetch_day_candles(fyers, VIX_SYMBOL,   date_str)

        # Track live API calls (cache misses) for ETA feedback
        # (rough: if both symbols were already cached we'd have printed nothing)
        api_hits += 1

        if not nifty_raw:
            skipped += 1
            if i % 50 == 0:
                pct = 100 * i / total
                print(f"  [{i:>3}/{total}] {date_str}  holiday/gap  "
                      f"(skipped {skipped} so far)  {pct:.0f}% done")
            continue

        morning = get_morning_candles(nifty_raw)
        vix_val = get_vix_at_11(vix_raw)

        result = classify_from_data(morning, vix_val, news_score=0)

        row = {
            "date":           date_str,
            "regime":         result["regime"],
            "reason":         result.get("reason", ""),
            "spot":           result.get("spot", ""),
            "morning_high":   result.get("morning_high", ""),
            "morning_low":    result.get("morning_low", ""),
            "morning_range":  result.get("morning_range", ""),
            "position_score": result.get("position_score", ""),
            "vix":            result.get("vix", vix_val if vix_val else ""),
        }
        rows.append(row)

        if i % 25 == 0:
            pct = 100 * i / total
            print(f"  [{i:>3}/{total}] {date_str}  →  {result['regime']:<12}  "
                  f"{pct:.0f}% done")

    print(f"\nDone. {len(rows)} classified days, {skipped} holidays/gaps skipped.")
    return rows


def write_csv(rows):
    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows → {OUTPUT_CSV}")


def print_summary(rows):
    total          = len(rows)
    regime_counts  = Counter(r["regime"] for r in rows)
    reason_counts  = Counter(r["reason"] for r in rows if r["regime"] == "NO_TRADE")

    # Per-regime average morning range (useful for sanity-checking data)
    range_by_regime = {}
    for r in rows:
        reg = r["regime"]
        try:
            rng = float(r["morning_range"])
            range_by_regime.setdefault(reg, []).append(rng)
        except (ValueError, TypeError):
            pass

    print()
    print("=" * 60)
    print("REGIME DISTRIBUTION  (2024–2025, news_score=0 throughout)")
    print("=" * 60)
    for regime in ("RANGE", "UPTREND", "DOWNTREND", "NO_TRADE"):
        n   = regime_counts.get(regime, 0)
        pct = 100 * n / total if total else 0
        ranges = range_by_regime.get(regime, [])
        avg_r  = f"  avg range {sum(ranges)/len(ranges):.0f}pt" if ranges else ""
        print(f"  {regime:<12}  {n:>4} days  ({pct:5.1f}%){avg_r}")
    print(f"  {'TOTAL':<12}  {total:>4} days")

    if reason_counts:
        print("\nNO_TRADE breakdown by reason:")
        for reason, n in reason_counts.most_common():
            pct = 100 * n / total if total else 0
            print(f"  {reason:<40} {n:>4}  ({pct:.1f}%)")

    # Position score distribution for RANGE days — the diagnostic we need
    range_rows = [r for r in rows if r["regime"] == "RANGE" and r["position_score"] != ""]
    if range_rows:
        scores = sorted(float(r["position_score"]) for r in range_rows)
        p25 = scores[len(scores) // 4]
        p75 = scores[3 * len(scores) // 4]
        print(f"\nRANGE days — position_score distribution:")
        print(f"  min {scores[0]:+.3f}  p25 {p25:+.3f}  "
              f"median {scores[len(scores)//2]:+.3f}  p75 {p75:+.3f}  max {scores[-1]:+.3f}")

    print("=" * 60)
    print()
    print(f"Full detail in: {OUTPUT_CSV}")
    print("Run backtest_pnl.py next to add P&L simulation (Stage 2).")


def main():
    print()
    print("=" * 60)
    print(" MyAlgo v3 — Regime Classifier Backtest  (Stage 1)")
    print("=" * 60)

    try:
        fyers = get_fyers_session()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        print("Run:  python fyers_login.py")
        sys.exit(1)

    # Quick connectivity test
    try:
        profile = fyers.get_profile()
        if profile.get("s") != "ok":
            print(f"ERROR: Fyers auth check failed — {profile}")
            sys.exit(1)
        print(f"Connected as {profile['data'].get('name', '?')}\n")
    except Exception as e:
        print(f"ERROR: Fyers connection failed — {e}")
        sys.exit(1)

    rows = run_backtest(fyers)
    write_csv(rows)
    print_summary(rows)


if __name__ == "__main__":
    main()
