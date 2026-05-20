"""
slope_analysis.py
For the 40 Iron Fly trades with loss worse than ₹2,000, fetch the
9:15-11:00 morning candles and compute the OLS linear-regression slope
of Nifty close prices. Prints the slope distribution so you can pick
a classifier threshold.

Run with: conda run -n base python slope_analysis.py
"""

import os
import sys
import time
import json
import math
from datetime import datetime, date, timedelta

# ── Fyers ──────────────────────────────────────────────────────────
try:
    from fyers_apiv3 import fyersModel
except ImportError:
    print("fyers_apiv3 not found — run inside conda base: conda run -n base python slope_analysis.py")
    sys.exit(1)

import openpyxl

# ── CONFIG ─────────────────────────────────────────────────────────
BROKER_ID   = "Z33F8IITSE-100"
TOKEN_FILE  = "token.txt"
NIFTY_SYM   = "NSE:NIFTY50-INDEX"
XLSX_FILE   = "backtest_v3_results.xlsx"
CACHE_DIR   = "backtest_cache"
API_DELAY   = 0.5   # seconds between calls

# ── Fyers client ───────────────────────────────────────────────────
def get_fyers():
    with open(TOKEN_FILE) as f:
        token = f.read().strip()
    return fyersModel.FyersModel(client_id=BROKER_ID, token=token, log_path="")


# ── Cache (shared with fetch_historical_data.py) ───────────────────
def _cache_path(symbol, date_str):
    safe = symbol.replace(":", "_").replace("-", "_")
    return os.path.join(CACHE_DIR, f"{safe}_{date_str}.json")

def _load_cache(symbol, date_str):
    p = _cache_path(symbol, date_str)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return None

def _save_cache(symbol, date_str, candles):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(symbol, date_str), "w") as f:
        json.dump(candles, f)


# ── Fetch day candles (5-min) ──────────────────────────────────────
def fetch_day(fyers, symbol, date_str):
    cached = _load_cache(symbol, date_str)
    if cached is not None:
        return cached

    payload = {
        "symbol": symbol,
        "resolution": "5",
        "date_format": "1",
        "range_from": date_str,
        "range_to":   date_str,
        "cont_flag": "1",
    }
    try:
        r = fyers.history(data=payload)
        candles = r.get("candles", []) if r.get("s") == "ok" else []
        if r.get("s") != "ok":
            print(f"   [API ERR] {date_str}: {r.get('message', r.get('s'))}")
    except Exception as e:
        print(f"   [EXCEPTION] {date_str}: {e}")
        candles = []

    _save_cache(symbol, date_str, candles)
    time.sleep(API_DELAY)
    return candles


# ── Morning window filter (9:15 ≤ ts < 11:00) ─────────────────────
def morning_candles(raw):
    out = []
    for c in raw:
        ts = datetime.fromtimestamp(c[0])
        if ts.hour == 9 and ts.minute >= 15:
            out.append(c)
        elif ts.hour == 10:
            out.append(c)
    return out


# ── OLS slope (pts per 5-min bar) ─────────────────────────────────
def ols_slope(values):
    n = len(values)
    if n < 2:
        return None
    x_bar = (n - 1) / 2.0
    y_bar = sum(values) / n
    num = sum((i - x_bar) * (v - y_bar) for i, v in enumerate(values))
    den = sum((i - x_bar) ** 2 for i in range(n))
    return num / den if den else 0.0


