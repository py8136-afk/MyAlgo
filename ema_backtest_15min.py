"""
ema_backtest_15min.py
NIFTY 9/21 EMA CROSSOVER BACKTEST — 15-MINUTE CANDLES

Identical to ema_backtest.py except RESOLUTION = "15".
Entry at close of crossover candle (no next-bar lag).
Output: Excel workbook with two sheets — Trades + Yearly Summary.

Run:
python ema_backtest_15min.py
"""

import time
import math
import csv
from collections import defaultdict
from datetime import datetime, date, timedelta
import pytz

from fyers_apiv3 import fyersModel
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

# ───────────────── CONFIG ─────────────────

TOKEN_FILE = "token.txt"
BROKER_ID = "Z33F8IITSE-100"

SYMBOL = "NSE:NIFTY50-INDEX"
RESOLUTION = "15"

START_DATE = date(2021, 5, 19)
END_DATE = date(2026, 5, 19)

ENTRY_AFTER = (9, 45)
EXIT_AT = (15, 10)

EMA_FAST = 9
EMA_SLOW = 21

LOT_CHANGE_DATE = date(2024, 11, 20)
LOT_BEFORE = 50
LOT_AFTER = 25

OUTPUT_EXCEL = "ema_backtest_trades_15min.xlsx"

CHUNK_DAYS = 90

IST = pytz.timezone("Asia/Kolkata")

# ───────────────── FYERS ─────────────────

def get_fyers():

    with open(TOKEN_FILE) as f:
        token = f.read().strip()

    return fyersModel.FyersModel(
        client_id=BROKER_ID,
        token=token,
        log_path=""
    )

# ───────────────── FETCH DATA ─────────────────

def fetch_chunk(fyers, from_date, to_date):

    payload = {
        "symbol": SYMBOL,
        "resolution": RESOLUTION,
        "date_format": "1",
        "range_from": from_date.strftime("%Y-%m-%d"),
        "range_to": to_date.strftime("%Y-%m-%d"),
        "cont_flag": "1",
    }

    try:

        res = fyers.history(data=payload)

        if res.get("s") == "ok":
            return res.get("candles", [])

        print(f"[WARN] {res}")

    except Exception as e:
        print(f"[ERROR] {e}")

    return []

def fetch_all_candles(fyers):

    all_candles = []

    cur = START_DATE

    while cur <= END_DATE:

        chunk_end = min(
            cur + timedelta(days=CHUNK_DAYS),
            END_DATE
        )

        print(f"Fetching {cur} → {chunk_end} ...", end=" ")

        candles = fetch_chunk(fyers, cur, chunk_end)

        print(f"{len(candles)} candles")

        all_candles.extend(candles)

        cur = chunk_end + timedelta(days=1)

        time.sleep(0.3)

    return all_candles

# ───────────────── EMA ─────────────────

def compute_ema(closes, period):

    emas = [None] * len(closes)

    if len(closes) < period:
        return emas

    k = 2 / (period + 1)

    seed_idx = period - 1

    emas[seed_idx] = sum(closes[:period]) / period

    for i in range(seed_idx + 1, len(closes)):

        emas[i] = (
            closes[i] * k
            +
            emas[i - 1] * (1 - k)
        )

    return emas

# ───────────────── LOT SIZE ─────────────────

def lot_size(trade_date):

    if trade_date >= LOT_CHANGE_DATE:
        return LOT_AFTER

    return LOT_BEFORE

# ───────────────── BACKTEST ─────────────────

