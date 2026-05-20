"""
backtest_v3_strategy.py
MyAlgo v3 — 11 AM Adaptive Hedged System — 5 Year Backtest
Regime classifier: same logic as v3_regime.py (unpatched)
Option pricing: Black-Scholes (synthetic, tagged SYNTH)
Run: python backtest_v3_strategy.py
"""

import os, math, time, csv
from datetime import datetime, date, timedelta
from fyers_apiv3 import fyersModel

# ── CONFIG (mirrors v3_config.py) ─────────────────────────────────
BROKER_ID    = "Z33F8IITSE-100"
TOKEN_FILE   = "token.txt"
NIFTY_SYM    = "NSE:NIFTY50-INDEX"
VIX_SYM      = "NSE:INDIAVIX-INDEX"
STRIKE_STEP  = 50
CAPITAL      = 1_000_000

# Lot size history
def lot_size(d: date):
    if d >= date(2026, 1, 15): return 65
    if d >= date(2024, 11, 20): return 25
    return 50

# Regime thresholds
TREND_POS_THRESH  = 0.35
BREAKOUT_BUFFER   = 10
MIN_RANGE         = 20
VIX_MIN, VIX_MAX  = 11, 22
VIX_TREND_MAX     = 18
VIX_RANGE_MAX     = 16
SLOPE_THRESHOLD   = 4.0   # |OLS slope| pts/bar → override RANGE to trend

# Iron Fly
IF_WING  = 100
IF_LOTS  = 4
IF_MIN_CREDIT       = 40
IF_PROFIT_TGT_PCT   = 0.25  # was 0.50 — never fired; 25% is achievable intraday
IF_LOSS_LIM_LOT     = 1000  # was 3000 — unreachable; 1000 ≈ 20 pts/contract

# Vertical Spread
VS_WIDTH   = 100
VS_OFFSET  = 50
VS_LOTS    = 5
VS_PROFIT_TGT_PCT = 0.60
VS_SL_PCT         = 0.70

# Time windows
ENTRY_H, ENTRY_M   = 11, 0
HARD_EXIT_H, HARD_EXIT_M = 14, 45
RISK_FREE = 0.065   # 6.5% annualised

# Backtest period
START = date(2021, 5, 19)
END   = date(2026, 5, 19)
CHUNK = 90

OUTPUT_CSV = "backtest_v3_results.csv"

# ── FYERS ──────────────────────────────────────────────────────────
def get_fyers():
    with open(TOKEN_FILE) as f: token = f.read().strip()
    return fyersModel.FyersModel(client_id=BROKER_ID, token=token, log_path="")

def fetch_candles(fyers, symbol, from_d: date, to_d: date, res="5"):
    payload = {
        "symbol": symbol, "resolution": res, "date_format": "1",
        "range_from": from_d.strftime("%Y-%m-%d"),
        "range_to":   to_d.strftime("%Y-%m-%d"),
        "cont_flag": "1",
    }
    try:
        r = fyers.history(data=payload)
        if r.get("s") == "ok":
            return r.get("candles", [])
    except Exception as e:
        print(f"  [ERR] {symbol} {from_d}→{to_d}: {e}")
    return []

# ── DATA LOADER ────────────────────────────────────────────────────
def load_all_data(fyers):
    """Returns {date: {"candles": [...], "vix": float}}"""
    print("  Fetching Nifty 5-min candles...")
    nifty_raw = []
    vix_raw   = []
    cur = START
    while cur < END:
        chunk_end = min(cur + timedelta(days=CHUNK), END)
        print(f"    {cur} → {chunk_end}", end=" ", flush=True)
        nc = fetch_candles(fyers, NIFTY_SYM, cur, chunk_end)
        vc = fetch_candles(fyers, VIX_SYM, cur, chunk_end)
        print(f"Nifty={len(nc)} VIX={len(vc)}")
        nifty_raw.extend(nc)
        vix_raw.extend(vc)
        cur = chunk_end + timedelta(days=1)
        time.sleep(0.4)

    # Build per-day structure
    days = {}
    for c in nifty_raw:
        ts  = datetime.fromtimestamp(c[0])
        d   = ts.date()
        if d.weekday() >= 5: continue
        days.setdefault(d, {"candles": [], "vix": None})
        days[d]["candles"].append({"ts": ts, "o": c[1], "h": c[2], "l": c[3], "c": c[4]})

    # Attach VIX — use last candle of the day as daily VIX
    vix_by_day = {}
    for c in vix_raw:
        ts = datetime.fromtimestamp(c[0])
        d  = ts.date()
        vix_by_day[d] = float(c[4])
    for d in days:
        days[d]["vix"] = vix_by_day.get(d)

    return days

