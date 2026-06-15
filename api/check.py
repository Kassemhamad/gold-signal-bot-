"""
Vercel serverless function — signal checker.
Called every 5 minutes by cron-job.org.
Uses aiohttp for concurrent OANDA requests (fast, fits in 10s timeout).
Uses Upstash Redis for open_trades state.
"""
import os, json, asyncio
import aiohttp
import pandas as pd
import requests as req_sync
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

from strategy_50pct_reversal import get_levels, check_entry

# ── CREDENTIALS ──────────────────────────────────────────────────────────────
OANDA_TOKEN      = os.getenv("OANDA_API_TOKEN", "")
OANDA_ENV        = os.getenv("OANDA_ENV", "practice")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
UPSTASH_URL      = os.getenv("UPSTASH_REDIS_REST_URL", "")
UPSTASH_TOKEN    = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

BASE_URL = (
    "https://api-fxpractice.oanda.com"
    if OANDA_ENV == "practice"
    else "https://api-fxtrade.oanda.com"
)
MA_PERIOD = 1000

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


# ── STATE (Upstash Redis) ─────────────────────────────────────────────────────

def load_trades() -> dict:
    if not UPSTASH_URL:
        return {}
    try:
        r = req_sync.get(
            f"{UPSTASH_URL}/get/open_trades",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            timeout=5
        )
        result = r.json().get("result")
        return json.loads(result) if result else {}
    except:
        return {}


def save_trades(trades: dict) -> None:
    if not UPSTASH_URL:
        return
    try:
        req_sync.post(
            f"{UPSTASH_URL}/set/open_trades",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            json={"value": json.dumps(trades)},
            timeout=5
        )
    except:
        pass


# ── TELEGRAM ─────────────────────────────────────────────────────────────────

def send_telegram(message: str) -> None:
    if not TELEGRAM_TOKEN:
        print(f"[NO TELEGRAM] {message}")
        return
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        req_sync.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")


# ── ASYNC OANDA FETCH ─────────────────────────────────────────────────────────

async def fetch_candles(session: aiohttp.ClientSession, instrument: str, granularity: str, count: int) -> pd.DataFrame:
    url     = f"{BASE_URL}/v3/instruments/{instrument}/candles"
    params  = {"granularity": granularity, "count": count, "price": "M"}
    headers = {"Authorization": f"Bearer {OANDA_TOKEN}"}
    try:
        async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r:
            data = await r.json()
            rows = []
            for c in data.get("candles", []):
                if not c["complete"]: continue
                rows.append({
                    "time":  pd.to_datetime(c["time"]),
                    "open":  float(c["mid"]["o"]),
                    "high":  float(c["mid"]["h"]),
                    "low":   float(c["mid"]["l"]),
                    "close": float(c["mid"]["c"]),
                })
            return pd.DataFrame(rows)
    except:
        return pd.DataFrame()


async def fetch_all_data():
    async with aiohttp.ClientSession() as session:
        tasks = []
        for inst in INSTRUMENTS:
            tasks.append(fetch_candles(session, inst["symbol"], "D",  3))
            tasks.append(fetch_candles(session, inst["symbol"], "M5", MA_PERIOD + 10))
            tasks.append(fetch_candles(session, inst["symbol"], "M1", 2))
        return await asyncio.gather(*tasks)


# ── SIGNAL LOGIC ─────────────────────────────────────────────────────────────

def process_instrument(name: str, daily: pd.DataFrame, bars5: pd.DataFrame, bars1: pd.DataFrame,
                       open_trades: dict, now_utc: datetime) -> None:
    if daily.empty or bars5.empty or bars1.empty:
        return

    bars5["ma1000"] = bars5["close"].rolling(MA_PERIOD).mean()
    ma  = float(bars5.iloc[-1]["ma1000"])
    bar = bars1.iloc[-1]

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
            if high >= target: hit = "WIN";  exit_price = target
            elif low <= stop:  hit = "LOSS"; exit_price = stop
        else:
            if low <= target:  hit = "WIN";  exit_price = target
            elif high >= stop: hit = "LOSS"; exit_price = stop

        if not hit and now_utc.hour >= 20:
            hit = "EOD"; exit_price = close

        if hit:
            if hit == "WIN":   msg = f"✅ *{name} — TP HIT*"
            elif hit == "LOSS": msg = f"❌ *{name} — SL HIT*"
            else:               msg = f"⏹ *{name} — SESSION CLOSED*"
            send_telegram(msg)
            del open_trades[name]
        return

    prev       = daily.iloc[-1]
    prev_green = prev["close"] > prev["open"]
    levels     = get_levels(prev["high"], prev["low"])

    if pd.isna(ma):
        return

    signal = None
    for _, m1_bar in bars1.iterrows():
        signal = check_entry(
            bar_low   = float(m1_bar["low"]),
            bar_high  = float(m1_bar["high"]),
            bar_close = float(m1_bar["close"]),
            ma1000    = ma,
            prev_green= prev_green,
            levels    = levels,
        )
        if signal:
            bar = m1_bar
            break

    if signal:
        action   = "BUY" if signal["direction"] == "LONG" else "SELL"
        time_str = pd.to_datetime(bar["time"]).strftime("%H:%M UTC")
        msg = (
            f"🔔 *{name} — {action}*\n"
            f"Entry : `{signal['entry']:.4f}`\n"
            f"SL    : `{signal['stop']:.4f}`\n"
            f"TP    : `{signal['target']:.4f}`\n"
            f"Time  : {time_str}"
        )
        send_telegram(msg)
        open_trades[name] = {
            "symbol":    name,
            "direction": signal["direction"],
            "entry":     signal["entry"],
            "stop":      signal["stop"],
            "target":    signal["target"],
            "open_time": time_str,
        }


# ── MAIN ─────────────────────────────────────────────────────────────────────

async def run():
    now_utc = datetime.now(timezone.utc)
    print(f"[{now_utc.strftime('%Y-%m-%d %H:%M')} UTC] Checking signals...")

    if now_utc.weekday() >= 5:
        return "weekend"

    session_start = now_utc.hour > 13 or (now_utc.hour == 13 and now_utc.minute >= 30)
    if not session_start:
        return "pre-session"
    if now_utc.hour >= 20:
        return "session-closed"

    all_data    = await fetch_all_data()
    open_trades = load_trades()

    for i, inst in enumerate(INSTRUMENTS):
        daily = all_data[i * 3]
        bars5 = all_data[i * 3 + 1]
        bars1 = all_data[i * 3 + 2]
        process_instrument(inst["name"], daily, bars5, bars1, open_trades, now_utc)

    save_trades(open_trades)
    return "ok"


# ── VERCEL HANDLER ────────────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        result = asyncio.run(run())
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": result}).encode())

    def log_message(self, format, *args):
        pass