def run_backtest(candles):

    days = {}
    all_bars = []

    # ───── BUILD BAR STRUCTURE ─────

    for c in candles:

        ts = datetime.utcfromtimestamp(c[0]).replace(
            tzinfo=pytz.utc
        ).astimezone(IST)

        day = ts.date()

        # Skip weekends
        if day.weekday() >= 5:
            continue

        bar = {
            "ts": ts,
            "o": c[1],
            "h": c[2],
            "l": c[3],
            "c": c[4]
        }

        days.setdefault(day, []).append(bar)

        all_bars.append(bar)

    # ───── GLOBAL SORT ─────

    all_bars = sorted(
        all_bars,
        key=lambda x: x["ts"]
    )

    # ───── CONTINUOUS EMA ─────

    closes = [b["c"] for b in all_bars]

    fast_ema = compute_ema(closes, EMA_FAST)
    slow_ema = compute_ema(closes, EMA_SLOW)

    for i in range(len(all_bars)):

        all_bars[i]["fast"] = fast_ema[i]
        all_bars[i]["slow"] = slow_ema[i]

    trades = []

    # ───── DAYWISE BACKTEST ─────

    for day in sorted(days.keys()):

        bars = sorted(
            days[day],
            key=lambda x: x["ts"]
        )

        position = None
        entry_px = None
        entry_ts = None

        entry_t = IST.localize(
            datetime.combine(
                day,
                datetime.min.time()
            ).replace(
                hour=ENTRY_AFTER[0],
                minute=ENTRY_AFTER[1]
            )
        )

        exit_t = IST.localize(
            datetime.combine(
                day,
                datetime.min.time()
            ).replace(
                hour=EXIT_AT[0],
                minute=EXIT_AT[1]
            )
        )

        for i in range(1, len(bars)):

            # EMA warmup skip
            if (
                bars[i]["fast"] is None
                or bars[i]["slow"] is None
                or bars[i - 1]["fast"] is None
                or bars[i - 1]["slow"] is None
            ):
                continue

            bar = bars[i]

            bar_ts = bar["ts"]

            close = bar["c"]

            # ───── HARD EXIT ─────

            if bar_ts >= exit_t and position is not None:

                pts = (
                    (close - entry_px)
                    *
                    (1 if position == "LONG" else -1)
                )

                pnl = pts * lot_size(day)

                trades.append({

                    "date": day.isoformat(),

                    "direction": position,

                    "entry_time": entry_ts.strftime("%H:%M"),

                    "exit_time": bar_ts.strftime("%H:%M"),

                    "entry_px": round(entry_px, 2),

                    "exit_px": round(close, 2),

                    "pts": round(pts, 2),

                    "lot_size": lot_size(day),

                    "pnl": round(pnl, 2),

                    "exit_reason": "TIME_EXIT"

                })

                position = None

                break

            # ───── CROSSOVER DETECTION ─────

            cross_up = (

                bars[i - 1]["fast"]
                <
                bars[i - 1]["slow"]

                and

                bars[i]["fast"]
                >=
                bars[i]["slow"]

            )

            cross_down = (

                bars[i - 1]["fast"]
                >
                bars[i - 1]["slow"]

                and

                bars[i]["fast"]
                <=
                bars[i]["slow"]

            )

            # ───── ENTRY WINDOW ─────

            if bar_ts < entry_t:
                continue

            # ───── LONG ENTRY ─────

            if cross_up and position != "LONG":

                # Close SHORT first
                if position == "SHORT":

                    pts = entry_px - close

                    pnl = pts * lot_size(day)

                    trades.append({

                        "date": day.isoformat(),

                        "direction": "SHORT",

                        "entry_time": entry_ts.strftime("%H:%M"),

                        "exit_time": bar_ts.strftime("%H:%M"),

                        "entry_px": round(entry_px, 2),

                        "exit_px": round(close, 2),

                        "pts": round(pts, 2),

                        "lot_size": lot_size(day),

                        "pnl": round(pnl, 2),

                        "exit_reason": "SIGNAL_FLIP"

                    })

                # enter at crossover candle close
                position = "LONG"

                entry_px = close

                entry_ts = bar_ts

            # ───── SHORT ENTRY ─────

            elif cross_down and position != "SHORT":

                # Close LONG first
                if position == "LONG":

                    pts = close - entry_px

                    pnl = pts * lot_size(day)

                    trades.append({

                        "date": day.isoformat(),

                        "direction": "LONG",

                        "entry_time": entry_ts.strftime("%H:%M"),

                        "exit_time": bar_ts.strftime("%H:%M"),

                        "entry_px": round(entry_px, 2),

                        "exit_px": round(close, 2),

                        "pts": round(pts, 2),

                        "lot_size": lot_size(day),

                        "pnl": round(pnl, 2),

                        "exit_reason": "SIGNAL_FLIP"

                    })

                # enter at crossover candle close
                position = "SHORT"

                entry_px = close

                entry_ts = bar_ts

# ───────────────── SUMMARY ─────────────────
    return trades

def summarise(trades):

    if not trades:
        print("No trades found.")
        return

    pnls = [t["pnl"] for t in trades]

    wins = [p for p in pnls if p > 0]

    losses = [p for p in pnls if p <= 0]

    total_pnl = sum(pnls)

    win_rate = len(wins) / len(pnls) * 100

    # Max drawdown
    equity = 0
    peak = 0
    max_dd = 0

    for p in pnls:

        equity += p

        peak = max(peak, equity)

        dd = peak - equity

        max_dd = max(max_dd, dd)

    print("\n" + "=" * 60)

    print(" NIFTY EMA BACKTEST — 15 MIN ")

    print("=" * 60)

    print(f"Total Trades : {len(trades)}")

    print(f"Winners      : {len(wins)}")

    print(f"Losers       : {len(losses)}")

    print(f"Win Rate     : {win_rate:.2f}%")

    print(f"Total P&L    : ₹{total_pnl:,.2f}")

    print(f"Max DD       : ₹{max_dd:,.2f}")

    print("=" * 60)


# ───────────────── YEARLY STATS ─────────────────

