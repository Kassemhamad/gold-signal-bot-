"""Gold paper-trading decision journal. READ ONLY OANDA, NO ORDERS.
Independent of existing live-signal workflow. All trade accounting is simulated.
"""
import datetime as dt
import json, os, pathlib, requests
from zoneinfo import ZoneInfo

NY=ZoneInfo("America/New_York")
API="https://api-fxpractice.oanda.com" if os.getenv("OANDA_ENV","practice").lower()=="practice" else "https://api-fxtrade.oanda.com"
HEAD={"Authorization":"Bearer "+os.environ["OANDA_API_TOKEN"]}
STATE=pathlib.Path("paper_pro_gold_state.json")
JOURNAL=pathlib.Path("paper_pro_gold_journal.jsonl")
INITIAL_EQUITY=10000.0
RISK_FRACTION=.005
MAX_SPREAD=1.5
MAX_TRADES_PER_DAY=1

def fetch(granularity,count):
    resp=requests.get(API+"/v3/instruments/XAU_USD/candles",
        params={"granularity":granularity,"count":count,"price":"BA"},
        headers=HEAD,timeout=40)
    resp.raise_for_status()
    result=[]
    for b in resp.json()["candles"]:
        if not b.get("complete",False):continue
        stamp=dt.datetime.fromisoformat(b["time"].replace("Z","+00:00"))
        result.append({"time":stamp,**{s+k:float(v[k]) for s,v in (("b",b["bid"]),("a",b["ask"])) for k in "ohlc"}})
    return result

def mid(b,key):
    return (b["b"+key]+b["a"+key])/2

def load():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"equity":INITIAL_EQUITY,"position":None,"last_checked_bar":None,
            "last_trade_date":None,"last_evaluated_bar":None,"total_realized_r":0.0,
            "closed_trades":0}

def save(state):
    STATE.write_text(json.dumps(state,indent=2)+"\n")

def log(**data):
    with JOURNAL.open("a") as f:f.write(json.dumps(data,default=str)+"\n")
    print(json.dumps(data,default=str))

def market_context(m5,m15,h1,now):
    last=m5[-1]
    price=mid(last,"c")
    previous=[b for b in m15 if b["time"]<now-dt.timedelta(minutes=15)]
    # Liquidity based on prior 24h, not today's NY live bar.
    reference=previous[-96:]
    upper=max(mid(x,"h") for x in reference) if reference else None
    lower=min(mid(x,"l") for x in reference) if reference else None
    h1_closes=[mid(x,"c") for x in h1]
    slope=h1_closes[-1]-h1_closes[-5]
    bias="BULL" if slope>2 else ("BEAR" if slope<-2 else "NEUTRAL")
    return {"price":price,"bias":bias,"h1_change_4bars":round(slope,3),
            "reference_high":upper,"reference_low":lower}

def proposal(m5,ctx):
    # Sweep + reclaim of the recent 24h range with a 5m candle close.
    # Confirmation: wick swept level, close back inside, strong candle body.
    latest=m5[-1]
    previous=m5[-2]
    p=mid(latest,"c")
    o=mid(latest,"o")
    spread=latest["ac"]-latest["bc"]
    result={"decision":"HOLD","reason":"No confirmed liquidity sweep","spread":round(spread,3)}
    if spread>MAX_SPREAD:
        result["reason"]="Spread too wide for paper entry"
        return result
    atr=sum(mid(x,"h")-mid(x,"l") for x in m5[-15:-1])/14
    if atr<=0:
        result["reason"]="No volatility"
        return result
    low=ctx["reference_low"];high=ctx["reference_high"]
    if low is None or high is None:
        result["reason"]="Insufficient reference data"
        return result
    bullish=mid(latest,"l")<low and p>low and p>o and p>mid(previous,"c")
    bearish=mid(latest,"h")>high and p<high and p<o and p<mid(previous,"c")
    if bullish and ctx["bias"]!="BEAR":
        side=1
        entry=latest["ac"]
        stop=min(latest["bl"],mid(previous,"l"))-0.15*atr
    elif bearish and ctx["bias"]!="BULL":
        side=-1
        entry=latest["bc"]
        stop=max(latest["ah"],mid(previous,"h"))+0.15*atr
    else:return result
    distance=side*(entry-stop)
    if not (2<=distance<=25):
        result["reason"]=f"Invalid stop distance {distance:.2f}"
        return result
    target=entry+side*1.5*distance
    result.update(decision="BUY" if side==1 else "SELL",
        reason="Sweep and reclaim confirmed by closed M5 candle; H1 bias not opposed",
        entry=round(entry,3),stop=round(stop,3),target=round(target,3),
        risk_distance=round(distance,3),rr=1.5,signal_candle=latest["time"].isoformat())
    return result

