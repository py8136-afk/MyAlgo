"""
daily_review.py — Run this in the evening to see how the day went.
Shows today's trades, weekly P&L, win rate, and recommendations.
"""

import pandas as pd
import datetime
import os

LOG_FILE = "trades_log.csv"


def main():
    print("\n" + "=" * 65)
    print("  DAILY REVIEW")
    print("=" * 65)

    if not os.path.exists(LOG_FILE):
        print("\n  No trades logged yet.")
        return

    try:
        df = pd.read_csv(LOG_FILE)
    except Exception as e:
        print(f"\n  Could not read log: {e}")
        return

    if df.empty:
        print("\n  Trade log is empty.")
        return

    today = datetime.date.today().isoformat()
    week_start = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()

    # Today's trades
    today_df = df[df["date"] == today]
    week_df  = df[df["date"] >= week_start]
    all_df   = df

    print(f"\n  📅 TODAY ({today})")
    print("  " + "─" * 50)
    if today_df.empty:
        print("    No trades today.")
    else:
        summarize(today_df)

    print(f"\n  📆 LAST 7 DAYS")
    print("  " + "─" * 50)
    summarize(week_df)

    print(f"\n  📈 ALL-TIME (since {all_df['date'].min()})")
    print("  " + "─" * 50)
    summarize(all_df)

    # Exit reason breakdown
    if len(all_df) >= 5:
        print(f"\n  EXIT REASON BREAKDOWN (all-time)")
        print("  " + "─" * 50)
        counts = all_df["exit_reason"].value_counts()
        for reason, count in counts.items():
            pct = count / len(all_df) * 100
            print(f"    {reason:20s} {count:3d}  ({pct:.0f}%)")

    # Recommendations
    print(f"\n  💡 RECOMMENDATIONS")
    print("  " + "─" * 50)
    if len(all_df) < 10:
        print(f"    Need more data. Current trades: {len(all_df)}")
        print(f"    Keep paper trading to build confidence.")
    else:
        win_rate = (all_df["pnl"] > 0).sum() / len(all_df) * 100
        avg_win  = all_df[all_df["pnl"] > 0]["pnl"].mean() if (all_df["pnl"] > 0).any() else 0
        avg_loss = all_df[all_df["pnl"] < 0]["pnl"].mean() if (all_df["pnl"] < 0).any() else 0

        if win_rate >= 55 and avg_win > abs(avg_loss) * 1.5:
            print(f"    ✅ Strategy performing well (WR: {win_rate:.0f}%)")
            print(f"    Consider going live after more paper data.")
        elif win_rate >= 45:
            print(f"    🟡 Mixed results (WR: {win_rate:.0f}%)")
            print(f"    Keep paper trading. Review losing trades.")
        else:
            print(f"    🔴 Poor performance (WR: {win_rate:.0f}%)")
            print(f"    Do NOT go live. Something is wrong.")

    print("\n" + "=" * 65 + "\n")


def summarize(df):
    if df.empty:
        print("    (no trades)")
        return
    total = len(df)
    wins  = (df["pnl"] > 0).sum()
    losses = (df["pnl"] < 0).sum()
    total_pnl = df["pnl"].sum()
    avg_pnl   = df["pnl"].mean()
    win_rate  = wins / total * 100 if total else 0

    status = "🟢" if total_pnl >= 0 else "🔴"
    print(f"    Trades: {total}  |  W: {wins}  L: {losses}  |  WR: {win_rate:.0f}%")
    print(f"    Total P&L: {status} ₹{total_pnl:,.0f}   |   Avg/trade: ₹{avg_pnl:,.0f}")
    if wins > 0 and losses > 0:
        avg_win  = df[df["pnl"] > 0]["pnl"].mean()
        avg_loss = df[df["pnl"] < 0]["pnl"].mean()
        print(f"    Avg win: ₹{avg_win:,.0f}  |  Avg loss: ₹{avg_loss:,.0f}")


if __name__ == "__main__":
    main()