def _year_metrics(year_trades, all_pnls_for_sharpe=None):
    """Compute all metrics for a slice of trades. Returns an ordered dict."""
    pnls   = [t["pnl"] for t in year_trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n      = len(pnls)

    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses)) if losses else 0
    total_pnl    = sum(pnls)

    # Daily P&L for Sharpe
    daily = defaultdict(float)
    for t in year_trades:
        daily[t["date"]] += t["pnl"]
    dpnls = list(daily.values())

    sharpe = 0.0
    if len(dpnls) >= 2:
        mean_d = sum(dpnls) / len(dpnls)
        var    = sum((x - mean_d) ** 2 for x in dpnls) / (len(dpnls) - 1)
        std_d  = math.sqrt(var)
        if std_d > 0:
            sharpe = mean_d / std_d * math.sqrt(252)

    # Max drawdown within this slice
    eq = 0; peak = 0; max_dd = 0
    for p in pnls:
        eq  += p
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)

    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else "—"
    calmar        = round(total_pnl / max_dd, 2) if max_dd > 0 else "—"

    return {
        "Trades":             n,
        "Winners":            len(wins),
        "Losers":             len(losses),
        "Win Rate %":         round(len(wins) / n * 100, 2) if n else 0,
        "Total P&L (₹)":     round(total_pnl, 2),
        "Avg P&L / Trade (₹)": round(total_pnl / n, 2) if n else 0,
        "Avg Winner (₹)":    round(gross_profit / len(wins), 2) if wins else 0,
        "Avg Loser (₹)":     round(sum(losses) / len(losses), 2) if losses else 0,
        "Best Trade (₹)":    round(max(pnls), 2) if pnls else 0,
        "Worst Trade (₹)":   round(min(pnls), 2) if pnls else 0,
        "Gross Profit (₹)":  round(gross_profit, 2),
        "Gross Loss (₹)":    round(gross_loss, 2),
        "Profit Factor":      profit_factor,
        "Sharpe Ratio":       round(sharpe, 2),
        "Max Drawdown (₹)":  round(max_dd, 2),
        "Calmar Ratio":       calmar,
    }


def compute_yearly_stats(trades):
    by_year = defaultdict(list)
    for t in trades:
        by_year[t["date"][:4]].append(t)

    rows = []
    for year in sorted(by_year.keys()):
        metrics = _year_metrics(by_year[year])
        rows.append({"Year": year, **metrics})

    # TOTAL row
    total_metrics = _year_metrics(trades)
    rows.append({"Year": "TOTAL", **total_metrics})

    return rows


# ───────────────── SAVE EXCEL ─────────────────

HEADER_FILL  = PatternFill("solid", fgColor="1F4E79")   # dark blue
TOTAL_FILL   = PatternFill("solid", fgColor="BDD7EE")   # light blue
HEADER_FONT  = Font(bold=True, color="FFFFFF")
TOTAL_FONT   = Font(bold=True)


def _write_sheet(ws, headers, rows, total_row_marker=None):
    """Write headers + rows to a worksheet, return the sheet."""
    ws.append(headers)
    for cell in ws[1]:
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL

    for row_data in rows:
        ws.append([row_data.get(h, "") for h in headers])

    # Bold + tint TOTAL row if present
    if total_row_marker is not None:
        for ri, row_data in enumerate(rows, 2):
            if row_data.get("Year") == total_row_marker:
                for cell in ws[ri]:
                    cell.font  = TOTAL_FONT
                    cell.fill  = TOTAL_FILL

    # Auto-width
    for col_idx, col in enumerate(ws.columns, 1):
        width = max(len(str(cell.value or "")) for cell in col) + 3
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    ws.freeze_panes = "A2"


def save_excel(trades):
    if not trades:
        return

    wb = Workbook()

    # ── Sheet 1: Trades ──
    ws_trades = wb.active
    ws_trades.title = "Trades"
    trade_headers = list(trades[0].keys())
    _write_sheet(ws_trades, trade_headers, trades)

    # ── Sheet 2: Yearly Summary ──
    ws_summ = wb.create_sheet("Yearly Summary")
    yearly  = compute_yearly_stats(trades)
    summ_headers = list(yearly[0].keys())
    _write_sheet(ws_summ, summ_headers, yearly, total_row_marker="TOTAL")

    wb.save(OUTPUT_EXCEL)
    print(f"\nExcel saved → {OUTPUT_EXCEL}  (sheets: Trades | Yearly Summary)")


# ───────────────── MAIN ─────────────────

if __name__ == "__main__":

    print("=" * 60)
    print(" NIFTY EMA BACKTEST — 15 MIN ")
    print("=" * 60)

    print("\n[1/3] Connecting to Fyers...")

    fyers = get_fyers()

    profile = fyers.get_profile()

    if profile.get("s") != "ok":

        print("Token invalid.")

        print("Run fyers_login.py")

        exit(1)

    print(f"Connected as {profile['data']['name']}")

    print("\n[2/3] Fetching candles...")

    candles = fetch_all_candles(fyers)

    print(f"Total candles fetched: {len(candles):,}")

    if len(candles) < 100:

        print("Too few candles.")

        exit(1)

    print("\n[3/3] Running backtest...")

    trades = run_backtest(candles)

    print(f"Trades simulated: {len(trades)}")

    summarise(trades)

    save_excel(trades)
