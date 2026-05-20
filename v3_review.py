"""
v3_review.py
End-of-day review for v3 trades.
Run this any evening:  python v3_review.py
"""

import csv
import os
import json
from datetime import datetime, timedelta
from v3_config import TRADES_LOG, CAPITAL_FILE, CAPITAL


def load_trades():
    if not os.path.exists(TRADES_LOG):
        return []
    with open(TRADES_LOG, "r") as f:
        return list(csv.DictReader(f))


def fmt_money(x):
    try:
        return f"₹{float(x):+,.0f}"
    except (ValueError, TypeError):
        return "—"


def summary():
    trades = load_trades()
    if not trades:
        print("No trades logged yet.")
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    today = [t for t in trades if t.get("date") == today_str]

    week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y-%m-%d")
    week = [t for t in trades if t.get("date", "") >= week_start]

    print("\n" + "=" * 60)
    print(" MyAlgo v3 — Daily Review ")
    print("=" * 60)

    # Today
    print(f"\n📅 TODAY ({today_str})")
    if not today:
        print("  No trades today.")
    else:
        for t in today:
            pnl = float(t.get("pnl") or 0)
            mark = "✅" if pnl > 0 else "❌" if pnl < 0 else "➖"
            print(f"  {mark} {t.get('strategy'):<20s} "
                  f"{t.get('regime'):<10s}  "
                  f"PnL: {fmt_money(t.get('pnl'))}  "
                  f"Reason: {t.get('exit_reason')}")
        today_pnl = sum(float(t.get("pnl") or 0) for t in today)
        print(f"\n  → Today total: {fmt_money(today_pnl)}")

    # Week
    print(f"\n📊 THIS WEEK ({week_start} onwards)")
    if not week:
        print("  No trades this week.")
    else:
        wins = sum(1 for t in week if float(t.get("pnl") or 0) > 0)
        losses = sum(1 for t in week if float(t.get("pnl") or 0) < 0)
        week_pnl = sum(float(t.get("pnl") or 0) for t in week)
        wr = (wins / max(1, wins + losses)) * 100
        print(f"  Trades: {len(week)}  Wins: {wins}  Losses: {losses}  Win Rate: {wr:.1f}%")
        print(f"  Week P&L: {fmt_money(week_pnl)}")

    # Overall
    if os.path.exists(CAPITAL_FILE):
        with open(CAPITAL_FILE, "r") as f:
            cap = json.load(f)
        print(f"\n💰 CAPITAL")
        print(f"  Starting:  ₹{cap['starting_capital']:,.0f}")
        print(f"  Current:   ₹{cap['current_capital']:,.0f}")
        print(f"  Total P&L: {fmt_money(cap['total_pnl'])}")
        roi = (cap["total_pnl"] / cap["starting_capital"]) * 100
        print(f"  ROI:       {roi:+.2f}%")
        print(f"  Trades:    {cap['trades_count']}  "
              f"Wins: {cap['winning_trades']}  Losses: {cap['losing_trades']}")
        if cap["trades_count"] > 0:
            wr_all = (cap["winning_trades"] / cap["trades_count"]) * 100
            print(f"  Win Rate:  {wr_all:.1f}%")

    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    summary()
