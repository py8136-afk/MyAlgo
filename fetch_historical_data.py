"""
fetch_historical_data.py
Fetches Fyers historical candle data for backtesting.
Caches results in backtest_cache/ to avoid re-hitting the API on subsequent runs.

NOTE: Fyers historical API for intraday data has a per-call limit (typically
100 trading days). Since we fetch one day at a time (range_from == range_to)
this limit is never a concern. However, lookback depth depends on your Fyers
subscription tier. If you get empty responses for 2024 dates, contact Fyers
support to confirm historical data access for your account.
"""

import os
import json
import time
from datetime import datetime, timedelta
from datetime import time as dtime

from v3_config import NIFTY_SYMBOL, VIX_SYMBOL

CACHE_DIR = "backtest_cache"
API_DELAY  = 0.5   # seconds between API calls — keeps us well under rate limits


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_path(symbol, date_str):
    safe = symbol.replace(":", "_").replace("-", "_")
    return os.path.join(CACHE_DIR, f"{safe}_{date_str}.json")


def _load_cache(symbol, date_str):
    path = _cache_path(symbol, date_str)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def _save_cache(symbol, date_str, candles):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(symbol, date_str), "w") as f:
        json.dump(candles, f)


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_day_candles(fyers, symbol, date_str, resolution="5"):
    """
    Return all 5-min candles for symbol on date_str ("YYYY-MM-DD").
    Hits the Fyers history API on first call; subsequent calls read from disk.
    Returns [] for weekends, NSE holidays, or API failures.
    """
    cached = _load_cache(symbol, date_str)
    if cached is not None:
        return cached

    data = {
        "symbol":      symbol,
        "resolution":  resolution,
        "date_format": "1",
        "range_from":  date_str,
        "range_to":    date_str,
        "cont_flag":   "1",
    }
    try:
        res = fyers.history(data=data)
        candles = res.get("candles", []) if res.get("s") == "ok" else []
        if res.get("s") != "ok":
            print(f"[FETCH] {symbol} {date_str}: API error — {res.get('message', res)}")
    except Exception as e:
        print(f"[FETCH] {symbol} {date_str}: exception — {e}")
        candles = []

    _save_cache(symbol, date_str, candles)
    time.sleep(API_DELAY)
    return candles


def get_morning_candles(candles):
    """Filter candle list to the 9:15–11:00 observation window. Pure, no API."""
    out = []
    for c in candles:
        ts = datetime.fromtimestamp(c[0])
        if dtime(9, 15) <= ts.time() < dtime(11, 0):
            out.append(c)
    return out


def get_vix_at_11(vix_candles):
    """Last VIX close in the 9:15–11:00 window. Returns None if no data."""
    morning = get_morning_candles(vix_candles)
    return float(morning[-1][4]) if morning else None


def iter_trading_days(start_date, end_date):
    """
    Yield "YYYY-MM-DD" strings for every weekday in [start_date, end_date].
    Actual NSE holidays are handled naturally — Fyers returns [] for them,
    and the backtest skips rows with no candle data.
    """
    cur = start_date
    while cur <= end_date:
        if cur.weekday() < 5:   # 0=Mon … 4=Fri
            yield cur.strftime("%Y-%m-%d")
        cur += timedelta(days=1)


def count_cached_days(symbol):
    """How many days are already cached for a symbol (for progress reporting)."""
    if not os.path.exists(CACHE_DIR):
        return 0
    safe = symbol.replace(":", "_").replace("-", "_")
    return sum(1 for f in os.listdir(CACHE_DIR) if f.startswith(safe))
