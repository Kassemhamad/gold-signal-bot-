"""
Real-time signal bot — OANDA streaming API.
Fires Telegram the instant price touches the entry level.
Checks MA1000 immediately, sends alert if all rules pass.
Monitors open trades for TP/SL in real time.
"""
import os, json, time, threading, requests
import pandas as pd
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
BEIRUT = ZoneInfo("Asia/Beirut")
from dotenv import load_dotenv

load_dotenv()

OANDA_TOKEN   = os.getenv("OANDA_API_TOKEN", "")
OANDA_ACCOUNT = os.getenv("OANDA_ACCOUNT_ID", "")
OANDA_ENV     = os.getenv("OANDA_ENV", "practice")
TG_TOKEN      = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT       = os.getenv("TELEGRAM_CHAT_ID", "")

BASE_URL   = "https://api-fxpractice.oanda.com"    if OANDA_ENV == "practice" else "https://api-fxtrade.oanda.com"
STREAM_URL = "https://stream-fxpractice.oanda.com" if OANDA_ENV == "practice" else "https://stream-fxtrade.oanda.com"
HEADERS    = {"Authorization": f"Bearer {OANDA_TOKEN}"}
MA_PERIOD  = 1000

INSTRUMENTS = [
    {"symbol": "XAU_USD",    "name": "GOLD"  },
    {"symbol": "WTICO_USD",  "name": "WTI"   },
    {"symbol": "DE30_EUR",   "name": "DE40",  "session_end": (16, 30)},
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

NAME_MAP = {i["symbol"]: i["name"] for i in INSTRUMENTS}
SYMBOLS  = [i["symbol"] for i in INSTRUMENTS]

# ── Per-instrument state ──────────────────────────────────────────────────────
# levels[symbol] = {mid, prev_high, prev_low, prev_green, ma1000}
levels: dict      = {}
open_trades: dict = {}   # symbol -> {direction, entry, target, stop}
signaled: set     = set()  # symbols that already fired today


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(msg: str) -> None:
    if not TG_TOKEN:
        print(f"[TELEGRAM] {msg}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"Telegram error: {e}")


# ── Fetch daily levels + MA1000 for all instruments ───────────────────────────

def fetch_levels_and_ma() -> None:
    print(f"[{_now()}] Fetching daily levels + MA1000 for all instruments...")
    for inst in INSTRUMENTS:
        sym  = inst["symbol"]
        name = inst["name"]
        try:
            # Previous daily candle
            r = requests.get(f"{BASE_URL}/v3/instruments/{sym}/candles",
                             headers=HEADERS,
                             params={"granularity": "D", "count": 3, "price": "M"},
                             timeout=10)
            candles = [c for c in r.json().get("candles", []) if c.get("complete", True)]
            if len(candles) < 1:
                continue
            prev = candles[-1]
            ph   = float(prev["mid"]["h"])
            pl   = float(prev["mid"]["l"])
            mid  = (ph + pl) / 2
            prev_green = float(prev["mid"]["c"]) > float(prev["mid"]["o"])

            # MA1000 from M5 bars
            r2 = requests.get(f"{BASE_URL}/v3/instruments/{sym}/candles",
                              headers=HEADERS,
                              params={"granularity": "M5", "count": MA_PERIOD + 10, "price": "M"},
                              timeout=15)
            m5 = [float(c["mid"]["c"]) for c in r2.json().get("candles", [])
                  if c.get("complete", True)]
            ma = sum(m5[-MA_PERIOD:]) / MA_PERIOD if len(m5) >= MA_PERIOD else None

            levels[sym] = {
                "mid":         mid,
                "prev_high":   ph,
                "prev_low":    pl,
                "prev_green":  prev_green,
                "ma1000":      ma,
                "session_end": inst.get("session_end", (20, 0)),
            }
            color = "GREEN" if prev_green else "RED"
            print(f"  {name:8s}  mid={mid:.5f}  MA={ma:.5f if ma else 'N/A'}  prev={color}")

        except Exception as e:
            print(f"  {name}: fetch error — {e}")

    print(f"[{_now()}] Levels ready. Streaming started.\n")


def refresh_ma() -> None:
    """Refresh MA1000 every 5 minutes in background."""
    for inst in INSTRUMENTS:
        sym = inst["symbol"]
        if sym not in levels:
            continue
        try:
            r = requests.get(f"{BASE_URL}/v3/instruments/{sym}/candles",
                             headers=HEADERS,
                             params={"granularity": "M5", "count": MA_PERIOD + 10, "price": "M"},
                             timeout=15)
            m5 = [float(c["mid"]["c"]) for c in r.json().get("candles", [])
                  if c.get("complete", True)]
            if len(m5) >= MA_PERIOD:
                levels[sym]["ma1000"] = sum(m5[-MA_PERIOD:]) / MA_PERIOD
        except:
            pass


def ma_refresh_loop() -> None:
    while True:
        time.sleep(300)   # every 5 minutes
        refresh_ma()


# ── Session helpers ───────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(BEIRUT).strftime("%H:%M:%S")


def in_ny_session() -> bool:
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 13 * 60 + 30 <= minutes < 20 * 60


def session_closed() -> bool:
    now = datetime.now(timezone.utc)
    return now.hour >= 20


# ── Tick processor ────────────────────────────────────────────────────────────

def on_tick(sym: str, bid: float, ask: float) -> None:
    if sym not in levels:
        return

    lv   = levels[sym]
    name = NAME_MAP.get(sym, sym)

    now_utc = datetime.now(timezone.utc)
    end_h, end_m = lv.get("session_end", (20, 0))
    past_end = now_utc.hour > end_h or (now_utc.hour == end_h and now_utc.minute >= end_m)

    # ── Check open trade for TP/SL or instrument EOD ─────────────────────────
    if sym in open_trades:
        trade = open_trades[sym]
        hit   = None

        if trade["direction"] == "LONG":
            if ask >= trade["target"]:  hit = "WIN"
            elif bid <= trade["stop"]:  hit = "LOSS"
        else:
            if bid <= trade["target"]:  hit = "WIN"
            elif ask >= trade["stop"]:  hit = "LOSS"

        if not hit and past_end:
            hit = "EOD"

        if hit == "WIN":
            send_telegram(
                f"✅ *{name} — TP HIT*\n"
                f"Entry `{trade['entry']:.5f}` → TP `{trade['target']:.5f}`"
            )
            print(f"[{_now()}] {name} TP HIT")
            del open_trades[sym]

        elif hit == "LOSS":
            send_telegram(
                f"❌ *{name} — SL HIT*\n"
                f"Entry `{trade['entry']:.5f}` → SL `{trade['stop']:.5f}`"
            )
            print(f"[{_now()}] {name} SL HIT")
            del open_trades[sym]

        elif hit == "EOD":
            send_telegram(f"⏹ *{name} — SESSION CLOSED*\nNo TP/SL hit by {end_h:02d}:{end_m:02d} UTC")
            print(f"[{_now()}] {name} SESSION CLOSED (EOD)")
            del open_trades[sym]
        return

    # ── Check for new entry signal ────────────────────────────────────────────
    if sym in signaled:
        return
    if past_end:
        return
    if not in_ny_session():
        return

    mid        = lv["mid"]
    prev_green = lv["prev_green"]
    ma         = lv["ma1000"]

    if ma is None:
        return

    # GREEN prev day -> LONG: price HIGH reaches mid AND close (ask) > MA
    # RED   prev day -> SHORT: price LOW reaches mid AND close (bid) < MA
    if prev_green:
        touched = ask >= mid
        ok      = ask > ma
        direction = "LONG"
        target    = lv["prev_high"]
        stop      = lv["prev_low"]
        action    = "BUY"
    else:
        touched = bid <= mid
        ok      = bid < ma
        direction = "SHORT"
        target    = lv["prev_low"]
        stop      = lv["prev_high"]
        action    = "SELL"

    if touched and ok:
        signaled.add(sym)
        open_trades[sym] = {
            "direction": direction,
            "entry":     mid,
            "target":    target,
            "stop":      stop,
        }
        prev_color = "GREEN" if prev_green else "RED"
        rule_line  = (
            f"Prev {prev_color} → high≥mid, close>MA"
            if direction == "LONG"
            else f"Prev {prev_color} → low≤mid, close<MA"
        )
        send_telegram(
            f"🔔 *{name} — {action}*\n"
            f"Entry : `{mid:.5f}`\n"
            f"SL    : `{stop:.5f}`\n"
            f"TP    : `{target:.5f}`\n"
            f"MA1000: `{ma:.5f}`\n"
            f"Rule  : {rule_line}\n"
            f"Time  : {_now()} Beirut"
        )
        print(f"[{_now()}] SIGNAL {name} {action}  mid={mid:.5f}  MA={ma:.5f}")


# ── Session-close cleanup ─────────────────────────────────────────────────────

session_close_sent = False

def handle_session_close() -> None:
    global session_close_sent
    if session_close_sent:
        return
    session_close_sent = True

    for sym, trade in list(open_trades.items()):
        name = NAME_MAP.get(sym, sym)
        send_telegram(f"⏹ *{name} — SESSION CLOSED*\nNo TP/SL hit by 20:00 UTC")
        del open_trades[sym]

    send_telegram(
        f"🌙 *Session closed — that's a wrap!*\n\n"
        f"NY session done for today.\nSee you tomorrow at 13:30 UTC 🚀"
    )
    print(f"[{_now()}] Session closed.")


# ── Main stream loop ──────────────────────────────────────────────────────────

def stream() -> None:
    global session_close_sent
    url    = f"{STREAM_URL}/v3/accounts/{OANDA_ACCOUNT}/pricing/stream"
    params = {"instruments": ",".join(SYMBOLS)}

    while True:
        try:
            print(f"[{_now()}] Connecting to OANDA stream...")
            with requests.get(url, headers=HEADERS, params=params,
                              stream=True, timeout=30) as resp:
                print(f"[{_now()}] Stream connected (status {resp.status_code})")
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except:
                        continue

                    if msg.get("type") != "PRICE":
                        continue

                    sym = msg.get("instrument")
                    if sym not in NAME_MAP:
                        continue

                    bids = msg.get("bids", [])
                    asks = msg.get("asks", [])
                    if not bids or not asks:
                        continue

                    bid = float(bids[0]["price"])
                    ask = float(asks[0]["price"])

                    # Session close check
                    if session_closed():
                        handle_session_close()
                        continue

                    on_tick(sym, bid, ask)

        except Exception as e:
            print(f"[{_now()}] Stream error: {e} — reconnecting in 5s...")
            time.sleep(5)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    now = datetime.now(timezone.utc)
    print(f"Signal Bot starting — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Environment: {OANDA_ENV.upper()}")
    print()

    # Reset daily state at midnight
    def daily_reset_loop():
        global session_close_sent
        while True:
            now = datetime.now(timezone.utc)
            # Reset at 00:01 UTC each day
            if now.hour == 0 and now.minute == 1:
                signaled.clear()
                open_trades.clear()
                session_close_sent = False
                fetch_levels_and_ma()
                print(f"[{_now()}] Daily reset done.")
                time.sleep(60)
            time.sleep(30)

    fetch_levels_and_ma()

    threading.Thread(target=ma_refresh_loop,    daemon=True).start()
    threading.Thread(target=daily_reset_loop,   daemon=True).start()

    stream()
