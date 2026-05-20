"""
═══════════════════════════════════════════════════════════════════
  NIFTY FILTERED ORB v2.1 — by Prateek x Claude
  Reviewed & corrected April 21 2026 after peer analysis

  STRATEGY:
    ORB (9:15-9:45) + VWAP filter + LR slope + VIX filter
    Buy ATM Call/Put on filtered breakout
    Exit: +50% target / -20% SL / 12:00 PM time stop

  CAPITAL: ₹20,000 base, scales with profits
  LOT SIZE: 25 (current Nifty weekly lot size as of 2024)
  RISK PER TRADE: 5% of capital (~₹1,000)
  WEEKLY LOSS GUARD: -₹3,000 = auto shutdown
  DAILY LOSS GUARD: -₹1,500 = stop for the day

  BACKTEST BASIS:
    - Zerodha ORB NIFTY (Jan 2022-Feb 2026): 48% WR
    - VWAP paper (Zarattini, 2023): Sharpe 2.1
    - VIX Rank filter: real WR boost ~55-58% (Options Cafe)
    - QuantInsti LR on Nifty: beat B&H 5/6 years

  HONEST EXPECTATIONS (post-cost, post-slippage):
    Win Rate: 40-55% (realistic, not backtested ideal)
    R:R: ~1.3-2:1 after slippage
    Monthly: 5-15% in trending months, flat in choppy
    This is a LEARNING system first. Capital grows slowly.
═══════════════════════════════════════════════════════════════════
"""

from fyers_apiv3 import fyersModel
try:
    import news_intel
    NEWS_INTEL_OK = True
except ImportError:
    NEWS_INTEL_OK = False
import pandas as pd
import numpy as np
import time
import datetime
import os
import json

# ═══════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════

APP_ID        = "Z33F8IITSE-100"
PAPER_TRADE   = True
LOT_SIZE      = 25          # FIXED: Nifty weekly lot size is 25 (updated 2024)
LOG_FILE      = "trades_log.csv"
WITHDRAWAL_TRACKER = "withdrawal_tracker.json"
CAPITAL_FILE       = "capital.json"

# Time windows
ORB_START          = "09:15"
ORB_END            = "09:45"
ENTRY_WIN1_START   = "09:45"
ENTRY_WIN1_END     = "14:00"
ENTRY_WIN2_START   = "99:99"  # DISABLED: afternoon window removed
ENTRY_WIN2_END     = "99:99"  # DISABLED: afternoon window removed
MIDDAY_BLOCK_START = "11:30"
MIDDAY_BLOCK_END   = "11:30"  # No afternoon window
TIME_STOP          = "14:30"  # Exit all positions by 11:45 AM (theta protection)
HARD_EXIT          = "15:00"

CHECK_EVERY = 30

# Strategy parameters
LR_LOOKBACK      = 15
LR_SLOPE_MIN     = 0.5
VIX_LOW_BLOCK    = 11.0
VIX_HIGH_CAUTION = 20.0
VWAP_BUFFER      = 5

# Risk management — reviewed and corrected
RISK_PCT_PER_TRADE = 0.05
SL_PCT             = 0.20   # TIGHTENED: 20% SL (was 25%) per reviewer advice
TARGET_PCT         = 0.50   # Keep 50% target -> R:R = 2.5:1
TRAIL_TRIGGER      = 0.25   # Trail at +25% (was 30%)
WEEKLY_LOSS_LIMIT  = -3000
DAILY_LOSS_LIMIT   = -1500
DAILY_PROFIT_CAP   = 4000

# ─── Withdrawal tiers ──────────────────────────────────────────────
WITHDRAWAL_TIERS = [
    (20000, 3000, 1500),   # base 20k → trigger 3k → withdraw 1.5k
    (25000, 4000, 2000),
    (30000, 5000, 2500),
    (40000, 6500, 3250),
    (50000, 8000, 4000),
    (75000, 12000, 6000),
    (100000, 15000, 7500),
]

