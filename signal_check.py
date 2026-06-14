"""
Single-shot signal checker — runs once and exits.
GitHub Actions calls this every 5 minutes during NY session.
Checks both XAU_USD (Gold) and NAS100_USD (Nasdaq).
"""
import os
import sys
import requests
import pandas as pd
from datetime import datetime, timezone

from strategy_50pct_reversal import get_levels, check_entry

# ── CREDENTIALS ────────────────────────────────────────────────────────────
API_TOKEN        = os.getenv("OANDA_API_TOKEN", "")
ENV              = os.getenv("OANDA_ENV", "practice")
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

MA_PERIOD   = 1000
INSTRUMENTS = [
    {"symbol": "XAU_USD",    "name": "GOLD"  },
    {"symbol": "XAG_USD",    "name": "SILVER"},
    {"symbol": "SPX500_USD", "name": "SP500" },
    {"symbol": "US30_USD",   "name": "US30"  },
    {"symbol": "NAS100_USD", "name": "NAS100"},
]


def get_candles(instrument: str, granularity: str, count: int) -> pd.DataFrame:
    url    = f"{BASE_URL}/v3/instruments/{instrument}/candles"
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


def check_instrument(instrument: str, name: str) -> None:
    try:
        daily = get_candles(instrument, "D", 3)
        bars5 = get_candles(instrument, "M5", MA_PERIOD + 10)
    except Exception as e:
        print(f"[{name}] Data error: {e}"); return

    if daily.empty or bars5.empty:
        print(f"[{name}] No data"); return

    bars5["ma1000"] = bars5["close"].rolling(MA_PERIOD).mean()

    prev       = daily.iloc[-1]
    prev_green = prev["close"] > prev["open"]
    levels     = get_levels(prev["high"], prev["low"])

    bar = bars5.iloc[-1]
    ma  = bar["ma1000"]

    print(
        f"[{name}] {bar['time'].strftime('%H:%M')}  "
        f"close={bar['close']:.2f}  MA={ma:.2f}  "
        f"mid={levels['mid']:.2f}  prev={'GREEN' if prev_green else 'RED'}"
    )

    if pd.isna(ma):
        print(f"[{name}] MA not ready yet"); return

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
            f"*{name} SIGNAL*\n"
            f"Action : *{action}*\n"
            f"Entry  : `{signal['entry']:.2f}`\n"
            f"Stop   : `{signal['stop']:.2f}`\n"
            f"Target : `{signal['target']:.2f}`\n"
            f"Risk   : `{signal['risk']:.1f} pts`\n"
            f"Time   : {bar['time'].strftime('%H:%M UTC')}"
        )
        print(f"  SIGNAL: {action}  entry={signal['entry']:.2f}  SL={signal['stop']:.2f}  TP={signal['target']:.2f}")
        send_telegram(msg)
    else:
        print(f"[{name}] No signal this bar")


def main() -> None:
    now_utc = datetime.now(timezone.utc)
    print(f"[{now_utc.strftime('%Y-%m-%d %H:%M')} UTC] Checking signals...")

    if now_utc.weekday() >= 5:
        print("Weekend — market closed"); return

    session_start = now_utc.hour > 13 or (now_utc.hour == 13 and now_utc.minute >= 30)
    session_end   = now_utc.hour >= 20
    if not session_start:
        print("Pre-session — NY opens at 13:30 UTC"); return
    if session_end:
        print("Session closed — past 20:00 UTC"); return

    for inst in INSTRUMENTS:
        check_instrument(inst["symbol"], inst["name"])


if __name__ == "__main__":
    main()