# ── NEAREST THURSDAY EXPIRY ────────────────────────────────────────
def _ols_slope(values):
    n = len(values)
    if n < 2: return 0.0
    x_bar = (n - 1) / 2.0
    y_bar = sum(values) / n
    num = sum((i - x_bar) * (v - y_bar) for i, v in enumerate(values))
    den = sum((i - x_bar) ** 2 for i in range(n))
    return num / den if den else 0.0


def next_thursday(d: date):
    days_ahead = (3 - d.weekday()) % 7
    if days_ahead == 0: days_ahead = 0   # today is thursday
    return d + timedelta(days=days_ahead)

# ── BLACK-SCHOLES ──────────────────────────────────────────────────
def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def bs_price(S, K, T, r, sigma, opt="CE"):
    """Returns BS option price. T in years."""
    if T <= 0: 
        if opt == "CE": return max(S - K, 0)
        return max(K - S, 0)
    try:
        d1 = (math.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*math.sqrt(T))
        d2 = d1 - sigma*math.sqrt(T)
        if opt == "CE":
            return S*_norm_cdf(d1) - K*math.exp(-r*T)*_norm_cdf(d2)
        else:
            return K*math.exp(-r*T)*_norm_cdf(-d2) - S*_norm_cdf(-d1)
    except:
        return 0

def option_price(spot, strike, expiry: date, trade_date: date, trade_time_h, vix, opt):
    T_days = (expiry - trade_date).days + (1 - trade_time_h/24)
    T = max(T_days / 365, 0.001)
    sigma = (vix / 100) if vix else 0.15
    return bs_price(spot, strike, T, RISK_FREE, sigma, opt)

def atm(spot):
    return int(round(spot / STRIKE_STEP) * STRIKE_STEP)

# ── REGIME CLASSIFIER (same logic as v3_regime.py) ─────────────────
def classify(morning_candles, vix):
    if vix is None:             return "NO_TRADE", "VIX_UNAVAILABLE", {}
    if vix < VIX_MIN:           return "NO_TRADE", f"VIX_LOW_{vix:.1f}", {}
    if vix > VIX_MAX:           return "NO_TRADE", f"VIX_HIGH_{vix:.1f}", {}
    if len(morning_candles) < 15: return "NO_TRADE", "INSUF_CANDLES", {}

    highs  = [c["h"] for c in morning_candles]
    lows   = [c["l"] for c in morning_candles]
    closes = [c["c"] for c in morning_candles]

    mh  = max(highs)
    ml  = min(lows)
    mr  = mh - ml
    spot = closes[-1]

    if mr < MIN_RANGE: return "NO_TRADE", f"RANGE_TIGHT_{mr:.0f}", {}

    mid = (mh + ml) / 2
    pos_score = (spot - mid) / mr

    if pos_score > TREND_POS_THRESH and spot > mh - BREAKOUT_BUFFER:
        regime = "UPTREND"
    elif pos_score < -TREND_POS_THRESH and spot < ml + BREAKOUT_BUFFER:
        regime = "DOWNTREND"
    else:
        regime = "RANGE"

    slope = _ols_slope(closes)
    if regime == "RANGE" and abs(slope) >= SLOPE_THRESHOLD:
        regime = "UPTREND" if slope > 0 else "DOWNTREND"

    if regime in ("UPTREND","DOWNTREND") and vix > VIX_TREND_MAX:
        return "NO_TRADE", "TREND_VIX_HIGH", {}

    return regime, "OK", {
        "spot": spot, "morning_high": mh, "morning_low": ml,
        "morning_range": mr, "position_score": round(pos_score, 3),
        "slope": round(slope, 3)
    }

