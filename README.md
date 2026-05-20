# MyAlgo v2 — NIFTY Filtered ORB

Research-backed intraday option buying system for ₹20k capital.

## The 4 files you have now

| File | What it does | When to run |
|---|---|---|
| `strategy.py` | Main algo — monitors market, enters/exits trades | Every trading day at 9:15 AM |
| `test_offline.py` | Validates all logic without needing the market | Before first use |
| `daily_review.py` | Shows today's P&L, week summary, recommendations | Every evening after market |
| `mark_withdrawn.py` | Reset the withdrawal tracker after pulling money | After each withdrawal |

## First-time setup (do this tonight)

1. Put all 4 files in your `MyAlgo` folder (same place as `fyers_login.py`)
2. Run the test suite:
   ```bash
   python test_offline.py
   ```
   Should show "ALL OFFLINE TESTS PASSED". If not, stop and paste the error.

## Every trading day

**Morning (before 9:15 AM):**
```bash
python fyers_login.py    # get fresh token
python strategy.py       # start the algo
```

**Evening (after market close):**
```bash
python daily_review.py
```

## The trading flow (what happens automatically)

```
9:15-9:45 AM  →  ORB forms (algo watches, doesn't trade yet)
9:45 AM       →  ORB locked. Algo starts looking for signal.
9:45-11:30    →  Primary entry window. Signal needs:
                   ✅ VIX between 11 and 20
                   ✅ Price breaks above ORB high OR below ORB low
                   ✅ Price on correct side of VWAP
                   ✅ LR slope confirms direction
                 All 4 must pass. If any fails → no trade.
11:30-14:15   →  Dead zone. No entries. Already-open trades still monitored.
12:30 PM      →  Force exit any open position (theta protection)
14:15-14:45   →  Secondary entry window (late-session trend edge)
15:00 PM      →  Hard exit everything. Day done.
```

## Trade management (automatic)

- **Stop loss:** -25% of premium paid
- **Target:** +50% of premium paid
- **Trail:** at +30%, SL moves to breakeven (protects profits)
- **Time stop:** 12:30 PM no matter what

## Risk guards (automatic)

- **Daily loss limit:** -₹1,500 → algo stops for the day
- **Weekly loss limit:** -₹3,000 → algo stops for the week
- **Daily profit cap:** +₹4,000 → stop, don't be greedy

## Withdrawal reminders (automatic)

When cumulative profit since last withdrawal hits your tier's threshold:

| Base capital | Profit trigger | Recommended withdraw |
|---|---|---|
| ₹20,000 | ₹3,000 | ₹1,500 |
| ₹25,000 | ₹4,000 | ₹2,000 |
| ₹30,000 | ₹5,000 | ₹2,500 |

The algo prints a loud reminder when triggered. You withdraw via Fyers app, then run:

```bash
python mark_withdrawn.py
```

Enter the amounts you withdrew and kept. The tracker resets and base capital updates.

## Plan for this week

| Day | Action |
|---|---|
| Tue | Paper trade Day 1 |
| Wed | Paper trade Day 2 |
| Thu | Paper trade Day 3 |
| Fri | Run `daily_review.py` — decide go/no-go |

If Friday's win rate ≥ 50% and total P&L ≥ 0, change `PAPER_TRADE = False` in strategy.py and go live Monday.

If not, another week of paper trading. **No shortcuts.**

## Emergency stop

If the algo misbehaves while running:
- Press `Ctrl+C` in the terminal to stop it immediately
- Go to Fyers app/web and manually square off any open positions
- Paste the error message back for me to fix

## What to send me each day

Just the output of `python daily_review.py` at end of day. I'll know from that whether to adjust anything.
