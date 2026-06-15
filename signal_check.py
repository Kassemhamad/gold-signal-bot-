"""
Single-shot signal checker — runs once and exits.
GitHub Actions calls this every 5 minutes during NY session.
Tracks open trades in open_trades.json and sends result when TP/SL is hit.
"""
import os
import json
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

MA_PERIOD    = 1000
TRADES_FILE  = "open_trades.json"
ALERTS_FILE  = "level_alerts.json"
INSTRUMENTS = [
    {"symbol": "XAU_USD",    "name": "GOLD"  },
    {"symbol": "XAG_USD",    "name": "SILVER"},
    {"symbol": "SPX500_USD", "name": "SP500" },
    {"symbol": "US30_USD",   "name": "US30"  },
    {"symbol": "NAS100_USD", "name": "NAS100"},
    {"symbol": "EUR_USD",    "name": "EURUSD"},
    {"symbol": "GBP_USD",    "name": "GBPUSD"},
    {"symbol": "USD_JPY",    "name": "USDJPY"},
    {"symbol": "USD_CAD",    "name": "USDCAD"},
    {"symbol": "AUD_USD",    "name": "AUDUSD"},
    {"symbol": "USD_CHF",    "name": "USDCHF"},
    {"symbol": "GBP_JPY",    "name": "GBPJPY"},
    {"symbol": "EUR_JPY",    "name": "EURJPY"},
    {"symbol": "EUR_GBP",    "name": "EURGBP"},
]


# ── TRADE STATE ──────────────────────────────────────────────────────────────

def load_trades() -> dict:
    if os.path.exists(TRADES_FILE):
        with open(TRADES_FILE) as f:
            return json.load(f)
    return {}


def save_trades(trades: dict) -> None:
    with open(TRADES_FILE, "w") as f:
        json.dump(trades, f, indent=2)


def load_alerts() -> dict:
    if os.path.exists(ALERTS_FILE):
        with open(ALERTS_FILE) as f:
            return json.load(f)
    return {}


def save_alerts(alerts: dict) -> None:
    with open(ALERTS_FILE, "w") as f:
        json.dump(alerts, f, indent=2)


# ── OANDA ────────────────────────────────────────────────────────────────────

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


# ── TELEGRAM ─────────────────────────────────────────────────────────────────

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


# ── DAILY BRIEFING ───────────────────────────────────────────────────────────

def send_daily_briefing(date_str: str) -> None:
    lines = [f"📊 *Daily Levels — {date_str}*\n"]
    for inst in INSTRUMENTS:
        try:
            daily = get_candles(inst["symbol"], "D", 3)
            if daily.empty:
                continue
            prev   = daily.iloc[-1]
            levels = get_levels(prev["high"], prev["low"])
            direction = "SHORT 🔻" if prev["close"] > prev["open"] else "LONG 🔺"
            lines.append(
                f"*{inst['name']}*  {direction}\n"
                f"  50%: `{levels['mid']:.4f}`  "
                f"SL: `{levels['prev_high'] if prev['close'] > prev['open'] else levels['prev_low']:.4f}`  "
                f"TP: `{levels['prev_low'] if prev['close'] > prev['open'] else levels['prev_high']:.4f}`"
            )
        except Exception as e:
            print(f"[{inst['name']}] briefing error: {e}")

    send_telegram("\n".join(lines))


# ── CORE LOGIC ───────────────────────────────────────────────────────────────