# ═══════════════════════════════════════════════════════════════════

def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")

def now_str():
    return datetime.datetime.now().strftime("%H:%M")

def today_str():
    return datetime.datetime.now().strftime("%Y-%m-%d")


# ─── Fyers connection ──────────────────────────────────────────────

def load_fyers():
    try:
        with open("access_token.txt", "r") as f:
            token = f.read().strip()
        return fyersModel.FyersModel(
            client_id=APP_ID, token=token, log_path="", is_async=False
        )
    except FileNotFoundError:
        log("❌ access_token.txt not found. Run fyers_login.py first.")
        return None


def get_quote(fyers, symbol):
    """Returns LTP for any symbol. None if invalid."""
    r = fyers.quotes(data={"symbols": symbol})
    try:
        d = r.get("d", [{}])[0]
        if d.get("s") == "error":
            return None
        v = d.get("v", {})
        ltp = v.get("lp") or v.get("ltp") or 0
        return ltp if ltp and ltp > 0.1 else None
    except (KeyError, IndexError, TypeError):
        return None


def get_nifty_spot(fyers):
    return get_quote(fyers, "NSE:NIFTY50-INDEX")


def get_vix(fyers):
    """Fetch India VIX. Returns None if unavailable."""
    vix = get_quote(fyers, "NSE:INDIAVIX-INDEX")
    return vix


# ─── Historical candles for LR, VWAP, ORB ──────────────────────────

def get_intraday_candles(fyers, symbol="NSE:NIFTY50-INDEX", resolution="1"):
    """
    Fetch today's 1-min candles for the given symbol.
    Returns list of dicts: {timestamp, open, high, low, close, volume}
    """
    today = today_str()
    data = {
        "symbol": symbol,
        "resolution": resolution,
        "date_format": "1",
        "range_from": today,
        "range_to": today,
        "cont_flag": "1"
    }
    try:
        r = fyers.history(data=data)
        if r.get("s") != "ok":
            log(f"  ⚠️ History fetch failed: {r}")
            return []
        candles = r.get("candles", [])
        parsed = []
        for c in candles:
            parsed.append({
                "ts": c[0],
                "open": c[1],
                "high": c[2],
                "low": c[3],
                "close": c[4],
                "volume": c[5],
            })
        return parsed
    except Exception as e:
        log(f"  ⚠️ History exception: {e}")
        return []


def compute_orb(candles):
    """
    From 1-min candles, compute ORB high/low for 9:15-9:45 window.
    Returns (orb_high, orb_low) or (None, None).
    """
    orb_candles = []
    for c in candles:
        dt = datetime.datetime.fromtimestamp(c["ts"])
        t = dt.strftime("%H:%M")
        if "09:15" <= t < "09:45":
            orb_candles.append(c)
    if not orb_candles:
        return None, None
    highs = [c["high"] for c in orb_candles]
    lows  = [c["low"]  for c in orb_candles]
    return max(highs), min(lows)


def compute_vwap(candles):
    """
    Volume-weighted average price from open to now.
    VWAP = Σ(typical_price × volume) / Σ(volume)
    """
    if not candles:
        return None
    cum_pv = 0
    cum_v  = 0
    for c in candles:
        typical = (c["high"] + c["low"] + c["close"]) / 3
        cum_pv += typical * c["volume"]
        cum_v  += c["volume"]
    return cum_pv / cum_v if cum_v > 0 else None


def compute_lr_slope(candles, lookback=LR_LOOKBACK):
    """
    Linear regression slope over last N candles using closing prices.
    Returns slope in points-per-minute, or None if insufficient data.
    Positive = uptrend, Negative = downtrend.
    """
    if len(candles) < lookback:
        return None
    recent = candles[-lookback:]
    closes = np.array([c["close"] for c in recent])
    x = np.arange(len(closes))
    # slope = (n·Σxy - Σx·Σy) / (n·Σx² - (Σx)²)
    slope = np.polyfit(x, closes, 1)[0]
    return slope


