"""
v3_main.py
Entry point. Run this and walk away.

  python v3_main.py

What it does:
  1. Loads Fyers session (requires token.txt from fyers_login.py)
  2. Resumes any open position from previous run (if any)
  3. Waits until 11:00 AM
  4. Classifies regime, deploys appropriate strategy
  5. Monitors P&L every 30s, exits on target/SL
  6. Force-exits at 14:30 (soft) / 14:45 (hard)
  7. Logs everything to trades_log_v3.csv

Daily routine:
  9:15 AM — run fyers_login.py (get token)
  Any time before 11:00 — run python v3_main.py
  Walk away. It handles entry, monitoring, exit, logging.
"""

import time
import sys
import traceback
from datetime import datetime

from v3_config import *
from v3_utils import (
    get_fyers_session, log_print, market_is_open, seconds_until,
    load_capital, update_capital, log_trade, save_position_state,
    load_position_state, clear_position_state, get_today_pnl, get_week_pnl
)
from v3_regime import RegimeDetector
from v3_strategies import IronFly, BullCallSpread, BearPutSpread


# ============ STRATEGY ROUTER ============

def select_strategy(regime, fyers):
    """Pick the strategy class for the regime."""
    if regime == "RANGE":
        return IronFly(fyers)
    if regime == "UPTREND":
        return BullCallSpread(fyers)
    if regime == "DOWNTREND":
        return BearPutSpread(fyers)
    return None


def get_strategy_for_position(position, fyers):
    """Recreate strategy object from a persisted position."""
    name = position.get("strategy")
    if name == "IRON_FLY":
        return IronFly(fyers)
    if name == "BULL_CALL_SPREAD":
        return BullCallSpread(fyers)
    if name == "BEAR_PUT_SPREAD":
        return BearPutSpread(fyers)
    return None


# ============ RISK CHECKS ============

def pre_trade_risk_check():
    """Block new trades if daily/weekly limits hit."""
    today_pnl = get_today_pnl()
    week_pnl = get_week_pnl()
    log_print(f"Today P&L so far: ₹{today_pnl:.0f}  |  Week P&L: ₹{week_pnl:.0f}")

    if today_pnl <= DAILY_LOSS_LIMIT:
        log_print(f"BLOCKED: Daily loss limit hit ({today_pnl:.0f} <= {DAILY_LOSS_LIMIT})", "ERROR")
        return False
    if today_pnl >= DAILY_PROFIT_CAP:
        log_print(f"BLOCKED: Daily profit cap reached ({today_pnl:.0f} >= {DAILY_PROFIT_CAP})", "INFO")
        return False
    if week_pnl <= WEEKLY_LOSS_LIMIT:
        log_print(f"BLOCKED: Weekly loss limit hit ({week_pnl:.0f} <= {WEEKLY_LOSS_LIMIT})", "ERROR")
        return False
    return True


# ============ MONITORING LOOP ============

def monitor_and_exit(fyers, strategy, position):
    """Loop: check P&L, exit on triggers, force exit at 14:30/14:45."""
    log_print("Entering monitoring loop. Press Ctrl+C to interrupt.")

    while True:
        try:
            now = datetime.now()
            cur_time = now.time()

            # Hard exit window
            if (cur_time.hour > HARD_EXIT_HOUR
                    or (cur_time.hour == HARD_EXIT_HOUR and cur_time.minute >= HARD_EXIT_MINUTE)):
                log_print("HARD EXIT TIME REACHED", "TRADE")
                closed = strategy.close(position)
                closed["exit_reason"] = "HARD_EXIT_1445"
                return closed

            # Soft exit window — close regardless of P&L
            if (cur_time.hour > SOFT_EXIT_HOUR
                    or (cur_time.hour == SOFT_EXIT_HOUR and cur_time.minute >= SOFT_EXIT_MINUTE)):
                log_print("SOFT EXIT TIME REACHED", "TRADE")
                closed = strategy.close(position)
                closed["exit_reason"] = "SOFT_EXIT_1430"
                return closed

            # Check trigger-based exits
            should_exit, reason, state = strategy.should_exit(position)
            pnl = state.get("total_pnl", 0)
            log_print(f"P&L: ₹{pnl:+.0f}  |  Status: monitoring")

            if should_exit:
                log_print(f"EXIT TRIGGER: {reason}", "TRADE")
                closed = strategy.close(position)
                closed["exit_reason"] = reason
                return closed

            time.sleep(MONITOR_INTERVAL_SEC)

        except KeyboardInterrupt:
            log_print("INTERRUPTED by user — closing position", "WARN")
            closed = strategy.close(position)
            closed["exit_reason"] = "MANUAL_INTERRUPT"
            return closed
        except Exception as e:
            log_print(f"Loop exception: {e}", "ERROR")
            log_print(traceback.format_exc(), "ERROR")
            time.sleep(30)


# ============ TRADE FINALIZATION ============