def check_instrument(instrument: str, name: str, open_trades: dict, level_alerts: dict, now_utc: datetime) -> None:
    try:
        daily = get_candles(instrument, "D", 3)
        bars5 = get_candles(instrument, "M5", MA_PERIOD + 10)
    except Exception as e:
        print(f"[{name}] Data error: {e}"); return

    if daily.empty or bars5.empty:
        print(f"[{name}] No data"); return

    bars5["ma1000"] = bars5["close"].rolling(MA_PERIOD).mean()
    bar = bars5.iloc[-1]
    ma  = bar["ma1000"]

    # ── CHECK IF OPEN TRADE HIT TP OR SL ─────────────────────────────────────
    if name in open_trades:
        trade     = open_trades[name]
        direction = trade["direction"]
        entry     = trade["entry"]
        stop      = trade["stop"]
        target    = trade["target"]
        high      = float(bar["high"])
        low       = float(bar["low"])
        close     = float(bar["close"])

        hit        = None
        exit_price = close

        if direction == "LONG":
            if high >= target:
                hit = "WIN";  exit_price = target
            elif low <= stop:
                hit = "LOSS"; exit_price = stop
        else:
            if low <= target:
                hit = "WIN";  exit_price = target
            elif high >= stop:
                hit = "LOSS"; exit_price = stop

        if not hit and now_utc.hour >= 20:
            hit = "EOD"; exit_price = close

        if hit:
            if hit == "WIN":
                msg = f"✅ *{name} — TP HIT*"
            elif hit == "LOSS":
                msg = f"❌ *{name} — SL HIT*"
            else:
                msg = f"⏹ *{name} — SESSION CLOSED*"
            print(f"  [{name}] CLOSED {hit}  entry={entry:.4f}  exit={exit_price:.4f}")
            send_telegram(msg)
            del open_trades[name]
            return

        print(f"[{name}] Trade open  {direction}  entry={entry:.4f}  SL={stop:.4f}  TP={target:.4f}  now={close:.4f}")
        return

    # ── CHECK FOR NEW SIGNAL ──────────────────────────────────────────────────
    prev       = daily.iloc[-1]
    prev_green = prev["close"] > prev["open"]
    levels     = get_levels(prev["high"], prev["low"])
    today      = now_utc.strftime("%Y-%m-%d")

    print(
        f"[{name}] {bar['time'].strftime('%H:%M')}  "
        f"close={bar['close']:.4f}  MA={ma:.4f}  "
        f"mid={levels['mid']:.4f}  prev={'GREEN' if prev_green else 'RED'}"
    )

    if pd.isna(ma):
        print(f"[{name}] MA not ready yet"); return

    bar_low  = float(bar["low"])
    bar_high = float(bar["high"])
    mid      = levels["mid"]
    touched  = bar_low <= mid or bar_high >= mid

    # Send level alert once per market per day when price touches the 50%
    alert_key = f"{name}_{today}"
    if touched and alert_key not in level_alerts:
        direction_label = "SHORT 🔻" if prev_green else "LONG 🔺"
        send_telegram(f"⚡ *{name} — AT THE LEVEL*  {direction_label}\nWaiting for MA confirmation...")
        level_alerts[alert_key] = True
        print(f"  [{name}] Level touched — alert sent")

    signal = check_entry(
        bar_low   = bar_low,
        bar_high  = bar_high,
        bar_close = float(bar["close"]),
        ma1000    = ma,
        prev_green= prev_green,
        levels    = levels,
    )

    if signal:
        action   = "BUY" if signal["direction"] == "LONG" else "SELL"
        time_str = bar["time"].strftime("%H:%M UTC")
        msg = (
            f"🔔 *{name} — {action} ACTIVE*\n"
            f"Entry : `{signal['entry']:.4f}`\n"
            f"SL    : `{signal['stop']:.4f}`\n"
            f"TP    : `{signal['target']:.4f}`\n"
            f"Time  : {time_str}"
        )
        print(f"  SIGNAL: {action}  entry={signal['entry']:.4f}  SL={signal['stop']:.4f}  TP={signal['target']:.4f}")
        send_telegram(msg)

        open_trades[name] = {
            "symbol":    instrument,
            "direction": signal["direction"],
            "entry":     signal["entry"],
            "stop":      signal["stop"],
            "target":    signal["target"],
            "open_time": time_str,
        }
    else:
        print(f"[{name}] No signal this bar")


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main() -> None:
    now_utc = datetime.now(timezone.utc)
    print(f"[{now_utc.strftime('%Y-%m-%d %H:%M')} UTC] Checking signals...")

    if os.getenv("TEST_MODE", "false").lower() == "true":
        send_telegram(
            f"*Signal Bot — Test Ping*\n"
            f"Bot is live and watching 14 markets.\n"
            f"Time: {now_utc.strftime('%Y-%m-%d %H:%M')} UTC\n"
            f"Next live signals: Mon–Fri 13:30–20:00 UTC"
        )
        print("Test message sent."); return

    if now_utc.weekday() >= 5:
        print("Weekend — market closed"); return

    session_start = now_utc.hour > 13 or (now_utc.hour == 13 and now_utc.minute >= 30)
    session_end   = now_utc.hour >= 20
    if not session_start:
        print("Pre-session — NY opens at 13:30 UTC"); return
    if session_end:
        print("Session closed — past 20:00 UTC"); return

    # Send daily briefing on the first run of the session (13:30–13:35 UTC)
    if now_utc.hour == 13 and now_utc.minute < 36:
        date_str = now_utc.strftime("%a %d %b %Y")
        print("Sending daily briefing...")
        send_daily_briefing(date_str)

    open_trades  = load_trades()
    level_alerts = load_alerts()

    for inst in INSTRUMENTS:
        check_instrument(inst["symbol"], inst["name"], open_trades, level_alerts, now_utc)

    save_trades(open_trades)
    save_alerts(level_alerts)


if __name__ == "__main__":
    main()
