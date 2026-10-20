"""SMA-filtered New York 09:30 opening 5-minute candle experiment.
Use previous *closed* H1 SMA; no future candle leakage.
Compares SMA20, SMA50, SMA100, SMA200 versus unfiltered baseline.
"""
import csv,io,json,urllib.request
from collections import defaultdict
from bisect import bisect_left
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo
from backtest import read_bars

URL="https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/main/XAUUSD_5m.csv"
NY=ZoneInfo("America/New_York")
def fetch():
    rd=csv.DictReader(io.StringIO(urllib.request.urlopen(URL,timeout=90).read().decode("utf-8-sig")))
    p=Path("ny_sma_gold.csv")
    with p.open("w",newline="") as f:
        w=csv.writer(f);w.writerow(["timestamp","open","high","low","close"])
        for r in rd:
            t=r["datetime"].strip()
            if not t.endswith("Z") and "+" not in t:t+="Z"
            w.writerow([t,r["open"],r["high"],r["low"],r["close"]])
    return read_bars(p)

def sma_bias(bars,length):
    # Build complete hourly OHLC only. Use previous hour close vs SMA.
    grouped=defaultdict(list)
    for bar in bars:
        grouped[bar.ts.replace(minute=0,second=0,microsecond=0)].append(bar)
    hours=sorted(grouped)
    full={h: grouped[h][-1].c for h in hours if len(grouped[h])==12}
    completed_hours=sorted(full)
    means={}
    for i in range(length-1,len(completed_hours)):
        h=completed_hours[i]
        means[h]=sum(full[x] for x in completed_hours[i-length+1:i+1])/length
    out={}
    for bar in bars:
        hour=bar.ts.replace(minute=0,second=0,microsecond=0)
        idx=bisect_left(completed_hours,hour)-1
        if idx<0:continue
        h=completed_hours[idx]
        if h in means:out[bar.ts]=(full[h],means[h])
    return out

def backtest(bars,length=None,spread=.2,slippage=.1,inverse=False):
    bydate=defaultdict(list)
    bias=sma_bias(bars,length) if length else {}
    for i,b in enumerate(bars):
        t=b.ts.astimezone(NY)
        if t.time()==time(9,30):bydate[t.date()].append((i,b))
    rows=[];balance=30;peak=30;maxdd=0
    for day,opens in sorted(bydate.items()):
        i,b=opens[0]
        if i+1>=len(bars):continue
        nxt=bars[i+1]
        if nxt.ts.astimezone(NY).date()!=day or nxt.ts.astimezone(NY).time()!=time(9,35):continue
        if b.c==b.o:continue
        direction=1 if b.c>b.o else -1
        if length:
            if b.ts not in bias:continue
            hclose,mean=bias[b.ts]
            if inverse:
                if not ((direction==1 and hclose<mean) or (direction==-1 and hclose>mean)):continue
            else:
                if not ((direction==1 and hclose>mean) or (direction==-1 and hclose<mean)):continue
        entry=nxt.o+direction*(spread+slippage)/2
        stop=b.l if direction==1 else b.h
        risk=direction*(entry-stop)
        if risk<=0:continue
        target=entry+direction*risk
        reason="TIME";price=None
        for j in range(i+1,min(len(bars),i+79)):
            x=bars[j]
            if x.ts.astimezone(NY).date()!=day:break
            if (x.l<=stop if direction==1 else x.h>=stop):
                price=stop;reason="SL";break
            if (x.h>=target if direction==1 else x.l<=target):
                price=target-direction*(spread+slippage)/2;reason="TP";break
            price=x.c-direction*(spread+slippage)/2
        if price is None:continue
        r=direction*(price-entry)/risk
        balance*=1+.01*r
        peak=max(balance,peak)
        maxdd=max(maxdd,(peak-balance)/peak)
        rows.append(dict(day=str(day),side="BUY" if direction==1 else "SELL",result=reason,R=round(r,4),equity=round(balance,2)))
    wins=sum(t["R"]>0 for t in rows)
    return {"sma":length or "none","filter_mode":"inverse" if inverse else "trend","trades":len(rows),"wins":wins,"losses":len(rows)-wins,
      "win_rate_pct":round(100*wins/len(rows),2) if rows else None,
      "total_R":round(sum(x["R"] for x in rows),2),
      "ending_balance":round(balance,2),"max_drawdown_pct":round(100*maxdd,2),
      "trades_detail":rows}

if __name__=="__main__":
    bars=fetch()
    res={"source":URL,"period":[bars[0].ts.isoformat(),bars[-1].ts.isoformat()],
         "opening_candle":"09:30-09:35 NY 5-minute candle",
         "filter":"trend: buy above/sell below SMA; inverse: sell above/buy below SMA; last completed H1 close vs SMA",
         "scenarios":{}}
    for spread in (.2,.4,.8):
        res["scenarios"][str(spread)]=[backtest(bars,n,spread,inverse=inv) for inv in [False,True] for n in [20,50,100,200]] + [backtest(bars,None,spread)]
        print(json.dumps([{k:v for k,v in t.items() if k!="trades_detail"} for t in res["scenarios"][str(spread)]],indent=2))
    Path("ny_sma_results.json").write_text(json.dumps(res,indent=2))