def finalize_trade(position):
    """Log closed trade to CSV and update capital."""
    cap = update_capital(position.get("pnl", 0))
    log_print(f"Capital now: ₹{cap['current_capital']:,.0f}  "
              f"Total trades: {cap['trades_count']}  "
              f"Wins: {cap['winning_trades']}  Losses: {cap['losing_trades']}")

    record = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "entry_time": position.get("entry_time"),
        "exit_time": position.get("exit_time"),
        "regime": position.get("regime_info", {}).get("regime", ""),
        "strategy": position.get("strategy"),
        "spot_at_entry": position.get("spot_at_entry"),
        "vix_at_entry": position.get("vix_at_entry"),
        "lots": position.get("lots"),
        "entry_price": (position.get("net_credit_entry")
                        or position.get("net_debit_entry")),
        "exit_price": (position.get("net_debit_exit")
                       or position.get("exit_value")),
        "pnl": position.get("pnl"),
        "pnl_per_lot": position.get("pnl_per_lot"),
        "exit_reason": position.get("exit_reason"),
        "morning_high": position.get("regime_info", {}).get("morning_high"),
        "morning_low": position.get("regime_info", {}).get("morning_low"),
        "details_json": position,
    }
    log_trade(record)
    clear_position_state()


# ============ MAIN ============

def main():
    print()
    print("=" * 70)
    print(" MyAlgo v3 — 11 AM Adaptive Hedged System ")
    print("=" * 70)

    if not market_is_open() and datetime.now().time().hour < 9:
        log_print("Market not yet open. Will wait.")
    elif not market_is_open():
        log_print("Market closed (weekend or after-hours). Exiting.", "WARN")
        return

    # Show capital state
    cap = load_capital()
    log_print(f"Capital: ₹{cap['current_capital']:,.0f}  "
              f"(start ₹{cap['starting_capital']:,.0f}, "
              f"total P&L ₹{cap['total_pnl']:+,.0f})")
    log_print(f"Mode: {'PAPER' if PAPER_TRADE else 'LIVE'}")

    try:
        fyers = get_fyers_session()
        # Test connection
        profile = fyers.get_profile()
        if profile.get("s") != "ok":
            log_print(f"Fyers auth failed: {profile}", "ERROR")
            return
        log_print(f"Connected as {profile['data'].get('name','?')}")
    except Exception as e:
        log_print(f"Failed to init Fyers: {e}", "ERROR")
        log_print("Run `python fyers_login.py` first to refresh the token.", "ERROR")
        return

    # ----- Resume any open position -----
    existing = load_position_state()
    if existing and existing.get("status") == "OPEN":
        log_print(f"Resuming open {existing['strategy']} from {existing['entry_time']}",
                  "INFO")
        strategy = get_strategy_for_position(existing, fyers)
        if strategy:
            closed = monitor_and_exit(fyers, strategy, existing)
            finalize_trade(closed)
            return

    # ----- Wait until 11 AM -----
    wait_secs = seconds_until(ENTRY_HOUR, ENTRY_MINUTE)
    if wait_secs > 0:
        log_print(f"Waiting {wait_secs/60:.1f} minutes until {ENTRY_HOUR:02d}:"
                  f"{ENTRY_MINUTE:02d} entry time...")
        while wait_secs > 0:
            sleep_for = min(60, wait_secs)
            time.sleep(sleep_for)
            wait_secs = seconds_until(ENTRY_HOUR, ENTRY_MINUTE)
            if wait_secs > 60:
                mins = wait_secs / 60
                if int(mins) % 5 == 0:
                    log_print(f"...{mins:.1f} min to entry")
    elif wait_secs < -ENTRY_GRACE_MINUTES * 60:
        log_print(f"It's past {ENTRY_HOUR:02d}:{ENTRY_MINUTE+ENTRY_GRACE_MINUTES} — "
                  f"entry window missed, skipping today.", "WARN")
        return

    # ----- Classify regime -----
    log_print("Reached entry time. Classifying regime...")
    detector = RegimeDetector(fyers)
    result = detector.classify()

    if result["regime"] == "NO_TRADE":
        log_print(f"NO_TRADE today. Reason: {result.get('reason')}", "RESULT")
        return

    # ----- Pre-trade risk check -----
    if not pre_trade_risk_check():
        log_print("Risk limits hit. No new trade today.", "WARN")
        return

    # ----- Deploy strategy -----
    strategy = select_strategy(result["regime"], fyers)
    if not strategy:
        log_print(f"No strategy for regime {result['regime']}", "ERROR")
        return

    position = strategy.deploy(
        spot=result["spot"],
        vix=result["vix"],
        regime_info=result
    )
    if not position:
        log_print("Strategy deployment failed. Aborting.", "ERROR")
        return

    # ----- Monitor & exit -----
    closed = monitor_and_exit(fyers, strategy, position)
    finalize_trade(closed)
    log_print("Day complete. See you tomorrow.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_print(f"FATAL: {e}", "FATAL")
        log_print(traceback.format_exc(), "FATAL")
        sys.exit(1)
