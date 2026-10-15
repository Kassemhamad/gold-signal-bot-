"""Read-only XAUUSD daily-strategy research. No orders. OANDA M1 bid/ask.
Sweep a small, predeclared parameter grid and validate on the later 1/3.
"""
import csv, datetime as dt, os, statistics, requests
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo
NY=ZoneInfo("America/New_York")
API="https://api-fxpractice.oanda.com" if os.getenv("OANDA_ENV","practice").lower()=="practice" else "https://api-fxtrade.oanda.com"
DAYS=int(os.getenv("BACKTEST_DAYS","180"))
PREFIX="ny_gold_daily_rr1plus_research"

def candles():
    end=dt.datetime.now(dt.timezone.utc).replace(second=0,microsecond=0)
    start=end-dt.timedelta(days=DAYS)
    result={}
    while start<end:
        bound=min(start+dt.timedelta(days=2),end)
        r=requests.get(API+"/v3/instruments/XAU_USD/candles",headers={"Authorization":"Bearer "+os.environ["OANDA_API_TOKEN"]},
          params={"from":start.isoformat(),"to":bound.isoformat(),"granularity":"M1","price":"BA"},timeout=60)
        r.raise_for_status()
        for c in r.json().get("candles",[]):
            if not c.get("complete"):continue
            t=dt.datetime.fromisoformat(c["time"].replace("Z","+00:00"))
            b,a=c["bid"],c["ask"]
            result[t]={"t":t,**{s+k:float(v[k]) for s,v in [("b",b),("a",a)] for k in "ohlc"}}
        start=bound
    return [result[x] for x in sorted(result)]
def mid(b,k):return (b["b"+k]+b["a"+k])/2
def indicators(bars):
    sums={20:0.,50:0.,100:0.,200:0.}
    window=[]
    last=None
    for b in bars:
        # Preserve continuous trading history across days, reset after session gaps.
        if last and (b["t"]-last).total_seconds()!=60:
            window=[]
            sums={20:0.,50:0.,100:0.,200:0.}
        c=mid(b,"c")
        window.append(c)
        for n in sums:
            sums[n]+=c
            if len(window)>n:sums[n]-=window[-n-1]
            b["ma"+str(n)]=sums[n]/n if len(window)>=n else None
        if len(window)>200:window.pop(0)
        last=b["t"]
def signal(bars,i,kind,ma):
    if i<3:return 0
    a,b,c=bars[i-3:i]
    if any((bars[k]["t"]-bars[k-1]["t"]).total_seconds()!=60 for k in range(i-2,i+1)):return 0
    price=mid(c,"c")
    avg=c["ma"+str(ma)]
    if avg is None:return 0
    if kind=="structure":
        up=mid(a,"h")<mid(b,"h")<mid(c,"h") and mid(a,"l")<mid(b,"l")<mid(c,"l") and price>mid(b,"h")
        down=mid(a,"h")>mid(b,"h")>mid(c,"h") and mid(a,"l")>mid(b,"l")>mid(c,"l") and price<mid(b,"l")
    elif kind=="momentum":
        up=mid(a,"c")<mid(b,"c")<mid(c,"c")
        down=mid(a,"c")>mid(b,"c")>mid(c,"c")
    else:raise ValueError(kind)
    return 1 if up and price>avg else (-1 if down and price<avg else 0)

def settle(bars,i,side,sl,tp):
    entry=bars[i]["ao"] if side==1 else bars[i]["bo"]
    stop=entry-side*sl
    target=entry+side*tp
    for j in range(i,len(bars)):
        b=bars[j]
        # Do not close at NY session end: stop/target remain active across sessions.
        opening=b["bo"] if side==1 else b["ao"]
        if j>i:
            if side*(opening-stop)<=0:return side*(opening-entry),"LOSS",j
            if side*(opening-target)>=0:return side*(opening-entry),"WIN",j
        bad=b["bl"]<=stop if side==1 else b["ah"]>=stop
        good=b["bh"]>=target if side==1 else b["al"]<=target
        if bad:return -sl,"LOSS",j
        if good:return tp,"WIN",j
    return None,"OPEN",None

def entries(bars,kind,ma,reverse):
    # First qualifying signal from 09:30-12:30 NY, at most one entry per day.
    daily={}
    for i,b in enumerate(bars):
        ny=b["t"].astimezone(NY)
        clock=ny.strftime("%H:%M")
        if ny.weekday()>=5 or not("09:30"<=clock<"12:30"):continue
        if ny.date() in daily:continue
        side=signal(bars,i,kind,ma)
        if side:
            daily[ny.date()]=(i,-side if reverse else side)
    return daily

