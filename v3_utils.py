"""
v3_utils.py
Helper functions for option chain fetching, LTP, P&L calculation,
logging, and capital tracking.
"""

import os
import json
import csv
from datetime import datetime, time as dtime
from v3_config import *


# ============ OPTION SYMBOL & PRICE HELPERS ============

def get_atm_strike(spot, step=STRIKE_STEP):
    """Round spot to nearest valid strike."""
    return int(round(spot / step) * step)


def fetch_option_chain(fyers, strikecount=15):
    """Fetch live option chain from Fyers for nearest expiry."""
    try:
        data = {"symbol": NIFTY_SYMBOL, "strikecount": strikecount, "timestamp": ""}
        res = fyers.optionchain(data=data)
        if res.get("s") == "ok":
            return res.get("data", {})
        print(f"[CHAIN] Fetch failed: {res}")
    except Exception as e:
        print(f"[CHAIN] Exception: {e}")
    return None


def find_option_symbol(chain_data, strike, opt_type):
    """Find Fyers symbol from chain for given strike + type (CE/PE)."""
    if not chain_data:
        return None, None
    options = chain_data.get("optionsChain", [])
    for opt in options:
        if (int(opt.get("strike_price", 0)) == int(strike)
                and opt.get("option_type") == opt_type):
            return opt.get("symbol"), opt.get("ltp", 0)
    return None, None


def get_ltp(fyers, symbol):
    """Get last traded price for a symbol."""
    try:
        res = fyers.quotes(data={"symbols": symbol})
        if res.get("s") == "ok" and res.get("d"):
            return float(res["d"][0]["v"].get("lp", 0))
    except Exception as e:
        print(f"[LTP] {symbol} failed: {e}")
    return None


def get_multi_ltp(fyers, symbols):
    """Batch LTP fetch — returns dict {symbol: ltp}."""
    result = {s: None for s in symbols}
    try:
        res = fyers.quotes(data={"symbols": ",".join(symbols)})
        if res.get("s") == "ok":
            for item in res.get("d", []):
                sym = item.get("n")
                ltp = float(item.get("v", {}).get("lp", 0))
                if sym in result:
                    result[sym] = ltp
    except Exception as e:
        print(f"[MULTI_LTP] Failed: {e}")
    return result


# ============ TIME HELPERS ============

def now_ist():
    """Current datetime — assumes server is set to IST or we're running locally."""
    return datetime.now()


def market_is_open():
    """Mon-Fri, 9:15 to 15:30."""
    now = now_ist()
    if now.weekday() >= 5:  # Sat=5, Sun=6
        return False
    market_open = dtime(9, 15)
    market_close = dtime(15, 30)
    return market_open <= now.time() <= market_close


def seconds_until(target_hour, target_minute):
    """Seconds from now until target time today. Negative if past."""
    now = now_ist()
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    return (target - now).total_seconds()


# ============ CAPITAL TRACKING ============

def load_capital():
    """Load current capital state."""
    if not os.path.exists(CAPITAL_FILE):
        save_capital({
            "starting_capital": CAPITAL,
            "current_capital": CAPITAL,
            "total_pnl": 0,
            "trades_count": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "last_updated": now_ist().isoformat()
        })
    with open(CAPITAL_FILE, "r") as f:
        return json.load(f)


def save_capital(state):
    """Persist capital state."""
    state["last_updated"] = now_ist().isoformat()
    with open(CAPITAL_FILE, "w") as f:
        json.dump(state, f, indent=2)


def update_capital(pnl):
    """Update capital after a trade closes."""
    state = load_capital()
    state["current_capital"] += pnl
    state["total_pnl"] += pnl
    state["trades_count"] += 1
    if pnl > 0:
        state["winning_trades"] += 1
    elif pnl < 0:
        state["losing_trades"] += 1
    save_capital(state)
    return state


def get_today_pnl():
    """Calculate today's realized P&L from trades log."""
    if not os.path.exists(TRADES_LOG):
        return 0.0
    today_str = now_ist().strftime("%Y-%m-%d")
    total = 0.0
    try:
        with open(TRADES_LOG, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("date") == today_str and row.get("pnl"):
                    total += float(row["pnl"])
    except Exception as e:
        print(f"[PNL] Read error: {e}")
    return total


def get_week_pnl():
    """Calculate this week's realized P&L."""
    if not os.path.exists(TRADES_LOG):
        return 0.0
    now = now_ist()
    monday = now - timedelta_local(now.weekday())
    monday_str = monday.strftime("%Y-%m-%d")
    total = 0.0
    try:
        with open(TRADES_LOG, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("date", "") >= monday_str and row.get("pnl"):
                    total += float(row["pnl"])
    except Exception as e:
        print(f"[PNL_WEEK] {e}")
    return total


def timedelta_local(days):
    from datetime import timedelta
    return timedelta(days=days)


# ============ TRADE LOGGING ============

TRADE_LOG_COLUMNS = [
    "date", "entry_time", "exit_time", "regime", "strategy",
    "spot_at_entry", "vix_at_entry", "lots",
    "entry_price", "exit_price", "pnl", "pnl_per_lot",
    "exit_reason", "morning_high", "morning_low", "details_json"
]


def init_trades_log():
    """Create CSV with header if missing."""
    if not os.path.exists(TRADES_LOG):
        with open(TRADES_LOG, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=TRADE_LOG_COLUMNS)
            writer.writeheader()


def log_trade(trade_record):
    """Append a closed trade to the CSV log."""
    init_trades_log()
    row = {col: trade_record.get(col, "") for col in TRADE_LOG_COLUMNS}
    if isinstance(row.get("details_json"), (dict, list)):
        row["details_json"] = json.dumps(row["details_json"])
    with open(TRADES_LOG, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_LOG_COLUMNS)
        writer.writerow(row)


# ============ POSITION STATE PERSISTENCE ============

def save_position_state(position):
    """Persist open position to disk so a script restart can resume."""
    with open(POSITION_STATE, "w") as f:
        json.dump(position, f, indent=2, default=str)


def load_position_state():
    """Load any persisted open position."""
    if not os.path.exists(POSITION_STATE):
        return None
    try:
        with open(POSITION_STATE, "r") as f:
            return json.load(f)
    except Exception:
        return None


def clear_position_state():
    """Remove the position file when the position closes."""
    if os.path.exists(POSITION_STATE):
        os.remove(POSITION_STATE)


# ============ FYERS SESSION ============

def get_fyers_session():
    """Load token and return authenticated Fyers session."""
    from fyers_apiv3 import fyersModel
    if not os.path.exists(TOKEN_FILE):
        raise FileNotFoundError(f"{TOKEN_FILE} not found. Run fyers_login.py first.")
    with open(TOKEN_FILE, "r") as f:
        token = f.read().strip()
    return fyersModel.FyersModel(client_id=BROKER_ID, token=token, log_path="")


# ============ LOGGING ============

def log_print(msg, level="INFO"):
    """Timestamped print + optional file log."""
    ts = now_ist().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")
    os.makedirs(DAILY_LOG_DIR, exist_ok=True)
    log_file = os.path.join(DAILY_LOG_DIR, f"{now_ist().strftime('%Y-%m-%d')}.log")
    with open(log_file, "a") as f:
        f.write(f"[{ts}] [{level}] {msg}\n")