def mark_position(position,bars):
    # Simulate using executable quote sides, SL before TP on ambiguous candles.
    side=1 if position["side"]=="BUY" else -1
    from_time=dt.datetime.fromisoformat(position["last_scanned_bar"])
    newer=[b for b in bars if b["time"]>from_time]
    for b in newer:
        sl=position["stop"];tp=position["target"]
        opened=b["bo"] if side==1 else b["ao"]
        entry=position["entry"]
        if side*(opened-sl)<=0:return "LOSS",side*(opened-entry),b["time"]
        if side*(opened-tp)>=0:return "WIN",side*(opened-entry),b["time"]
        low=b["bl"] if side==1 else b["al"]
        high=b["bh"] if side==1 else b["ah"]
        stop_hit=(b["bl"]<=sl if side==1 else b["ah"]>=sl)
        target_hit=(b["bh"]>=tp if side==1 else b["al"]<=tp)
        if stop_hit:return "LOSS",side*(sl-entry),b["time"]
        if target_hit:return "WIN",side*(tp-entry),b["time"]
    if newer:position["last_scanned_bar"]=newer[-1]["time"].isoformat()
    return None,None,None

def main():
    now=dt.datetime.now(dt.timezone.utc)
    ny=now.astimezone(NY)
    state=load()
    m1=fetch("M1",500)
    m5=fetch("M5",180)
    m15=fetch("M15",140)
    h1=fetch("H1",80)
    if min(map(len,[m1,m5,m15,h1]))<20:raise RuntimeError("Insufficient candle data")
    ctx=market_context(m5,m15,h1,m5[-1]["time"])
    # Never claim continuity if historic position data is older than 500 M1 bars.
    if state["position"]:
        oldest=m1[0]["time"]
        scan_time=dt.datetime.fromisoformat(state["position"]["last_scanned_bar"])
        if scan_time<oldest-dt.timedelta(minutes=1):
            log(time=now.isoformat(),decision="DATA_GAP",reason="Unscanned period exceeds available M1 history",position=state["position"])
        else:
            outcome,price_pnl,t=mark_position(state["position"],m1)
            if outcome:
                position=state["position"]
                risk_money=position["risk_money"]
                pnl_money=(price_pnl/position["risk_distance"])*risk_money
                state["equity"]+=pnl_money
                state["closed_trades"]+=1
                state["total_realized_r"]+=price_pnl/position["risk_distance"]
                state["position"]=None
                log(time=now.isoformat(),event="PAPER_EXIT",outcome=outcome,
                    filled_at=t.isoformat(),pnl_usd=round(pnl_money,2),
                    pnl_R=round(price_pnl/position["risk_distance"],3),
                    equity=round(state["equity"],2))
    # Analyze only during NY regular session, never if data is stale.
    if not (ny.weekday()<5 and dt.time(9,30)<=ny.time()<dt.time(16,0)):
        log(time=now.isoformat(),decision="HOLD",reason="Outside NY entry session",context=ctx)
    elif state["position"]:
        log(time=now.isoformat(),decision="HOLD",reason="Existing paper position",context=ctx)
    elif state["last_trade_date"]==str(ny.date()):
        log(time=now.isoformat(),decision="HOLD",reason="Daily trade limit reached",context=ctx)
    elif (now-m5[-1]["time"]).total_seconds()>600:
        log(time=now.isoformat(),decision="HOLD",reason="Market data stale",context=ctx)
    elif state["last_evaluated_bar"]!=m5[-1]["time"].isoformat():
        state["last_evaluated_bar"]=m5[-1]["time"].isoformat()
        idea=proposal(m5,ctx)
        log(time=now.isoformat(),context=ctx,**idea)
        if idea["decision"] in ("BUY","SELL"):
            risk_money=round(state["equity"]*RISK_FRACTION,2)
            state["position"]={"side":idea["decision"],"entry":idea["entry"],
                "stop":idea["stop"],"target":idea["target"],
                "risk_distance":idea["risk_distance"],"risk_money":risk_money,
                "open_at":now.isoformat(),"last_scanned_bar":m1[-1]["time"].isoformat()}
            state["last_trade_date"]=str(ny.date())
            log(time=now.isoformat(),event="PAPER_ENTRY",position=state["position"])
    save(state)
if __name__=="__main__":main()