# ── Percentile helper ──────────────────────────────────────────────
def percentile(data, pct):
    s = sorted(data)
    k = (len(s) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


# ── Load bad Iron Fly dates from xlsx ─────────────────────────────
def load_bad_dates(path, loss_threshold=-2000):
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.active
    result = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        d_val, _regime, strategy, spot, _vix, _mr, pos_score, _lot, pnl = row[:9]
        if strategy == "IRON_FLY" and pnl is not None and pnl < loss_threshold:
            if isinstance(d_val, datetime):
                date_str = d_val.strftime("%Y-%m-%d")
            else:
                date_str = str(d_val)[:10]
            result.append({
                "date": date_str,
                "pnl":  pnl,
                "spot": spot,
                "position_score": pos_score,
            })
    return sorted(result, key=lambda x: x["pnl"])


# ── Main ───────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  SLOPE ANALYSIS — Bad Iron Fly Days (loss > ₹2,000)")
    print("=" * 60)

    # 1. Extract dates
    bad = load_bad_dates(XLSX_FILE)
    print(f"\n  Found {len(bad)} Iron Fly trades with loss worse than ₹2,000")
    print(f"  Date range: {bad[0]['date']} → {bad[-1]['date']}\n")

    # 2. Connect to Fyers
    print("  Connecting to Fyers...", end=" ", flush=True)
    fyers = get_fyers()
    p = fyers.get_profile()
    if p.get("s") != "ok":
        print(f"FAILED: {p}")
        print("  Run: python fyers_login.py")
        sys.exit(1)
    print(f"OK ({p['data']['name']})")

    # 3. Fetch candles and compute slopes
    print(f"\n  Fetching morning candles for {len(bad)} dates...")
    print("  (Cached dates will be instant; uncached ~0.5s each)\n")

    results = []
    no_data = []

    for i, row in enumerate(bad):
        ds = row["date"]
        raw = fetch_day(fyers, NIFTY_SYM, ds)
        mc  = morning_candles(raw)

        if len(mc) < 5:
            no_data.append(ds)
            print(f"  [{i+1:2d}/{len(bad)}]  {ds}  pnl=₹{row['pnl']:+,.0f}  → NO DATA ({len(mc)} candles)")
            continue

        closes = [c[4] for c in mc]
        slope  = ols_slope(closes)
        # Normalise: slope as fraction of spot per bar (dimensionless)
        norm_slope = slope / closes[0] if closes[0] else 0

        results.append({
            "date":       ds,
            "pnl":        row["pnl"],
            "spot":       row["spot"],
            "pos_score":  row["position_score"],
            "n_bars":     len(mc),
            "slope":      slope,
            "norm_slope": norm_slope * 10000,   # in bps/bar
        })
        print(f"  [{i+1:2d}/{len(bad)}]  {ds}  pnl=₹{row['pnl']:+,.0f}  "
              f"slope={slope:+.2f} pts/bar  ({norm_slope*10000:+.3f} bps/bar)  bars={len(mc)}")

    # 4. Distribution analysis
    if not results:
        print("\n  No data available for any date. "
              "Fyers may not have historical data this far back.")
        return

    slopes     = [r["slope"] for r in results]
    norm_slopes = [r["norm_slope"] for r in results]

    print("\n" + "=" * 60)
    print("  SLOPE DISTRIBUTION  (pts per 5-min bar)")
    print("=" * 60)
    print(f"  Dates with data : {len(results)} / {len(bad)}")
    if no_data:
        print(f"  No data         : {len(no_data)} ({', '.join(no_data[:5])}{'...' if len(no_data)>5 else ''})")
    print()

    abs_slopes = [abs(s) for s in slopes]
    print(f"  Raw slope (pts/bar):")
    print(f"    Min     : {min(slopes):+.3f}")
    print(f"    p10     : {percentile(slopes, 10):+.3f}")
    print(f"    p25     : {percentile(slopes, 25):+.3f}")
    print(f"    Median  : {percentile(slopes, 50):+.3f}")
    print(f"    p75     : {percentile(slopes, 75):+.3f}")
    print(f"    p90     : {percentile(slopes, 90):+.3f}")
    print(f"    Max     : {max(slopes):+.3f}")
    print(f"    |slope| mean : {sum(abs_slopes)/len(abs_slopes):.3f}")

    print(f"\n  Normalised slope (bps/bar = slope/spot × 10000):")
    abs_norm = [abs(s) for s in norm_slopes]
    print(f"    Min     : {min(norm_slopes):+.4f}")
    print(f"    p25     : {percentile(norm_slopes, 25):+.4f}")
    print(f"    Median  : {percentile(norm_slopes, 50):+.4f}")
    print(f"    p75     : {percentile(norm_slopes, 75):+.4f}")
    print(f"    Max     : {max(norm_slopes):+.4f}")
    print(f"    |norm|  mean : {sum(abs_norm)/len(abs_norm):.4f}")

    # Histogram of absolute slopes
    print("\n  Histogram |slope| (pts/bar):")
    buckets = [0, 2, 4, 6, 8, 10, 15, 20, float("inf")]
    labels  = ["0–2", "2–4", "4–6", "6–8", "8–10", "10–15", "15–20", "20+"]
    counts  = [0] * len(labels)
    for s in abs_slopes:
        for j, b in enumerate(buckets[:-1]):
            if b <= s < buckets[j+1]:
                counts[j] += 1
                break
    for label, count in zip(labels, counts):
        bar = "█" * count
        print(f"    {label:>6} pts/bar: {bar} ({count})")

    # Suggested threshold candidates
    print("\n  THRESHOLD CANDIDATES (|slope| pts/bar):")
    for thr in [2, 3, 4, 5, 6, 8, 10]:
        caught = sum(1 for s in abs_slopes if s >= thr)
        print(f"    ≥ {thr:2d} pts/bar → catches {caught}/{len(results)} bad days "
              f"({caught/len(results)*100:.0f}%)")

    # Detailed table sorted by slope magnitude
    print("\n  DETAILED TABLE (sorted by |slope| desc):")
    print(f"  {'Date':<12} {'P&L':>8} {'Spot':>8} {'PosScore':>9} {'Bars':>5} "
          f"{'Slope':>8} {'Norm(bps)':>10}")
    print("  " + "-" * 70)
    for r in sorted(results, key=lambda x: -abs(x["slope"])):
        print(f"  {r['date']:<12} {r['pnl']:>8,.0f} {r['spot']:>8.0f} "
              f"{r['pos_score']:>9.3f} {r['n_bars']:>5} "
              f"{r['slope']:>8.3f} {r['norm_slope']:>10.4f}")

    print("\n" + "=" * 60)
    print("  INTERPRETATION")
    print("=" * 60)
    print("""
  The slope here is the OLS regression coefficient on close prices
  of the 9:15-11:00 window, in pts per 5-min bar.

  Positive slope = market grinding up (dangerous for short call leg)
  Negative slope = market grinding down (dangerous for short put leg)
  |slope| near 0 = true sideways range (Iron Fly should work)

  For the classifier patch:
    - Set ABS_SLOPE_THRESHOLD in v3_config.py
    - If |slope| > threshold → override RANGE → DOWNTREND or UPTREND
    - Pick threshold where you catch most bad days without
      over-filtering (losing too many profitable RANGE days)
""")


if __name__ == "__main__":
    main()