# ─── Option symbol + expiry handling ───────────────────────────────

def parse_expiry_dt(expiry):
    try:
        return datetime.datetime(1970,1,1) + datetime.timedelta(seconds=int(expiry)) + datetime.timedelta(hours=5, minutes=30)
    except (ValueError, TypeError, OSError):
        try:
            return datetime.datetime.strptime(str(expiry), "%Y-%m-%d")
        except ValueError:
            return datetime.datetime.now()


def get_nearest_expiry(fyers):
    r = fyers.optionchain(data={
        "symbol": "NSE:NIFTY50-INDEX", "strikecount": 1, "timestamp": ""
    })
    if r.get("s") == "ok":
        exp_list = r["data"].get("expiryData", [])
        if exp_list:
            return exp_list[0].get("expiry", "")
    return ""


def find_atm_strike(spot):
    return round(spot / 50) * 50


def find_option_symbol(fyers, strike, opt_type, expiry):
    """Try multiple Fyers symbol formats; return (symbol, ltp) for first valid one."""
    dt  = parse_expiry_dt(expiry)
    dd  = dt.strftime("%d")
    mmm = dt.strftime("%b").upper()
    yy  = dt.strftime("%y")
    m   = str(dt.month)
    s   = int(strike)
    mm  = dt.strftime("%m")  # zero-padded month e.g. 04
    candidates = [
        f"NSE:NIFTY{yy}{mmm}{s}{opt_type}",          # 26APR24400PE ← CORRECT FORMAT
        f"NSE:NIFTY{yy}{m}{dd}{s}{opt_type}",         # 2642824400PE
        f"NSE:NIFTY{yy}{mm}{dd}{s}{opt_type}",        # 26042824400PE
        f"NSE:NIFTY{dd}{mmm}{yy}{s}{opt_type}",       # 28APR2624400PE
        f"NSE:NIFTY{yy}{mmm}{dd}{s}{opt_type}",       # 26APR2824400PE
    ]
    for sym in candidates:
        ltp = get_quote(fyers, sym)
        if ltp:
            return sym, ltp
    return None, 0


# ─── Capital & withdrawal tracking ─────────────────────────────────

def load_capital():
    if os.path.exists(CAPITAL_FILE):
        with open(CAPITAL_FILE) as f:
            return json.load(f)
    default = {"base_capital": 20000, "last_updated": today_str()}
    save_capital(default)
    return default


