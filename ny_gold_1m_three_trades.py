"""Read-only XAUUSD 1m New York strategy backtest, up to three sequential trades/day.
Uses OANDA bid/ask M1 candles. Never places orders.
"""
import csv
import datetime as dt
import os
import requests
from collections import Counter
from zoneinfo import ZoneInfo

TOKEN = os.environ["OANDA_API_TOKEN"]
ENV = os.getenv("OANDA_ENV", "practice").lower()
HOST = "https://api-fxpractice.oanda.com" if ENV == "practice" else "https://api-fxtrade.oanda.com"
NY = ZoneInfo("America/New_York")
HEAD = {"Authorization": "Bearer " + TOKEN}
START = os.getenv("BACKTEST_START", "09:30")
END = os.getenv("BACKTEST_END", "16:00")
DAYS = int(os.getenv("BACKTEST_DAYS", "90"))
MAX_TRADES = 3
STOP_DISTANCE = 10.0
TARGET_DISTANCE = 2.0

def fetch_bars():
    end = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
    begin = end - dt.timedelta(days=DAYS)
    items = {}
    t = begin
    # Two-day slices keep each OANDA request below its 5000-candle maximum.
    while t < end:
        nxt = min(t + dt.timedelta(days=2), end)
        response = requests.get(
            HOST + "/v3/instruments/XAU_USD/candles",
            params={"from": t.isoformat(), "to": nxt.isoformat(), "granularity": "M1", "price": "BA"},
            headers=HEAD, timeout=60,
        )
        response.raise_for_status()
        for candle in response.json().get("candles", []):
            if not candle.get("complete", False):
                continue
            time = dt.datetime.fromisoformat(candle["time"].replace("Z", "+00:00"))
            bid, ask = candle["bid"], candle["ask"]
            items[time] = {
                "t": time,
                "bo": float(bid["o"]), "bh": float(bid["h"]),
                "bl": float(bid["l"]), "bc": float(bid["c"]),
                "ao": float(ask["o"]), "ah": float(ask["h"]),
                "al": float(ask["l"]), "ac": float(ask["c"]),
            }
        t = nxt
    return [items[k] for k in sorted(items)]

def mid(bar, field):
    return (bar["b" + field] + bar["a" + field]) / 2

def signal(a, b, c):
    # Three consecutively closed 1m candles: HH/HL breakout or LL/LH breakdown.
    up = (mid(a, "h") < mid(b, "h") < mid(c, "h")
          and mid(a, "l") < mid(b, "l") < mid(c, "l")
          and mid(c, "c") > mid(b, "h"))
    down = (mid(a, "h") > mid(b, "h") > mid(c, "h")
            and mid(a, "l") > mid(b, "l") > mid(c, "l")
            and mid(c, "c") < mid(b, "l"))
    return 1 if up else (-1 if down else 0)

def test_day(day, bars):
    results = []
    idx = 3
    while idx < len(bars) and len(results) < MAX_TRADES:
        current = bars[idx]
        local = current["t"].astimezone(NY)
        clock = local.strftime("%H:%M")
        if clock < START or clock >= END:
            idx += 1
            continue
        previous = bars[idx-3:idx]
        if any((bars[k]["t"]-bars[k-1]["t"]).total_seconds() != 60
               for k in range(idx-2, idx+1)):
            idx += 1
            continue
        side = signal(*previous)
        if not side:
            idx += 1
            continue
        # Assume trade enters next candle OPEN; signal uses only closed candles.
        entry = current["ao"] if side == 1 else current["bo"]
        stop = entry - STOP_DISTANCE * side
        target = entry + TARGET_DISTANCE * side
        outcome = None
        pnl = None
        exit_index = idx
        exit_time = local
        for j in range(idx, len(bars)):
            candle = bars[j]
            local_j = candle["t"].astimezone(NY)
            if local_j.strftime("%H:%M") >= END:
                break
            if j > idx and (candle["t"] - bars[j-1]["t"]).total_seconds() != 60:
                break
            # Close long at bid, short at ask. If TP and SL are both
            # inside the same minute, assume SL first (conservative).
            if side == 1:
                hit_sl = candle["bl"] <= stop
                hit_tp = candle["bh"] >= target
            else:
                hit_sl = candle["ah"] >= stop
                hit_tp = candle["al"] <= target
            if hit_sl:
                outcome = "LOSS"
                pnl = -STOP_DISTANCE
            elif hit_tp:
                outcome = "WIN"
                pnl = TARGET_DISTANCE
            if outcome:
                exit_index = j
                exit_time = local_j
                break
        if outcome is None:
            # Exit at last candle BEFORE the session boundary or a missing data gap.
            j = min(j, len(bars)-1)
            exit_index = max(idx, j-1 if bars[j]["t"].astimezone(NY).strftime("%H:%M") >= END else j)
            closebar = bars[exit_index]
            price = closebar["bc"] if side == 1 else closebar["ac"]
            pnl = (price-entry) * side
            outcome = "SESSION_OR_GAP"
            exit_time = closebar["t"].astimezone(NY)
        results.append({
            "date": str(day), "entry_time_ny": local.strftime("%H:%M"),
            "exit_time_ny": exit_time.strftime("%H:%M"),
            "side": "BUY" if side == 1 else "SELL",
            "entry": round(entry, 3), "stop": round(stop, 3),
            "target": round(target, 3), "outcome": outcome,
            "gross_price_pnl": round(pnl, 3),
        })
        idx = exit_index + 1  # non-overlapping positions; max three each day
    return results

def main():
    if not ("00:00" <= START < END <= "23:59"):
        raise ValueError("Invalid New York session range")
    bars = fetch_bars()
    days = {}
    for bar in bars:
        local = bar["t"].astimezone(NY)
        if local.weekday() < 5:
            days.setdefault(local.date(), []).append(bar)
    trades = [trade for d, bb in sorted(days.items()) for trade in test_day(d, bb)]
    fields = ["date", "entry_time_ny", "exit_time_ny", "side", "entry",
              "stop", "target", "outcome", "gross_price_pnl"]
    with open("ny_gold_1m_three_trades.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(trades)
    outcomes = Counter(t["outcome"] for t in trades)
    profit = sum(t["gross_price_pnl"] for t in trades)
    wins = outcomes["WIN"]; losses = outcomes["LOSS"]
    sessions = len({t["date"] for t in trades})
    summary = (
        f"XAUUSD 1m New York 3-trades/day backtest\\n"
        f"Days requested: {DAYS}\\nBars fetched: {len(bars)}\\n"
        f"Entry window: {START}-{END} New York\\n"
        f"Trades: {len(trades)}\\nTrading days with trades: {sessions}\\n"
        f"Wins: {wins}\\nLosses: {losses}\\n"
        f"Session/gap exits: {outcomes['SESSION_OR_GAP']}\\n"
        f"Win rate all trades: {(wins/len(trades)*100 if trades else 0):.2f}%\\n"
        f"Gross price-unit P/L: {profit:.3f}\\n"
        "Rules: SL 10, TP 2, no BE; max 3 non-overlapping trades per weekday.\\n"
        "Execution: long entry ask/exit bid; short entry bid/exit ask (OANDA BA OHLC).\\n"
        "Note: OHLC bars cannot reveal intraminute order; SL assumed first on ties.\\n"
        "Results in gold price units, not USD account P/L; excludes slippage/commission.\\n"
    ).replace("\\n", "\n")
    print(summary)
    with open("ny_gold_1m_three_trades_summary.txt", "w") as f:
        f.write(summary)

if __name__ == "__main__":
    main()
