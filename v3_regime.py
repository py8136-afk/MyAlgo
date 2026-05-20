"""
v3_regime.py
Observes 9:15-11:00 morning action, classifies into one of:
  - RANGE       → Iron Fly
  - UPTREND     → Bull Call Spread
  - DOWNTREND   → Bear Put Spread
  - NO_TRADE    → Sit out

Reads news_intel.py if available for event blocking.
"""

from datetime import datetime, time, date
from v3_config import *
from v3_utils import log_print


def _ols_slope(values):
    """OLS slope of a sequence — pts per bar. O(n), no deps."""
    n = len(values)
    if n < 2:
        return 0.0
    x_bar = (n - 1) / 2.0
    y_bar = sum(values) / n
    num = sum((i - x_bar) * (v - y_bar) for i, v in enumerate(values))
    den = sum((i - x_bar) ** 2 for i in range(n))
    return num / den if den else 0.0


def classify_from_data(candles, vix, news_score=0):
    """
    Pure regime classification — no API calls, no logging, no datetime.now().
    candles: list of [ts, open, high, low, close, vol] pre-filtered to 9:15–11:00.
    vix:     VIX close at 11:00 (float), or None if unavailable.
    Returns the same dict format as RegimeDetector.classify().
    """
    if news_score < -5:
        return {"regime": "NO_TRADE", "reason": "NEWS_BLOCK", "score": news_score}

    if vix is None:
        return {"regime": "NO_TRADE", "reason": "VIX_FETCH_FAILED"}
    if vix < VIX_MIN:
        return {"regime": "NO_TRADE", "reason": f"VIX_TOO_LOW_{vix:.1f}", "vix": vix}
    if vix > VIX_MAX:
        return {"regime": "NO_TRADE", "reason": f"VIX_TOO_HIGH_{vix:.1f}", "vix": vix}

    if not candles or len(candles) < 15:
        n = len(candles) if candles else 0
        return {"regime": "NO_TRADE", "reason": f"INSUFFICIENT_CANDLES_{n}", "vix": vix}

    highs  = [c[2] for c in candles]
    lows   = [c[3] for c in candles]
    closes = [c[4] for c in candles]

    morning_high  = max(highs)
    morning_low   = min(lows)
    morning_range = morning_high - morning_low
    spot          = closes[-1]

    if morning_range < MIN_MORNING_RANGE:
        return {"regime": "NO_TRADE",
                "reason": f"RANGE_TOO_TIGHT_{morning_range:.0f}",
                "vix": vix}

    midpoint       = (morning_high + morning_low) / 2
    position_score = (spot - midpoint) / morning_range

    if position_score > TREND_POSITION_THRESHOLD and spot > morning_high - BREAKOUT_BUFFER:
        regime = "UPTREND"
    elif position_score < -TREND_POSITION_THRESHOLD and spot < morning_low + BREAKOUT_BUFFER:
        regime = "DOWNTREND"
    else:
        regime = "RANGE"

    # Slope patch: a steady grind keeps spot mid-range (position_score ≈ 0) but
    # still produces a directional trend. Override RANGE if the OLS slope of close
    # prices exceeds SLOPE_THRESHOLD pts/bar.
    slope = _ols_slope(closes)
    if regime == "RANGE" and abs(slope) >= SLOPE_THRESHOLD:
        regime = "UPTREND" if slope > 0 else "DOWNTREND"

    if regime in ("UPTREND", "DOWNTREND") and vix > VIX_TREND_MAX:
        return {"regime": "NO_TRADE", "reason": "TREND_BUT_VIX_HIGH",
                "vix": vix, "spot": spot}

    return {
        "regime":         regime,
        "reason":         "OK",
        "spot":           spot,
        "vix":            vix,
        "morning_high":   morning_high,
        "morning_low":    morning_low,
        "morning_range":  morning_range,
        "position_score": position_score,
        "slope":          round(slope, 3),
        "news_score":     news_score,
    }


class RegimeDetector:
    def __init__(self, fyers):
        self.fyers = fyers
        self.morning_high = None
        self.morning_low = None
        self.morning_range = None
        self.spot = None
        self.vix = None
        self.news_score = 0

    # ----- DATA FETCH -----

    def _fetch_history(self, symbol, resolution="5"):
        """Fetch today's intraday candles."""
        today = datetime.now().strftime("%Y-%m-%d")
        data = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": today,
            "range_to": today,
            "cont_flag": "1"
        }
        try:
            res = self.fyers.history(data=data)
            if res.get("s") == "ok":
                return res.get("candles", [])
            log_print(f"history fetch failed for {symbol}: {res}", "WARN")
        except Exception as e:
            log_print(f"history exception {symbol}: {e}", "ERROR")
        return []

    def _get_vix(self):
        """Latest VIX value."""
        candles = self._fetch_history(VIX_SYMBOL, "5")
        if not candles:
            return None
        return float(candles[-1][4])  # last close

    def _get_morning_candles(self):
        """5-min Nifty candles 9:15-11:00."""
        all_candles = self._fetch_history(NIFTY_SYMBOL, "5")
        morning = []
        for c in all_candles:
            ts = datetime.fromtimestamp(c[0])
            if time(9, 15) <= ts.time() < time(11, 0):
                morning.append(c)
        return morning

    def _get_news_score(self):
        """Try to use existing news_intel.py if present."""
        try:
            import news_intel
            if hasattr(news_intel, "get_news_score"):
                return news_intel.get_news_score()
            elif hasattr(news_intel, "main"):
                result = news_intel.main()
                if isinstance(result, dict) and "score" in result:
                    return result["score"]
                if isinstance(result, (int, float)):
                    return result
        except Exception as e:
            log_print(f"news_intel unavailable: {e}", "WARN")
        return 0

    # ----- CLASSIFICATION -----

    def classify(self):
        """Main classification — call at 11:00 sharp. Fetch then delegate."""
        log_print("=" * 60)
        log_print("REGIME DETECTION STARTING")

        self.news_score = self._get_news_score()
        log_print(f"News score: {self.news_score}")

        self.vix = self._get_vix()
        if self.vix is not None:
            log_print(f"VIX: {self.vix:.2f}")

        candles = self._get_morning_candles()
        result = classify_from_data(candles, self.vix, self.news_score)

        if "morning_high" in result:
            self.morning_high  = result["morning_high"]
            self.morning_low   = result["morning_low"]
            self.morning_range = result["morning_range"]
            self.spot          = result["spot"]
            log_print(f"Morning High: {self.morning_high:.1f}")
            log_print(f"Morning Low : {self.morning_low:.1f}")
            log_print(f"Range       : {self.morning_range:.1f} pts")
            log_print(f"Spot now    : {self.spot:.1f}")
            log_print(f"Position score: {result['position_score']:+.2f}")
            log_print(f"Slope (pts/bar): {result.get('slope', 0):+.2f}  (threshold ±{SLOPE_THRESHOLD})")

        regime = result["regime"]
        if regime == "NO_TRADE":
            log_print(f"NO_TRADE — {result.get('reason', '')}", "WARN")
        else:
            if regime == "RANGE" and self.vix and self.vix > VIX_RANGE_PREFER_MAX:
                log_print(f"Range with elevated VIX {self.vix:.1f}, fly will be sized smaller", "WARN")
            log_print(f"REGIME: {regime}", "RESULT")

        log_print("=" * 60)
        return result
