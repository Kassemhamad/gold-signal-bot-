"""New York 09:30-09:35 ET opening candle direction strategy, 1:1.
ONE trade/day, enter 09:35 M5 bar open, SL beyond opening candle, TP 1R.
Timezone-aware, stops prioritized on ambiguous bars. Historical simulation only.
"""
import csv, io, json, urllib.request
from collections import defaultdict
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo
from backtest import read_bars

URL="https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/main/XAUUSD_5m.csv"
NY=ZoneInfo("America/New_York")
def download():
    text=urllib.request.urlopen(URL,timeout=90).read().decode("utf-8-sig")
    reader=csv.DictReader(io.StringIO(text))
    rows=[]
    for r in reader:
        ts=r["datetime"].strip()
        if not ts.endswith("Z") and "+" not in ts: ts+="Z"
        rows.append([ts,r["open"],r["high"],r["low"],r["close"]])
    p=Path("ny_gold_m5.csv")
    with p.open("w",newline="") as f:
        writer=csv.writer(f);writer.writerow(["timestamp","open","high","low","close"]);writer.writerows(rows)
    return read_bars(p)
def simulate(bars,spread=.25,slippage=.10,buffer=0.,max_bars=78):
    by_date=defaultdict(list)
    for i,b in enumerate(bars):
        local=b.ts.astimezone(NY)
        by_date[local.date()].append((i,local,b))
    result=[];balance=30.;equity=[balance]
    for day,entries in sorted(by_date.items()):
        opening=[(i,b) for i,t,b in entries if t.time()==time(9,30)]
        if not opening:continue
        i,b=opening[0]
        if i+1>=len(bars):continue
        nxt=bars[i+1]
        if nxt.ts.astimezone(NY).date()!=day or nxt.ts.astimezone(NY).time()!=time(9,35): continue
        if b.c==b.o:continue
        d=1 if b.c>b.o else -1
        # half spread and slippage adversely on entry, similarly on target exit.
        entry=nxt.o + d*(spread+slippage)/2
        stop=(b.l-buffer) if d==1 else (b.h+buffer)
        risk=d*(entry-stop)
        if risk<=0: continue
        target=entry+d*risk  # 1:1 intended gross price risk
        exit_price=None;reason=None;exit_i=None
        for k in range(i+1,min(i+1+max_bars,len(bars))):
            x=bars[k]
            if x.ts.astimezone(NY).date()!=day:break
            # stop before target when both can be touched during the same M5 bar
            stopped=x.l<=stop if d==1 else x.h>=stop
            won=x.h>=target if d==1 else x.l<=target
            if stopped:exit_price=stop;reason="SL";exit_i=k;break
            if won:exit_price=target-d*(spread+slippage)/2;reason="TP";exit_i=k;break
            exit_i=k
        if exit_price is None:
            if exit_i is None:continue
            exit_price=bars[exit_i].c-d*(spread+slippage)/2;reason="TIME"
        R=d*(exit_price-entry)/risk
        # constant 1% fraction risk on each eligible trade for illustrative balance
        balance *= 1+.01*R
        equity.append(balance)
        result.append({"date":str(day),"side":"BUY" if d==1 else "SELL",
            "open_candle_utc":b.ts.isoformat(),"entry":round(entry,3),
            "stop":round(stop,3),"target":round(target,3),
            "outcome":reason,"R":round(R,4),"balance":round(balance,2)})
    wins=sum(x["R"]>0 for x in result)
    peak=equity[0];dd=0.
    for x in equity:
        peak=max(peak,x);dd=max(dd,(peak-x)/peak)
    return {"trades":len(result),"wins":wins,"win_rate":round(wins/len(result),4) if result else None,
      "total_R":round(sum(x["R"] for x in result),3),
      "ending_balance_1pct_risk":round(balance,2),"max_drawdown_pct":round(dd*100,2),
      "outcomes":{key:sum(x["outcome"]==key for x in result) for key in ("SL","TP","TIME")},
      "trade_log":result}

if __name__=="__main__":
    bars=download()
    report={"source":URL,"candle_count":len(bars),
      "period_utc":[bars[0].ts.isoformat(),bars[-1].ts.isoformat()],
      "definition":"First 09:30-09:35 America/New_York M5 candle, entry 09:35 open, stop opening high/low, 1R target, intrabar stop-first, at most 78 bars hold",
      "scenarios":{}}
    for cost in (.20,.40,.80):
        r=simulate(bars,spread=cost,slippage=.10)
        report["scenarios"][f"spread_{cost}"]=r
        print(json.dumps({"scenario":cost,**{k:v for k,v in r.items() if k!="trade_log"}},indent=2))
    Path("ny_open_results.json").write_text(json.dumps(report,indent=2))
    print("Wrote ny_open_results.json")
