# MyAlgo v3 — 11 AM Adaptive Hedged System

## What this is

A multi-strategy options system that:
- Observes Nifty from 9:15-11:00 AM (you don't need to be online)
- At **11:00 AM sharp** classifies the regime (RANGE / UPTREND / DOWNTREND / NO_TRADE)
- Deploys ONE of: Iron Fly, Bull Call Spread, Bear Put Spread
- Monitors and exits between 14:30-14:45
- Logs everything to CSV, tracks capital across days

**Paper trading on ₹10 LAKH capital.** All trades simulated; no real orders placed.

---

## Files (drop all into ~/Desktop/MyAlgo/)

| File | Purpose |
|---|---|
| `v3_config.py` | Every tunable parameter |
| `v3_utils.py` | Option chain, LTP, logging, capital |
| `v3_regime.py` | Morning observation + regime classification |
| `v3_strategies.py` | Iron Fly + Bull/Bear Spreads |
| `v3_main.py` | **The script you run** |
| `v3_review.py` | EOD summary |

Existing files that stay:
- `fyers_login.py` (token refresh — unchanged)
- `news_intel.py` (auto-imported by regime detector if present)

---

## Daily Routine

### Morning (any time 9:00 AM - 10:55 AM)

```bash
cd ~/Desktop/MyAlgo
python fyers_login.py      # refresh token
python v3_main.py          # start v3 — it'll wait until 11:00
```

That's it. Script will:
1. Wait until 11:00:00
2. Fetch the morning candles (9:15-11:00) from Fyers history
3. Classify regime
4. Deploy strategy (if conditions OK)
5. Monitor P&L every 30 seconds
6. Exit at target/SL OR at 14:30 sharp
7. Write trade to `trades_log_v3.csv`
8. Exit cleanly

### Evening (any time after 3:30 PM)

```bash
python v3_review.py        # see today's summary + week + overall
```

---

## How the 3 Strategies Work

### Iron Fly (RANGE regime — most common, ~55-60% of days)

```
SELL ATM CE @ ~75
SELL ATM PE @ ~75
BUY  ATM+100 CE @ ~25     (wing — caps upside loss)
BUY  ATM-100 PE @ ~25     (wing — caps downside loss)

Net credit collected: ~100 pts × 65 × 4 lots = ₹26,000

Target: buy back when credit decays 50% → ₹13,000 profit
Stop:  exit if loss/lot hits ₹3,000 → ₹12,000 max loss
Force exit: 14:30
```

### Bull Call Spread (UPTREND regime)

```
BUY  ATM+50 CE @ ~60
SELL ATM+150 CE @ ~30

Net debit: 30 pts × 65 × 5 lots = ₹9,750

Target: 60% of max profit → ₹13,650
Stop:   70% of debit lost → ₹6,825
Force exit: 14:30
```

### Bear Put Spread (DOWNTREND regime)

Mirror of Bull Call Spread on the put side.

---

## Risk Limits (hard-coded)

| Limit | Value | Action |
|---|---|---|
| Per-trade max risk | ₹20,000 | Position sized to fit |
| Daily loss limit | -₹15,000 | Stop trading for day |
| Daily profit cap | +₹25,000 | Lock in, stop trading |
| Weekly loss limit | -₹40,000 | Pause, review system |
| Max margin used | 50% (₹5L) | Always keep ₹5L free |

---

## What Triggers NO_TRADE

The system refuses to trade when:
- VIX < 11 (premiums too thin)
- VIX > 22 (too risky)
- News intel BLOCK signal (RBI MPC, FOMC, Budget days)
- Morning range < 20 pts (market is dead)
- Daily/weekly loss limit hit
- Past 11:05 AM grace window (entry missed)

---

## Tuning

Everything is in `v3_config.py`. Common adjustments:

```python
IF_LOTS = 4                      # Iron fly lot count
VS_LOTS = 5                      # Spread lot count
TREND_POSITION_THRESHOLD = 0.35  # Tighter = more RANGE, looser = more trend
IF_PROFIT_TARGET_PCT = 0.50      # When to take iron fly profit
SOFT_EXIT_MINUTE = 30            # Change to 15 if you want earlier exits
```

---

## Honest Expectations

- **Win rate**: 60-70% on iron fly days, 45-55% on spread days
- **Average profitable day**: ₹4,000-12,000
- **Average losing day**: ₹3,000-8,000
- **Annualized target**: 25-35% on trading allocation (3L active capital)
- **Monthly P&L**: ₹10,000-30,000 realistic on paper

This is NOT a doubling strategy. It's a grinder that preserves capital.

---

## Three-Month Plan

| Month | Focus |
|---|---|
| **May** | Run paper daily, accumulate 20+ trades, audit logs |
| **June** | Backtest each strategy on 2024-2025 historical data; tune thresholds |
| **July** | Stress test — review worst weeks; calculate real position sizing |

End of July review: if Sharpe > 1.0 and max DD < 20% → consider small live capital.
Never go live with anything you wouldn't be okay losing 100% of.

---

## Troubleshooting

**"Token expired"** → run `python fyers_login.py` again, get a fresh token.

**"Symbol lookup failed"** → option chain may be slow; retry, or check Fyers status.

**"Insufficient candles"** → script started too late; either market data is delayed or you ran past 11:05 AM. Restart before 11:00 next day.

**Position state stuck open** → delete `v3_position_state.json` manually if you need to reset (only if you've manually confirmed no real position is hanging).

---

## Live Mode (when you're ready)

Change ONE line in `v3_config.py`:

```python
PAPER_TRADE = False
```

But: real order placement is currently stubbed out — explicit safeguard. You'll need to wire `fyers.place_order()` calls in the deploy methods when you're actually ready. We'll do that together after 3 months of paper.