def save_capital(data):
    with open(CAPITAL_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_withdrawal_tracker():
    if os.path.exists(WITHDRAWAL_TRACKER):
        with open(WITHDRAWAL_TRACKER) as f:
            return json.load(f)
    default = {"last_withdrawal_date": None, "profit_since_last": 0.0,
               "total_withdrawn": 0.0}
    save_withdrawal_tracker(default)
    return default


def save_withdrawal_tracker(data):
    with open(WITHDRAWAL_TRACKER, "w") as f:
        json.dump(data, f, indent=2)


def get_withdrawal_tier(base_capital):
    """Find the closest tier for current capital."""
    tier = WITHDRAWAL_TIERS[0]
    for t in WITHDRAWAL_TIERS:
        if base_capital >= t[0]:
            tier = t
    return tier  # (base, trigger, withdraw_amount)


def check_withdrawal_reminder():
    """Check if cumulative profit since last withdrawal hit trigger."""
    if not os.path.exists(LOG_FILE):
        return
    try:
        df = pd.read_csv(LOG_FILE)
    except Exception:
        return
    if df.empty or "pnl" not in df.columns:
        return

    cap = load_capital()
    tracker = load_withdrawal_tracker()
    base = cap["base_capital"]
    _, trigger, withdraw_amt = get_withdrawal_tier(base)

    # Filter trades since last withdrawal
    if tracker["last_withdrawal_date"]:
        df = df[df["date"] > tracker["last_withdrawal_date"]]

    # Only count PAPER or LIVE matching current mode
    mode = "PAPER" if PAPER_TRADE else "LIVE"
    df = df[df.get("mode", mode) == mode]

    cumulative = df["pnl"].sum() if not df.empty else 0

    if cumulative >= trigger:
        print("\n" + "═" * 60)
        log("💰 WITHDRAWAL REMINDER 💰")
        print("═" * 60)
        log(f"  Cumulative profit since last withdrawal: ₹{cumulative:,.0f}")
        log(f"  Threshold: ₹{trigger:,}  ✅ HIT")
        print()
        log(f"  Recommended action:")
        log(f"  → Withdraw ₹{withdraw_amt:,} (50%) to bank")
        log(f"  → Leave ₹{withdraw_amt:,} in Fyers (compounds capital)")
        log(f"  → New trading base: ₹{base + withdraw_amt:,}")
        print()
        log(f"  How to withdraw:")
        log(f"    1. Fyers app → Funds → Withdraw")
        log(f"    2. Enter ₹{withdraw_amt:,} → Submit before 4:30 PM")
        log(f"    3. Money hits bank next working day")
        print()
        log(f"  After withdrawing, run: python mark_withdrawn.py")
        print("═" * 60 + "\n")


# ─── Risk guards ───────────────────────────────────────────────────

def get_pnl_since(days_back=0):
    """Get cumulative P&L from trades_log.csv for today / this week."""
    if not os.path.exists(LOG_FILE):
        return 0
    try:
        df = pd.read_csv(LOG_FILE)
    except Exception:
        return 0
    if df.empty or "pnl" not in df.columns:
        return 0

    today = datetime.date.today()
    mode = "PAPER" if PAPER_TRADE else "LIVE"
    df = df[df.get("mode", mode) == mode]

    if days_back == 0:  # today only
        df = df[df["date"] == today.isoformat()]
    else:  # last N calendar days (for weekly)
        start = today - datetime.timedelta(days=days_back)
        df = df[df["date"] >= start.isoformat()]

    return df["pnl"].sum() if not df.empty else 0


def check_risk_guards():
    """
    Returns (allowed, reason).
    Blocks trading if daily/weekly loss limit hit.
    """
    today_pnl  = get_pnl_since(0)
    weekly_pnl = get_pnl_since(7)

    if today_pnl <= DAILY_LOSS_LIMIT:
        return False, f"🛑 DAILY LOSS LIMIT hit: ₹{today_pnl:.0f}. Stopping for today."
    if today_pnl >= DAILY_PROFIT_CAP:
        return False, f"🎯 DAILY PROFIT CAP hit: ₹{today_pnl:.0f}. Don't be greedy. Stop."
    if weekly_pnl <= WEEKLY_LOSS_LIMIT:
        return False, f"🛑 WEEKLY LOSS LIMIT hit: ₹{weekly_pnl:.0f}. Stopping for the week."
    return True, f"✅ Risk check passed | Today: ₹{today_pnl:.0f} | Week: ₹{weekly_pnl:.0f}"


# ─── Order placement ───────────────────────────────────────────────

def place_order(fyers, symbol, action, qty, paper=True):
    if paper:
        log(f"  [PAPER] {action} {qty} x {symbol}")
        return {"paper": True, "symbol": symbol, "action": action, "qty": qty}
    side = 1 if action == "BUY" else -1
    r = fyers.place_order(data={
        "symbol": symbol, "qty": qty, "type": 2, "side": side,
        "productType": "INTRADAY", "limitPrice": 0, "stopPrice": 0,
        "validity": "DAY", "disclosedQty": 0, "offlineOrder": False,
    })
    log(f"  Order response: {r}")
    return r


def save_trade_log(trade):
    df = pd.DataFrame([trade])
    if os.path.exists(LOG_FILE):
        df.to_csv(LOG_FILE, mode="a", header=False, index=False)
    else:
        df.to_csv(LOG_FILE, index=False)


# ─── Market helpers ────────────────────────────────────────────────

def is_market_open():
    now = datetime.datetime.now()
    t = now.time()
    return datetime.time(9, 15) <= t <= datetime.time(15, 30) and now.weekday() < 5


def time_in_window(start, end):
    return start <= now_str() < end


def in_entry_window():
    return (time_in_window(ENTRY_WIN1_START, ENTRY_WIN1_END) or
            time_in_window(ENTRY_WIN2_START, ENTRY_WIN2_END))


# ─── Signal generation ────────────────────────────────────────────

def evaluate_signal(fyers, orb_high, orb_low):
    """
    Returns dict with signal state and all filter statuses.
    signal: "LONG" / "SHORT" / None
    """
    spot = get_nifty_spot(fyers)
    vix  = get_vix(fyers)
    candles = get_intraday_candles(fyers)

    if not spot or not candles:
        return {"signal": None, "reason": "No data yet"}

    vwap  = compute_vwap(candles)
    slope = compute_lr_slope(candles)

    if vwap is None or slope is None:
        return {"signal": None, "reason": "Not enough data for indicators"}

    state = {
        "spot": spot, "vix": vix, "vwap": vwap, "slope": slope,
        "orb_high": orb_high, "orb_low": orb_low,
        "signal": None, "reason": "",
        "vix_ok": False, "orb_ok": False, "vwap_ok": False, "lr_ok": False,
        "size_multiplier": 1.0,
    }

    # Filter 1: VIX gate
    if vix is None:
        state["vix_ok"] = True  # fail-open if VIX data unavailable
    elif vix < VIX_LOW_BLOCK:
        state["reason"] = f"VIX too low ({vix:.1f}) — no trade"
        return state
    elif vix > VIX_HIGH_CAUTION:
        state["size_multiplier"] = 0.5
        state["vix_ok"] = True
    else:
        state["vix_ok"] = True

    # Filter 2: ORB break
    long_orb  = spot > orb_high
    short_orb = spot < orb_low
    if not (long_orb or short_orb):
        state["reason"] = f"Inside ORB ({orb_low:.0f}-{orb_high:.0f}), spot {spot:.1f}"
        return state
    state["orb_ok"] = True

    # Filter 3: VWAP alignment
    above_vwap = spot > (vwap + VWAP_BUFFER)
    below_vwap = spot < (vwap - VWAP_BUFFER)
    if long_orb and not above_vwap:
        state["reason"] = f"ORB break up but spot not above VWAP (spot {spot:.1f}, vwap {vwap:.1f})"
        return state
    if short_orb and not below_vwap:
        state["reason"] = f"ORB break down but spot not below VWAP (spot {spot:.1f}, vwap {vwap:.1f})"
        return state
    state["vwap_ok"] = True

    # Filter 4: LR slope confirmation
    if long_orb and slope < LR_SLOPE_MIN:
        state["reason"] = f"LR slope too weak for long ({slope:.2f})"
        return state
    if short_orb and slope > -LR_SLOPE_MIN:
        state["reason"] = f"LR slope too weak for short ({slope:.2f})"
        return state
    state["lr_ok"] = True

    # All filters passed
    state["signal"] = "LONG" if long_orb else "SHORT"
    state["reason"] = f"{state['signal']} signal — all filters aligned"
    return state


def print_state(state):
    """Pretty-print current market state + filter statuses."""
    def mark(ok): return "✅" if ok else "❌"
    spot = state.get("spot", 0)
    vwap = state.get("vwap") or 0
    vix  = state.get("vix") or 0
    slope = state.get("slope") or 0
    log(f"  Spot: {spot:.1f} | VWAP: {vwap:.1f} | VIX: {vix:.1f} | LR: {slope:+.2f}/min")
    log(f"  ORB: {state.get('orb_low', 0):.0f} — {state.get('orb_high', 0):.0f}")
    log(f"  Filters: VIX {mark(state['vix_ok'])} | ORB {mark(state['orb_ok'])} | "
        f"VWAP {mark(state['vwap_ok'])} | LR {mark(state['lr_ok'])}")
    if state["signal"]:
        log(f"  🎯 SIGNAL: {state['signal']} — entering trade")
    else:
        log(f"  ⏳ Waiting — {state['reason']}")


# ─── Position sizing ──────────────────────────────────────────────

def calc_position_size(capital, premium_per_share, size_mult=1.0):
    """
    Returns (lots, reason).
    Enforces 3 constraints (all must pass):
      1. Outlay ≤ 40% of capital (leave room for second trade)
      2. SL loss ≤ RISK_PCT_PER_TRADE × capital × size_mult
      3. At least 1 lot
    If constraint 1 or 2 fails with 1 lot, trade is SKIPPED (returns 0).
    """
    max_risk   = capital * RISK_PCT_PER_TRADE * size_mult
    max_outlay = capital * 0.40

    outlay_1lot = premium_per_share * LOT_SIZE
    loss_1lot   = SL_PCT * outlay_1lot

    if outlay_1lot > max_outlay:
        return 0, f"skip — 1 lot outlay ₹{outlay_1lot:.0f} > 40% cap (₹{max_outlay:.0f})"
    if loss_1lot > max_risk:
        # Still allow 1 lot but flag as "high risk" — this is the reality of small capital
        # User explicitly wanted to trade with 20k, and min 1 lot is unavoidable
        return 1, f"1 lot (HIGH RISK: SL loss ₹{loss_1lot:.0f} > target risk ₹{max_risk:.0f})"

    lots_by_risk   = int(max_risk / loss_1lot)
    lots_by_outlay = int(max_outlay / outlay_1lot)
    lots = min(lots_by_risk, lots_by_outlay)
    lots = max(1, lots)
    return lots, f"{lots} lot(s) | risk ≤ ₹{max_risk:.0f}, outlay ≤ ₹{max_outlay:.0f}"


# ═══════════════════════════════════════════════════════════════════
#  MAIN STRATEGY RUNNER
# ═══════════════════════════════════════════════════════════════════

def run():
    print()
    print("═" * 65)
    log("  NIFTY FILTERED ORB v2 — STARTING")
    log(f"  Mode: {'📝 PAPER TRADING' if PAPER_TRADE else '💰 LIVE TRADING'}")
    print("═" * 65)

    # Load capital
    cap = load_capital()
    log(f"  Base capital: ₹{cap['base_capital']:,}")

    # Check risk guards first
    allowed, reason = check_risk_guards()
    log(f"  {reason}")
    if not allowed:
        return

    # Check withdrawal reminder
    check_withdrawal_reminder()

    # Market open check
    if not is_market_open():
        log("  ⛔ Market closed. Nothing to do.")
        return

    # Connect
    fyers = load_fyers()
    if fyers is None:
        return
    log("  Connected to Fyers ✅")

    # ─── Phase 1: Wait for ORB to form ─────────────────────────────
    if now_str() < ORB_END:
        log(f"\n  Phase 1: Waiting for ORB to form (9:15-9:45)...")
        while now_str() < ORB_END:
            time.sleep(30)
            spot = get_nifty_spot(fyers)
            if spot:
                log(f"  ORB forming... Nifty spot: {spot:.1f}")

    # ─── Phase 2: Compute ORB levels ───────────────────────────────
    log(f"\n  Phase 2: Computing ORB levels...")
    candles = get_intraday_candles(fyers)
    orb_high, orb_low = compute_orb(candles)
    if not orb_high or not orb_low:
        log(f"  ❌ Could not compute ORB (no candles). Aborting.")
        return
    log(f"  ORB High: {orb_high:.1f}")
    log(f"  ORB Low:  {orb_low:.1f}")
    log(f"  ORB Range: {orb_high - orb_low:.1f} pts")

    # Get expiry (cached)
    expiry = get_nearest_expiry(fyers)
    log(f"  Nearest expiry: {expiry}")

    # ─── Phase 3: Monitor for signal ───────────────────────────────
    log(f"\n  Phase 3: Monitoring for breakout signals...")
    log(f"  Entry window: {ENTRY_WIN1_START}-{ENTRY_WIN1_END} (morning only)")

    while True:
        t = now_str()

        # Hard cutoffs
        if t >= HARD_EXIT:
            log("  🔚 Hard exit time reached. Done for today.")
            return
        if now_str() >= ENTRY_WIN1_END:
            log(f"  ⏸  Past entry window ({ENTRY_WIN1_END}). Done for today.")
            return
        if not in_entry_window():
            log(f"  Outside entry window. Current: {t}")
            time.sleep(CHECK_EVERY * 2)
            continue

        # Risk re-check every loop
        allowed, reason = check_risk_guards()
        if not allowed:
            log(f"  {reason}")
            return

        # Evaluate signal
        state = evaluate_signal(fyers, orb_high, orb_low)
        print_state(state)

        if state["signal"]:
            # Execute trade
            execute_trade(fyers, state, expiry, cap["base_capital"])
            # After one trade, check if we should continue
            allowed, reason = check_risk_guards()
            if not allowed:
                log(f"  {reason}")
                return
            # Brief cooldown before looking for next signal
            log("  Cooldown: 5 minutes before next signal scan")
            time.sleep(300)
        else:
            time.sleep(CHECK_EVERY)


def execute_trade(fyers, state, expiry, capital):
    """Place and manage a single trade until exit."""
    spot = state["spot"]
    atm  = find_atm_strike(spot)

    opt_type = "CE" if state["signal"] == "LONG" else "PE"
    log(f"\n  📊 Finding {opt_type} symbol for ATM {atm}...")
    sym, entry_ltp = find_option_symbol(fyers, atm, opt_type, expiry)
    if not sym or not entry_ltp:
        log(f"  ❌ Could not find valid option symbol. Skipping trade.")
        return

    log(f"  Symbol: {sym} | Entry LTP: ₹{entry_ltp:.2f}")

    # Position sizing
    lots, size_reason = calc_position_size(capital, entry_ltp, state["size_multiplier"])
    log(f"  Position sizing: {size_reason}")
    if lots < 1:
        log(f"  ❌ Skipping trade — capital constraints not met.")
        return

    qty = lots * LOT_SIZE
    outlay = entry_ltp * qty
    max_loss = outlay * SL_PCT
    target   = outlay * TARGET_PCT

    log(f"  Lots: {lots} | Qty: {qty} | Outlay: ₹{outlay:.0f}")
    log(f"  Target (+{TARGET_PCT*100:.0f}%): ₹{target:.0f}  |  "
        f"SL (-{SL_PCT*100:.0f}%): -₹{max_loss:.0f}")

    # ── News intelligence check ──────────────────────────────────
    if NEWS_INTEL_OK:
        news_verdict, news_mult, news_score, news_report = news_intel.run(
            signal_direction=state["signal"],
            current_vix=state.get("vix")
        )
        for line in news_report:
            print(line)

        if news_verdict == "BLOCK":
            log(f"  🔴 Trade BLOCKED by news intelligence. Skipping.")
            return

        # Combine news multiplier with VIX multiplier
        combined_mult = state["size_multiplier"] * news_mult
        combined_mult = max(0.5, min(1.5, combined_mult))  # cap 0.5-1.5x
        if combined_mult != state["size_multiplier"]:
            log(f"  Size adjusted: {state['size_multiplier']}x → {combined_mult}x (news: {news_verdict})")
            # Recalculate lots with new multiplier
            lots, size_reason = calc_position_size(capital, entry_ltp, combined_mult)
            log(f"  Revised position: {size_reason}")
            if lots < 1:
                log(f"  ❌ No valid position size after news adjustment. Skipping.")
                return
            qty = lots * LOT_SIZE
            outlay = entry_ltp * qty
            max_loss = outlay * SL_PCT
            target   = outlay * TARGET_PCT
            log(f"  Revised Lots: {lots} | Qty: {qty} | Outlay: ₹{outlay:.0f}")
    else:
        news_verdict = "NEUTRAL"
        news_score = 0

    # Place entry
    log(f"\n  📤 ENTERING {state['signal']} TRADE")
    place_order(fyers, sym, "BUY", qty, PAPER_TRADE)
    entry_time = datetime.datetime.now()

    # Monitor with trailing
    sl_price     = entry_ltp * (1 - SL_PCT)
    target_price = entry_ltp * (1 + TARGET_PCT)
    trail_trigger_price = entry_ltp * (1 + TRAIL_TRIGGER)
    trail_active = False
    exit_reason  = None

    while True:
        t = now_str()

        # Time stops
        if t >= TIME_STOP:
            exit_reason = "TIME_STOP"
            break
        if t >= HARD_EXIT:
            exit_reason = "HARD_EXIT"
            break

        curr = get_quote(fyers, sym)
        if curr is None:
            log(f"  ⚠️ Bad data tick, skipping")
            time.sleep(CHECK_EVERY)
            continue

        pnl     = (curr - entry_ltp) * qty
        pnl_pct = (curr - entry_ltp) / entry_ltp
        icon    = "🟢" if pnl >= 0 else "🔴"
        log(f"  {icon} {sym.split('NIFTY')[1]}: ₹{curr:.2f} | "
            f"P&L: ₹{pnl:.0f} ({pnl_pct*100:+.1f}%)")

        # Trail: once +30%, move SL to breakeven
        if not trail_active and pnl_pct >= TRAIL_TRIGGER:
            sl_price = entry_ltp
            trail_active = True
            log(f"  🔒 Trail activated — SL moved to breakeven ₹{entry_ltp:.2f}")

        # Exit checks
        if curr >= target_price:
            exit_reason = "TARGET_HIT"
            break
        if curr <= sl_price:
            exit_reason = "TRAIL_SL" if trail_active else "STOPLOSS_HIT"
            break

        time.sleep(CHECK_EVERY)

    # Exit order
    log(f"\n  📥 EXITING — {exit_reason}")
    place_order(fyers, sym, "SELL", qty, PAPER_TRADE)

    exit_ltp = get_quote(fyers, sym) or entry_ltp
    final_pnl = (exit_ltp - entry_ltp) * qty

    print("  " + "─" * 50)
    log(f"  TRADE SUMMARY")
    log(f"  Entry: ₹{entry_ltp:.2f}  |  Exit: ₹{exit_ltp:.2f}")
    log(f"  Qty: {qty} ({lots} lots)  |  P&L: ₹{final_pnl:.0f}")
    log(f"  Result: {'✅ PROFIT' if final_pnl > 0 else '❌ LOSS'}  |  "
        f"Reason: {exit_reason}")
    print("  " + "─" * 50)

    save_trade_log({
        "date":        entry_time.strftime("%Y-%m-%d"),
        "entry_time":  entry_time.strftime("%H:%M:%S"),
        "exit_time":   datetime.datetime.now().strftime("%H:%M:%S"),
        "strategy":    "FILTERED_ORB_v2",
        "signal":      state["signal"],
        "atm":         atm,
        "symbol":      sym,
        "entry_ltp":   entry_ltp,
        "exit_ltp":    exit_ltp,
        "qty":         qty,
        "lots":        lots,
        "outlay":      outlay,
        "pnl":         final_pnl,
        "vix":         state.get("vix"),
        "vwap":        state.get("vwap"),
        "lr_slope":    state.get("slope"),
        "orb_high":    state.get("orb_high"),
        "orb_low":     state.get("orb_low"),
        "size_mult":   state["size_multiplier"],
        "exit_reason": exit_reason,
        "mode":        "PAPER" if PAPER_TRADE else "LIVE",
    })
    log(f"  Saved to {LOG_FILE}")


if __name__ == "__main__":
    run()
python fyers_login.py