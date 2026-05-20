"""
v3_config.py
Central configuration for MyAlgo v3 — 11 AM Adaptive Hedged System
All tunable parameters live here. Change here, not in strategy files.
"""

# ============ CAPITAL & MODE ============
CAPITAL = 1000000           # ₹10,00,000 paper capital
PAPER_TRADE = True          # ALWAYS True until live confirmed
BROKER_ID = "Z33F8IITSE-100"

# ============ INSTRUMENT ============
NIFTY_LOT_SIZE = 65         # Post Jan 2026 SEBI change
NIFTY_SYMBOL = "NSE:NIFTY50-INDEX"
VIX_SYMBOL = "NSE:INDIAVIX-INDEX"
STRIKE_STEP = 50            # Nifty strikes in steps of 50

# ============ TIME WINDOWS (IST) ============
ENTRY_HOUR = 11             # Sharp 11:00 entry
ENTRY_MINUTE = 0
ENTRY_GRACE_MINUTES = 5     # If we boot up at 11:03, still allow entry
SOFT_EXIT_HOUR = 14
SOFT_EXIT_MINUTE = 30       # Start exiting at 14:30
HARD_EXIT_HOUR = 14
HARD_EXIT_MINUTE = 45       # Force flat by 14:45
MONITOR_INTERVAL_SEC = 30   # Check positions every 30s

# ============ REGIME DETECTION ============
MORNING_START = "09:15"
MORNING_END = "11:00"
TREND_POSITION_THRESHOLD = 0.35     # |position_score| > this = trend
BREAKOUT_BUFFER = 10                # spot must be within this of high/low for trend
MIN_MORNING_RANGE = 20              # If range < 20pts, market is dead, skip

# Slope patch (Stage 2b): override RANGE→trend if morning grind is strong
# Threshold of 4 pts/bar derived from backtest: catches 13/40 bad days
# without over-filtering profitable RANGE days
SLOPE_THRESHOLD = 4.0               # pts per 5-min bar — set 0 to disable

# ============ VIX GATES ============
VIX_MIN = 11                # Below this, premium too thin for selling
VIX_MAX = 22                # Above this, too risky
VIX_RANGE_PREFER_MAX = 16   # Best for iron fly
VIX_TREND_MAX = 18          # Above this, even spreads get risky

# ============ IRON FLY (Mode 1) ============
IF_WING_DISTANCE = 100      # OTM wings 100 pts from ATM
IF_LOTS = 4                 # 4 lots = 260 contracts
IF_MIN_CREDIT = 40          # Don't deploy if credit < 40 pts (not worth it)
IF_PROFIT_TARGET_PCT = 0.25 # Take profit when credit decays 25% (tightened from 50%)
IF_LOSS_LIMIT_PER_LOT = 3000  # Cut loss at ₹3000/lot
IF_SPOT_MOVE_SL = 80        # Exit Iron Fly if Nifty moves >80 pts from entry spot

# ============ VERTICAL SPREADS (Modes 2 & 3) ============
VS_WIDTH = 100              # 100-pt wide spread
VS_OFFSET = 50              # Long leg this far from spot (OTM by 50)
VS_LOTS = 5
VS_PROFIT_TARGET_PCT = 0.60 # Exit at 60% of max profit
VS_STOPLOSS_PCT = 0.70      # Exit if 70% of debit lost

# ============ PORTFOLIO RISK ============
MAX_RISK_PER_TRADE = 20000       # 2% of 10L
DAILY_LOSS_LIMIT = -15000        # Stop trading for the day
WEEKLY_LOSS_LIMIT = -40000       # Pause system, review
DAILY_PROFIT_CAP = 25000         # Stop trading, lock in
MAX_MARGIN_DEPLOYED_PCT = 0.50   # Never use more than 50% capital

# ============ FILES ============
TOKEN_FILE = "token.txt"
CAPITAL_FILE = "capital_v3.json"
TRADES_LOG = "trades_log_v3.csv"
POSITION_STATE = "v3_position_state.json"
DAILY_LOG_DIR = "v3_logs"