# ── STRATEGY SIMULATION ────────────────────────────────────────────
def sim_iron_fly(spot, vix, trade_date, expiry, intraday_candles):
    """Simulate Iron Fly. Returns pnl, exit_reason, exit_time."""
    strike = atm(spot)
    lots   = IF_LOTS
    ls     = lot_size(trade_date)

    # Price at entry (11:00)
    atm_ce = option_price(spot, strike,       expiry, trade_date, 11, vix, "CE")
    atm_pe = option_price(spot, strike,       expiry, trade_date, 11, vix, "PE")
    up_ce  = option_price(spot, strike+IF_WING, expiry, trade_date, 11, vix, "CE")
    lo_pe  = option_price(spot, strike-IF_WING, expiry, trade_date, 11, vix, "PE")

    credit = atm_ce + atm_pe - up_ce - lo_pe
    if credit < IF_MIN_CREDIT:
        return None, f"LOW_CREDIT_{credit:.1f}", None

    profit_target_credit = credit * (1 - IF_PROFIT_TGT_PCT)
    loss_limit = IF_LOSS_LIM_LOT   # per lot

    # Simulate intraday
    for bar in intraday_candles:
        h = bar["ts"].hour
        m = bar["ts"].minute
        s = bar["c"]
        t_h = h + m/60

        curr_ce = option_price(s, strike,        expiry, trade_date, t_h, vix, "CE")
        curr_pe = option_price(s, strike,        expiry, trade_date, t_h, vix, "PE")
        curr_uc = option_price(s, strike+IF_WING, expiry, trade_date, t_h, vix, "CE")
        curr_lp = option_price(s, strike-IF_WING, expiry, trade_date, t_h, vix, "PE")

        exit_debit = curr_ce + curr_pe - curr_uc - curr_lp
        pnl_per_lot = (credit - exit_debit) * ls
        total_pnl   = pnl_per_lot * lots

        if exit_debit <= profit_target_credit:
            return total_pnl, "PROFIT_TARGET", bar["ts"].strftime("%H:%M")
        if pnl_per_lot <= -loss_limit:
            return total_pnl, "STOPLOSS", bar["ts"].strftime("%H:%M")
        if h > HARD_EXIT_H or (h == HARD_EXIT_H and m >= HARD_EXIT_M):
            return total_pnl, "TIME_EXIT", bar["ts"].strftime("%H:%M")

    # Last bar
    if intraday_candles:
        bar = intraday_candles[-1]
        s   = bar["c"]; t_h = bar["ts"].hour + bar["ts"].minute/60
        curr_ce = option_price(s, strike,        expiry, trade_date, t_h, vix, "CE")
        curr_pe = option_price(s, strike,        expiry, trade_date, t_h, vix, "PE")
        curr_uc = option_price(s, strike+IF_WING, expiry, trade_date, t_h, vix, "CE")
        curr_lp = option_price(s, strike-IF_WING, expiry, trade_date, t_h, vix, "PE")
        exit_debit = curr_ce + curr_pe - curr_uc - curr_lp
        return (credit - exit_debit) * ls * lots, "TIME_EXIT", bar["ts"].strftime("%H:%M")
    return None, "NO_BARS", None

