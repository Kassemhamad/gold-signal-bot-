"""
Single-shot signal checker — runs once and exits.
GitHub Actions calls this every 5 minutes during NY session.
"""
import os
import sys
import requests
import pandas as pd
from datetime import datetime, timezone

from strategy_50pct_reversal import get_levels, check_entry

# ── CREDENTIALS (from environment / GitHub Secrets) ────────────────────────
API_TOKEN    = os.getenv("OANDA_API_TOKEN", "")
INSTRUMENT   = os.getenv("OANDA_INSTRUMENT", "XAU_USD")
ENV          = os.getenv("OANDA_ENV", "practice")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BASE_URL = (
    "https://api-fxpractice.oanda.com"
    if ENV == "practice"
    else "https://api-fxtrade.oanda.com"
)
HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type":  "application/json",
}

MA_PERIOD = 1000


def get_candles(granularity: str, count: int) -> pd.DataFrame:
    url    = f"{BASE_URL}/v3/instruments/{INSTRUMENT}/candles"
    params = {"granularity": granularity, "count": count, "price": "M"}
    r = requests.get(url, headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    rows = []
    for c in r.json()["candles"]:
        if not c["complete"]:
            continue
        rows.append({
            "time":  pd.to_datetime(c["time"]),
            "open":  float(c["mid"]["o"]),
            "high":  float(c["mid"]["h"]),
            "low":   float(c["mid"]["l"]),
            "close": float(c["mid"]["c"]),
        })
    return pd.DataFrame(rows)


def send_telegram(message: str) -> None:
    if not TELEGRAM_TOKEN:
        print(f"[NO TELEGRAM] {message}")
        return
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        r = requests.post(url, data=data, timeout=10)
        print(f"Telegram: {r.status_code}")
    except Exception as e:
        print(f"Telegram error: {e}")


def main() -> None:
    now_utc = datetime.now(timezone.utc)
    print(f"[{now_utc.strftime('%Y-%m-%d %H:%M')} UTC] Checking signal...")

    # On manual runs: send a fake signal to test Telegram end-to-end
    if os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch":
        send_telegram(
            f"*XAUUSD SIGNAL* (TEST)\n"
            f"Action : *BUY*\n"
            f"Entry  : `3250.00`\n"
            f"Stop   : `3200.00`\n"
            f"Target : `3300.00`\n"
            f"Risk   : `50.0 pts`\n"
            f"Time   : {now_utc.strftime('%H:%M UTC')}"
        )
        print("Test signal sent to Telegram")
        return

    # Weekend check
    if now_utc.weekday() >= 5:
        print("Weekend — market closed"); return

    # Session window: 13:30 – 20:00 UTC
    session_start = now_utc.hour > 13 or (now_utc.hour == 13 and now_utc.minute >= 30)
    session_end   = now_utc.hour >= 20
    if not session_start:
        print("Pre-session — NY opens at 13:30 UTC"); return
    if session_end:
        print("Session closed — past 20:00 UTC"); return

    # Fetch data
    try:
        daily = get_candles("D", 3)
        bars5 = get_candles("M5", MA_PERIOD + 10)
    except Exception as e:
        print(f"Data fetch error: {e}"); sys.exit(1)

    if daily.empty or bars5.empty:
        print("No data returned"); return

    bars5["ma1000"] = bars5["close"].rolling(MA_PERIOD).mean()

    prev       = daily.iloc[-1]
    prev_green = prev["close"] > prev["open"]
    levels     = get_levels(prev["high"], prev["low"])

    bar = bars5.iloc[-1]
    ma  = bar["ma1000"]

    print(
        f"Bar {bar['time'].strftime('%H:%M')}  "
        f"close={bar['close']:.2f}  MA={ma:.2f}  "
        f"mid={levels['mid']:.2f}  prev={'GREEN' if prev_green else 'RED'}"
    )

    if pd.isna(ma):
        print("MA not ready yet"); return

    signal = check_entry(
        bar_low   = bar["low"],
        bar_high  = bar["high"],
        bar_close = bar["close"],
        ma1000    = ma,
        prev_green= prev_green,
        levels    = levels,
    )

    if signal:
        action = "BUY" if signal["direction"] == "LONG" else "SELL"
        msg = (
            f"*XAUUSD SIGNAL*\n"
            f"Action : *{action}*\n"
            f"Entry  : `{signal['entry']:.2f}`\n"
            f"Stop   : `{signal['stop']:.2f}`\n"
            f"Target : `{signal['target']:.2f}`\n"
            f"Risk   : `{signal['risk']:.1f} pts`\n"
            f"Time   : {bar['time'].strftime('%H:%M UTC')}"
        )
        print(f"\n{'='*45}")
        print(f"  SIGNAL: {action}  entry={signal['entry']:.2f}  SL={signal['stop']:.2f}  TP={signal['target']:.2f}")
        print(f"{'='*45}\n")
        send_telegram(msg)
    else:
        print("No signal this bar")


if __name__ == "__main__":
    main()
