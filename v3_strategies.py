"""
v3_strategies.py
Three strategies:
  - IronFly       (RANGE regime)  — sell ATM CE+PE, buy OTM wings
  - BullCallSpread (UPTREND)      — buy ATM CE, sell OTM CE
  - BearPutSpread  (DOWNTREND)    — buy ATM PE, sell OTM PE

Each strategy has:
  deploy(spot)         — open the position, return position dict (or None)
  current_pnl(position)— live P&L
  should_exit(position)— returns (bool, reason) for exit triggers
  close(position)      — exit and return final P&L
"""

from datetime import datetime
from v3_config import *
from v3_utils import (
    get_atm_strike, fetch_option_chain, find_option_symbol,
    get_ltp, get_multi_ltp, log_print, save_position_state
)


# ================================================================
#  IRON FLY  (Mode 1 — RANGE)
# ================================================================

class IronFly:
    NAME = "IRON_FLY"

    def __init__(self, fyers):
        self.fyers = fyers

    def deploy(self, spot, vix=None, regime_info=None):
        atm = get_atm_strike(spot)
        upper_strike = atm + IF_WING_DISTANCE
        lower_strike = atm - IF_WING_DISTANCE

        log_print(f"[IF] Building Iron Fly. ATM={atm}, wings={lower_strike}/{upper_strike}")

        chain = fetch_option_chain(self.fyers)
        if not chain:
            log_print("[IF] option chain unavailable", "ERROR")
            return None

        atm_ce_sym, atm_ce_ltp = find_option_symbol(chain, atm, "CE")
        atm_pe_sym, atm_pe_ltp = find_option_symbol(chain, atm, "PE")
        up_ce_sym, up_ce_ltp = find_option_symbol(chain, upper_strike, "CE")
        lo_pe_sym, lo_pe_ltp = find_option_symbol(chain, lower_strike, "PE")

        if not all([atm_ce_sym, atm_pe_sym, up_ce_sym, lo_pe_sym]):
            log_print(f"[IF] symbol lookup failed CE={atm_ce_sym} PE={atm_pe_sym} "
                      f"UP={up_ce_sym} LO={lo_pe_sym}", "ERROR")
            return None

        # Validate LTPs
        for name, ltp in [("ATM_CE", atm_ce_ltp), ("ATM_PE", atm_pe_ltp),
                          ("UP_CE", up_ce_ltp), ("LO_PE", lo_pe_ltp)]:
            if not ltp or ltp <= 0:
                log_print(f"[IF] bad LTP for {name}: {ltp}", "ERROR")
                return None

        net_credit = atm_ce_ltp + atm_pe_ltp - up_ce_ltp - lo_pe_ltp
        log_print(f"[IF] Premiums  ATM_CE={atm_ce_ltp:.2f}  ATM_PE={atm_pe_ltp:.2f}  "
                  f"UP_CE={up_ce_ltp:.2f}  LO_PE={lo_pe_ltp:.2f}")
        log_print(f"[IF] Net credit: {net_credit:.2f} pts")

        if net_credit < IF_MIN_CREDIT:
            log_print(f"[IF] credit {net_credit:.2f} < min {IF_MIN_CREDIT}, skipping", "WARN")
            return None

        lots = IF_LOTS
        # Down-size if VIX high
        if vix and vix > VIX_RANGE_PREFER_MAX:
            lots = max(2, IF_LOTS // 2)
            log_print(f"[IF] VIX {vix:.1f} elevated → reducing lots to {lots}", "WARN")

        position = {
            "strategy": self.NAME,
            "entry_time": datetime.now().isoformat(),
            "spot_at_entry": spot,
            "vix_at_entry": vix,
            "atm_strike": atm,
            "lots": lots,
            "legs": {
                "atm_ce": {"symbol": atm_ce_sym, "action": "SELL", "entry_price": atm_ce_ltp,
                           "strike": atm, "type": "CE"},
                "atm_pe": {"symbol": atm_pe_sym, "action": "SELL", "entry_price": atm_pe_ltp,
                           "strike": atm, "type": "PE"},
                "upper_ce": {"symbol": up_ce_sym, "action": "BUY", "entry_price": up_ce_ltp,
                             "strike": upper_strike, "type": "CE"},
                "lower_pe": {"symbol": lo_pe_sym, "action": "BUY", "entry_price": lo_pe_ltp,
                             "strike": lower_strike, "type": "PE"},
            },
            "net_credit_entry": net_credit,
            "max_profit_per_lot": net_credit * NIFTY_LOT_SIZE,
            "max_loss_per_lot": max(0, (IF_WING_DISTANCE - net_credit)) * NIFTY_LOT_SIZE,
            "profit_target_credit": net_credit * (1 - IF_PROFIT_TARGET_PCT),  # buy back at this level
            "status": "OPEN",
            "regime_info": regime_info or {},
        }

        if not PAPER_TRADE:
            # Real order placement would go here (multi-leg)
            log_print("[IF] LIVE MODE — order placement not implemented yet", "ERROR")
            return None

        log_print(f"[IF] DEPLOYED  Lots={lots}  Credit={net_credit:.2f}  "
                  f"MaxProfit/lot=₹{position['max_profit_per_lot']:.0f}", "TRADE")
        save_position_state(position)
        return position

    def current_state(self, position):
        """Return current credit-to-close + P&L."""
        symbols = [leg["symbol"] for leg in position["legs"].values()]
        ltps = get_multi_ltp(self.fyers, symbols)
        for leg in position["legs"].values():
            leg["current_ltp"] = ltps.get(leg["symbol"]) or get_ltp(self.fyers, leg["symbol"])

        atm_ce = position["legs"]["atm_ce"]["current_ltp"] or 0
        atm_pe = position["legs"]["atm_pe"]["current_ltp"] or 0
        up_ce = position["legs"]["upper_ce"]["current_ltp"] or 0
        lo_pe = position["legs"]["lower_pe"]["current_ltp"] or 0

        # Cost to close (debit if we were to flatten now)
        exit_debit = atm_ce + atm_pe - up_ce - lo_pe
        pnl_per_lot = (position["net_credit_entry"] - exit_debit) * NIFTY_LOT_SIZE
        total_pnl = pnl_per_lot * position["lots"]

        return {"exit_debit": exit_debit, "pnl_per_lot": pnl_per_lot,
                "total_pnl": total_pnl}

    def should_exit(self, position):
        # Spot-move stop loss — check before premium-based exits
        current_spot = get_ltp(self.fyers, NIFTY_SYMBOL)
        if current_spot is not None:
            spot_move = abs(current_spot - position["spot_at_entry"])
            if spot_move > IF_SPOT_MOVE_SL:
                state = self.current_state(position)
                return True, f"SPOT_MOVE_SL (moved {spot_move:.0f}pt from entry)", state

        state = self.current_state(position)
        pnl_per_lot = state["pnl_per_lot"]
        exit_debit = state["exit_debit"]

        # Profit target hit
        if exit_debit <= position["profit_target_credit"]:
            return True, f"PROFIT_TARGET (pnl/lot ₹{pnl_per_lot:.0f})", state

        # Stop-loss hit
        if pnl_per_lot <= -IF_LOSS_LIMIT_PER_LOT:
            return True, f"STOPLOSS (pnl/lot ₹{pnl_per_lot:.0f})", state

        return False, None, state

    def close(self, position):
        """Compute final P&L and mark closed."""
        state = self.current_state(position)
        position["exit_time"] = datetime.now().isoformat()
        position["net_debit_exit"] = state["exit_debit"]
        position["pnl"] = state["total_pnl"]
        position["pnl_per_lot"] = state["pnl_per_lot"]
        position["status"] = "CLOSED"
        log_print(f"[IF] CLOSED  exit_debit={state['exit_debit']:.2f}  "
                  f"PnL=₹{state['total_pnl']:.0f}", "TRADE")
        return position


# ================================================================
#  VERTICAL SPREAD BASE
# ================================================================

class _VerticalSpread:
    NAME = "VERTICAL"
    LONG_TYPE = "CE"   # Override in subclass
    SHORT_TYPE = "CE"
    DIRECTION = 1      # 1 for bull, -1 for bear

    def __init__(self, fyers):
        self.fyers = fyers

    def deploy(self, spot, vix=None, regime_info=None):
        atm = get_atm_strike(spot)
        long_strike = atm + (VS_OFFSET * self.DIRECTION)   # OTM by 50
        short_strike = atm + ((VS_OFFSET + VS_WIDTH) * self.DIRECTION)

        # For bear put, "deeper OTM" means lower strike (direction = -1)
        log_print(f"[{self.NAME}] Building. Long={long_strike}{self.LONG_TYPE}  "
                  f"Short={short_strike}{self.SHORT_TYPE}")

        chain = fetch_option_chain(self.fyers)
        if not chain:
            log_print(f"[{self.NAME}] option chain unavailable", "ERROR")
            return None

        long_sym, long_ltp = find_option_symbol(chain, long_strike, self.LONG_TYPE)
        short_sym, short_ltp = find_option_symbol(chain, short_strike, self.SHORT_TYPE)

        if not (long_sym and short_sym) or not (long_ltp and short_ltp):
            log_print(f"[{self.NAME}] symbol lookup failed", "ERROR")
            return None

        net_debit = long_ltp - short_ltp
        if net_debit <= 0:
            log_print(f"[{self.NAME}] non-positive debit {net_debit}, skipping", "WARN")
            return None

        max_profit_pts = VS_WIDTH - net_debit
        max_loss_pts = net_debit

        position = {
            "strategy": self.NAME,
            "entry_time": datetime.now().isoformat(),
            "spot_at_entry": spot,
            "vix_at_entry": vix,
            "lots": VS_LOTS,
            "legs": {
                "long_leg": {"symbol": long_sym, "action": "BUY",
                             "entry_price": long_ltp, "strike": long_strike,
                             "type": self.LONG_TYPE},
                "short_leg": {"symbol": short_sym, "action": "SELL",
                              "entry_price": short_ltp, "strike": short_strike,
                              "type": self.SHORT_TYPE},
            },
            "net_debit_entry": net_debit,
            "max_profit_per_lot": max_profit_pts * NIFTY_LOT_SIZE,
            "max_loss_per_lot": max_loss_pts * NIFTY_LOT_SIZE,
            "profit_target_value": net_debit + (max_profit_pts * VS_PROFIT_TARGET_PCT),
            "stoploss_value": net_debit * (1 - VS_STOPLOSS_PCT),
            "status": "OPEN",
            "regime_info": regime_info or {},
        }

        if not PAPER_TRADE:
            log_print(f"[{self.NAME}] LIVE MODE — not implemented", "ERROR")
            return None

        log_print(f"[{self.NAME}] DEPLOYED  Debit={net_debit:.2f}  "
                  f"MaxProfit/lot=₹{position['max_profit_per_lot']:.0f}  "
                  f"MaxLoss/lot=₹{position['max_loss_per_lot']:.0f}", "TRADE")
        save_position_state(position)
        return position

    def current_state(self, position):
        symbols = [leg["symbol"] for leg in position["legs"].values()]
        ltps = get_multi_ltp(self.fyers, symbols)
        for leg in position["legs"].values():
            leg["current_ltp"] = ltps.get(leg["symbol"]) or get_ltp(self.fyers, leg["symbol"])
        long_ltp = position["legs"]["long_leg"]["current_ltp"] or 0
        short_ltp = position["legs"]["short_leg"]["current_ltp"] or 0
        current_value = long_ltp - short_ltp
        pnl_per_lot = (current_value - position["net_debit_entry"]) * NIFTY_LOT_SIZE
        total_pnl = pnl_per_lot * position["lots"]
        return {"current_value": current_value, "pnl_per_lot": pnl_per_lot,
                "total_pnl": total_pnl}

    def should_exit(self, position):
        state = self.current_state(position)
        cv = state["current_value"]

        if cv >= position["profit_target_value"]:
            return True, f"PROFIT_TARGET (pnl ₹{state['total_pnl']:.0f})", state

        if cv <= position["stoploss_value"]:
            return True, f"STOPLOSS (pnl ₹{state['total_pnl']:.0f})", state

        return False, None, state

    def close(self, position):
        state = self.current_state(position)
        position["exit_time"] = datetime.now().isoformat()
        position["exit_value"] = state["current_value"]
        position["pnl"] = state["total_pnl"]
        position["pnl_per_lot"] = state["pnl_per_lot"]
        position["status"] = "CLOSED"
        log_print(f"[{self.NAME}] CLOSED  exit_value={state['current_value']:.2f}  "
                  f"PnL=₹{state['total_pnl']:.0f}", "TRADE")
        return position


class BullCallSpread(_VerticalSpread):
    NAME = "BULL_CALL_SPREAD"
    LONG_TYPE = "CE"
    SHORT_TYPE = "CE"
    DIRECTION = 1


class BearPutSpread(_VerticalSpread):
    NAME = "BEAR_PUT_SPREAD"
    LONG_TYPE = "PE"
    SHORT_TYPE = "PE"
    DIRECTION = -1