def sim_spread(spot, vix, trade_date, expiry, intraday_candles, direction):
    """Simulate Bull Call Spread (UPTREND) or Bear Put Spread (DOWNTREND)."""
    ls   = lot_size(trade_date)
    lots = VS_LOTS

    if direction == "UPTREND":
        long_s  = atm(spot) + VS_OFFSET
        short_s = long_s + VS_WIDTH
        opt     = "CE"
    else:
        long_s  = atm(spot) - VS_OFFSET
        short_s = long_s - VS_WIDTH
        opt     = "PE"

    long_p  = option_price(spot, long_s,  expiry, trade_date, 11, vix, opt)
    short_p = option_price(spot, short_s, expiry, trade_date, 11, vix, opt)
    debit   = long_p - short_p

    if debit <= 0: return None, "NEG_DEBIT", None

    max_profit = VS_WIDTH - debit
    profit_target_val = debit + max_profit * VS_PROFIT_TGT_PCT
    sl_val = debit * (1 - VS_SL_PCT)

    for bar in intraday_candles:
        h = bar["ts"].hour; m = bar["ts"].minute
        s = bar["c"]; t_h = h + m/60

        curr_long  = option_price(s, long_s,  expiry, trade_date, t_h, vix, opt)
        curr_short = option_price(s, short_s, expiry, trade_date, t_h, vix, opt)
        curr_val   = curr_long - curr_short
        pnl        = (curr_val - debit) * ls * lots

        if curr_val >= profit_target_val:
            return pnl, "PROFIT_TARGET", bar["ts"].strftime("%H:%M")
        if curr_val <= sl_val:
            return pnl, "STOPLOSS", bar["ts"].strftime("%H:%M")
        if h > HARD_EXIT_H or (h == HARD_EXIT_H and m >= HARD_EXIT_M):
            return pnl, "TIME_EXIT", bar["ts"].strftime("%H:%M")

    if intraday_candles:
        bar = intraday_candles[-1]
        s = bar["c"]; t_h = bar["ts"].hour + bar["ts"].minute/60
        cl  = option_price(s, long_s,  expiry, trade_date, t_h, vix, opt)
        cs  = option_price(s, short_s, expiry, trade_date, t_h, vix, opt)
        return (cl - cs - debit) * ls * lots, "TIME_EXIT", bar["ts"].strftime("%H:%M")
    return None, "NO_BARS", None

# ── MAIN BACKTEST ──────────────────────────────────────────────────
def run(days):
    trades = []
    regime_counts = {"RANGE":0,"UPTREND":0,"DOWNTREND":0,"NO_TRADE":0}

    for d in sorted(days.keys()):
        if d.weekday() >= 5: continue
        data    = days[d]
        candles = sorted(data["candles"], key=lambda x: x["ts"])
        vix     = data["vix"]

        # Split morning (9:15-11:00) and intraday (11:00-14:45)
        morning = [c for c in candles
                   if c["ts"].hour == 9 and c["ts"].minute >= 15
                   or 10 <= c["ts"].hour < 11]
        intraday = [c for c in candles
                    if c["ts"].hour > 11
                    or (c["ts"].hour == 11 and c["ts"].minute >= 0)]
        intraday = [c for c in intraday
                    if not (c["ts"].hour > HARD_EXIT_H
                    or (c["ts"].hour == HARD_EXIT_H and c["ts"].minute > HARD_EXIT_M))]

        if not morning: continue

        regime, reason, info = classify(morning, vix)
        regime_counts[regime] = regime_counts.get(regime, 0) + 1

        if regime == "NO_TRADE":
            continue

        spot   = info["spot"]
        expiry = next_thursday(d)
        if expiry == d: expiry = d  # expiry day itself

        if regime == "RANGE":
            pnl, exit_r, exit_t = sim_iron_fly(spot, vix, d, expiry, intraday)
            strategy = "IRON_FLY"
        else:
            pnl, exit_r, exit_t = sim_spread(spot, vix, d, expiry, intraday, regime)
            strategy = "BULL_CALL_SPREAD" if regime=="UPTREND" else "BEAR_PUT_SPREAD"

        if pnl is None: continue

        trades.append({
            "date":           d.isoformat(),
            "regime":         regime,
            "strategy":       strategy,
            "spot_at_entry":  round(spot, 1),
            "vix":            round(vix, 2) if vix else "",
            "morning_range":  round(info.get("morning_range",0), 1),
            "position_score": info.get("position_score",""),
            "lot_size":       lot_size(d),
            "pnl":            round(pnl, 0),
            "exit_reason":    exit_r,
            "exit_time":      exit_t or "",
            "pricing":        "SYNTH_BS",
        })

    return trades, regime_counts