def metrics(rows):
    realized=[x for x in rows if x["pnl"] is not None]
    won=sum(x["outcome"]=="WIN" for x in realized)
    lost=sum(x["outcome"]=="LOSS" for x in realized)
    pnl=sum(x["pnl"] for x in realized)
    peak=equity=drawdown=0.
    for x in realized:
        equity+=x["pnl"]
        peak=max(peak,equity)
        drawdown=max(drawdown,peak-equity)
    return {"trades":len(realized),"wins":won,"losses":lost,"open":len(rows)-len(realized),
        "win_pct":round(100*won/len(realized),2) if realized else 0.,
        "price_pnl":round(pnl,3),"max_drawdown":round(drawdown,3)}

def main():
    bars=candles()
    indicators(bars)
    dates=sorted({b["t"].astimezone(NY).date() for b in bars if b["t"].astimezone(NY).weekday()<5})
    split=dates[max(1,int(len(dates)*2/3))] if len(dates)>2 else dt.date.max
    candidates=[]
    allrows=[]
    # 2 entry patterns x 4 SMA x 2 directions x 6 reward/risk settings = 96 candidates
    for kind in ("structure","momentum"):
      for ma in (20,50,100,200):
       for reverse in (False,True):
        available=entries(bars,kind,ma,reverse)
        for sl,tp in ((5,5),(5,7.5),(10,10),(10,12),(10,15),(15,15)):
            name=f"{kind}_ma{ma}_{'reverse' if reverse else 'trend'}_sl{sl}_tp{tp}"
            trades=[]
            for d,(i,side) in sorted(available.items()):
                pnl,outcome,exit_idx=settle(bars,i,side,sl,tp)
                row={"strategy":name,"date":str(d),"split":"train" if d<split else "validation",
                     "entry_ny":bars[i]["t"].astimezone(NY).isoformat(),"side":"BUY" if side==1 else "SELL",
                     "outcome":outcome,"pnl":round(pnl,3) if pnl is not None else None,
                     "exit_ny":bars[exit_idx]["t"].astimezone(NY).isoformat() if exit_idx is not None else ""}
                trades.append(row)
            train=metrics([r for r in trades if r["split"]=="train"])
            validate=metrics([r for r in trades if r["split"]=="validation"])
            full=metrics(trades)
            row={"strategy":name,"train_win_pct":train["win_pct"],"train_trades":train["trades"],
                 "train_price_pnl":train["price_pnl"],"validation_win_pct":validate["win_pct"],
                 "validation_trades":validate["trades"],"validation_price_pnl":validate["price_pnl"],
                 "full_win_pct":full["win_pct"],"full_trades":full["trades"],"full_price_pnl":full["price_pnl"],
                 "max_drawdown":full["max_drawdown"],"unclosed":full["open"],
                 "passes_threshold":(train["win_pct"]>=60 and validate["win_pct"]>=60 and train["price_pnl"]>0
                   and validate["price_pnl"]>0 and train["trades"]>=25 and validate["trades"]>=12)}
            candidates.append(row)
            allrows.extend(trades)
    candidates.sort(key=lambda r:(r["passes_threshold"],r["validation_price_pnl"],r["validation_win_pct"]),reverse=True)
    with open(PREFIX+"_ranking.csv","w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(candidates[0]));w.writeheader();w.writerows(candidates)
    with open(PREFIX+"_trades.csv","w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["strategy","date","split","entry_ny","side","outcome","pnl","exit_ny"])
        w.writeheader();w.writerows(allrows)
    eligible=[x for x in candidates if x["passes_threshold"]]
    lines=[f"XAUUSD daily strategy research ({DAYS} days; {len(bars)} M1 bid/ask bars)",
       f"Train/validation split date: {split}; candidate count: {len(candidates)}",
       f"1:1 OR BETTER - Candidates passing >=60% winners and positive P/L in BOTH splits: {len(eligible)}",
       "All TP >= SL. Bid/ask spread included; commission/financing/slippage excluded.",
       "Trade enters 9:30-12:30 NY, once per day; can hold overnight. 1m same-bar stop first.",
       "Selection on validation results is exploratory, NOT a clean forward test.",
       "OPEN trades excluded from realized win rates/P&L.",
       "TOP 10 BY SCREENING PASS THEN VALIDATION P/L:"]
    for r in candidates[:10]:
        lines.append(f"{r['strategy']}: train {r['train_trades']}tr {r['train_win_pct']}% pnl {r['train_price_pnl']}; validation {r['validation_trades']}tr {r['validation_win_pct']}% pnl {r['validation_price_pnl']}; full pnl {r['full_price_pnl']}; pass={r['passes_threshold']}")
    summary="\n".join(lines)+"\n"
    print(summary)
    with open(PREFIX+"_summary.txt","w") as f:f.write(summary)
if __name__=="__main__":main()