# ── SUMMARY ───────────────────────────────────────────────────────
def summarise(trades, regime_counts):
    pnls   = [t["pnl"] for t in trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total  = sum(pnls)

    daily = {}
    for t in trades:
        daily.setdefault(t["date"], 0)
        daily[t["date"]] += t["pnl"]
    dv = list(daily.values())
    mean_d = sum(dv)/len(dv) if dv else 0
    std_d  = math.sqrt(sum((x-mean_d)**2 for x in dv)/(len(dv)-1)) if len(dv)>1 else 1
    sharpe = mean_d/std_d*math.sqrt(252) if std_d>0 else 0

    equity=0; peak=0; max_dd=0
    for p in pnls:
        equity+=p; peak=max(peak,equity); max_dd=max(max_dd,peak-equity)

    print("\n"+"="*58)
    print("  MYALGO V3 — 11AM ADAPTIVE HEDGED — 5 YEAR BACKTEST")
    print("="*58)
    print(f"  Period        : {trades[0]['date']} → {trades[-1]['date']}")
    print(f"  Capital       : ₹{CAPITAL:,.0f}")
    print(f"  Pricing       : Black-Scholes Synthetic (SYNTH_BS)")
    print(f"\n  REGIME DISTRIBUTION")
    total_days = sum(regime_counts.values())
    for r,c in regime_counts.items():
        print(f"    {r:<12}: {c:4d} days ({c/total_days*100:.1f}%)")
    print(f"\n  TRADE PERFORMANCE")
    print(f"  Total Trades  : {len(trades)}")
    print(f"  Win Rate      : {len(wins)/len(pnls)*100:.1f}%")
    print(f"  Total P&L     : ₹{total:+,.0f}")
    print(f"  Avg Win       : ₹{sum(wins)/len(wins):+,.0f}" if wins else "  Avg Win       : —")
    print(f"  Avg Loss      : ₹{sum(losses)/len(losses):+,.0f}" if losses else "  Avg Loss      : —")
    print(f"  Expectancy    : ₹{total/len(trades):+,.0f}/trade")
    print(f"  Max Drawdown  : ₹{max_dd:,.0f}")
    print(f"  Sharpe Ratio  : {sharpe:.2f}")
    print(f"  Return on ₹10L: {total/CAPITAL*100:+.1f}%")
    print(f"  CAGR (approx) : {((1+total/CAPITAL)**(1/5)-1)*100:.1f}%")

    # Per strategy
    for strat in ["IRON_FLY","BULL_CALL_SPREAD","BEAR_PUT_SPREAD"]:
        st = [t for t in trades if t["strategy"]==strat]
        if not st: continue
        sw = len([t for t in st if t["pnl"]>0])
        print(f"\n  {strat}")
        print(f"    Trades: {len(st)}  WR: {sw/len(st)*100:.1f}%  P&L: ₹{sum(t['pnl'] for t in st):+,.0f}")

    # Year-wise
    print(f"\n  YEAR-WISE P&L")
    years = {}
    for t in trades:
        y = t["date"][:4]
        years.setdefault(y,[]).append(t["pnl"])
    for y in sorted(years.keys()):
        yw = len([p for p in years[y] if p>0])
        print(f"    {y}: ₹{sum(years[y]):+,.0f}  ({len(years[y])} trades, {yw/len(years[y])*100:.0f}% WR)")
    print("="*58+"\n")

def save_csv(trades):
    if not trades: return
    fields = list(trades[0].keys())
    with open(OUTPUT_CSV,"w",newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(trades)
    print(f"  Saved → {OUTPUT_CSV}")

# ── RUN ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("="*58)
    print("  MyAlgo v3 Backtest — 5 Years")
    print("="*58)

    print("\n[1/3] Connecting...")
    fyers = get_fyers()
    p = fyers.get_profile()
    if p.get("s") != "ok":
        print(f"  Bad token. Run: python fyers_login.py"); exit(1)
    print(f"  Connected as {p['data']['name']}")

    print("\n[2/3] Fetching data (~5-8 min)...")
    days = load_all_data(fyers)
    print(f"  Days loaded: {len(days)}")

    print("\n[3/3] Running backtest...")
    trades, regime_counts = run(days)
    print(f"  Trades: {len(trades)}")

    summarise(trades, regime_counts)
    save_csv(trades)
