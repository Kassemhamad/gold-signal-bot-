"""Read-only XAUUSD full NY session range breakout research, no orders.
Range: 09:30-16:00 America/New_York. Post-close M15 candle-close breakout.
First qualifying breakout only per NY day; enter following bar open.
Stop at range midpoint, breakeven after +0.3R, TP +1R. Allow overnight exits.
"""
import csv, datetime as dt, os, requests
from collections import Counter,defaultdict
from zoneinfo import ZoneInfo
NY=ZoneInfo("America/New_York")
DAYS=int(os.getenv("BACKTEST_DAYS","180"))
API="https://api-fxpractice.oanda.com" if os.getenv("OANDA_ENV","practice").lower()=="practice" else "https://api-fxtrade.oanda.com"
PREFIX="ny_gold_full_session_breakout_m15"
def get_bars():
    stop=dt.datetime.now(dt.timezone.utc).replace(second=0,microsecond=0)
    t=stop-dt.timedelta(days=DAYS)
    allbars={}
    while t<stop:
        nxt=min(t+dt.timedelta(days=2),stop)
        r=requests.get(API+"/v3/instruments/XAU_USD/candles",
          params={"from":t.isoformat(),"to":nxt.isoformat(),"granularity":"M1","price":"BA"},
          headers={"Authorization":"Bearer "+os.environ["OANDA_API_TOKEN"]},timeout=60)
        r.raise_for_status()
        for v in r.json().get("candles",[]):
            if not v.get("complete"):continue
            stamp=dt.datetime.fromisoformat(v["time"].replace("Z","+00:00"))
            b,a=v["bid"],v["ask"]
            allbars[stamp]={"t":stamp,**{s+k:float(q[k]) for s,q in (("b",b),("a",a)) for k in "ohlc"}}
        t=nxt
    return [allbars[x] for x in sorted(allbars)]
def mid(b,k):return (b["b"+k]+b["a"+k])/2
def exit_position(bars,index,side,entry,midpoint):
    risk=side*(entry-midpoint)
    if risk<=0:return None
    target=entry+side*risk
    stop=midpoint
    armed=False
    for i in range(index,len(bars)):
        b=bars[i]
        opening=b["bo"] if side==1 else b["ao"]
        if i>index:
            if side*(opening-stop)<=0:
                return ("BE" if armed else "LOSS"),round(side*(opening-entry),3),i,risk
            if side*(opening-target)>=0:
                return "WIN",round(side*(opening-entry),3),i,risk
        bad=b["bl"]<=stop if side==1 else b["ah"]>=stop
        good=b["bh"]>=target if side==1 else b["al"]<=target
        trigger=b["bh"]>=entry+side*.3*risk if side==1 else b["al"]<=entry+side*.3*risk
        # Conservative same-minute ordering: stop before target and trigger.
        if bad:return ("BE" if armed else "LOSS"),round(side*(stop-entry),3),i,risk
        if good:return "WIN",round(risk,3),i,risk
        # Stop becomes active after this entire 1m candle.
        if trigger and not armed:
            armed=True
            stop=entry
    return "OPEN",None,None,risk
def main():
    bars=get_bars()
    bydate=defaultdict(list)
    for i,b in enumerate(bars):
        local=b["t"].astimezone(NY)
        if local.weekday()<5:bydate[local.date()].append((i,b))
    rows=[]
    for day,entries in sorted(bydate.items()):
        regular=[(i,b) for i,b in entries if "09:30"<=b["t"].astimezone(NY).strftime("%H:%M")<"16:00"]
        if len(regular)!=390:continue
        if any((regular[k][1]["t"]-regular[k-1][1]["t"]).total_seconds()!=60 for k in range(1,len(regular))):continue
        high=max(mid(b,"h") for _,b in regular)
        low=min(mid(b,"l") for _,b in regular)
        center=(high+low)/2
        if high<=low:continue
        # Post-close, same NY date. Aggregate closed 15-minute blocks beginning 16:00.
        after=[(i,b) for i,b in entries if b["t"].astimezone(NY).strftime("%H:%M")>="16:00"]
        selected=None
        for start in range(0,len(after)-14,15):
            block=after[start:start+15]
            if any((block[k][1]["t"]-block[k-1][1]["t"]).total_seconds()!=60 for k in range(1,15)):continue
            if block[0][1]["t"].astimezone(NY).minute%15!=0:continue
            end_index=block[-1][0]
            if end_index+1>=len(bars) or (bars[end_index+1]["t"]-bars[end_index]["t"]).total_seconds()!=60:continue
            close=mid(block[-1][1],"c")
            side=1 if close>high else (-1 if close<low else 0)
            if side:
                selected=(end_index+1,side)
                break
        if not selected:continue
        idx,side=selected
        entry=bars[idx]["ao"] if side==1 else bars[idx]["bo"]
        result=exit_position(bars,idx,side,entry,center)
        if result is None:continue
        outcome,pnl,exitidx,risk=result
        rows.append({"day":str(day),"range_high":round(high,3),"range_low":round(low,3),
          "midpoint":round(center,3),"direction":"BUY" if side==1 else "SELL",
          "entry_time_ny":bars[idx]["t"].astimezone(NY).isoformat(),"entry":round(entry,3),
          "risk":round(risk,3),"outcome":outcome,"pnl_price_units":pnl,
          "pnl_R":round(pnl/risk,3) if pnl is not None else None,
          "exit_time_ny":bars[exitidx]["t"].astimezone(NY).isoformat() if exitidx is not None else ""})
    fields=["day","range_high","range_low","midpoint","direction","entry_time_ny",
      "entry","risk","outcome","pnl_price_units","pnl_R","exit_time_ny"]
    with open(PREFIX+"_trades.csv","w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    tally=Counter(x["outcome"] for x in rows)
    realized=[x for x in rows if x["pnl_R"] is not None]
    pnl=sum(x["pnl_price_units"] for x in realized)
    total_r=sum(x["pnl_R"] for x in realized)
    summary=(f"Full NY Session 15-minute Post-Close Breakout | {DAYS} calendar days\n"
      f"09:30-16:00 NY high/low, first M15 close after 16:00 breaking range.\n"
      f"SL=session midpoint; trigger breakeven +0.3R; TP +1R.\n"
      f"Valid trades: {len(rows)}\nWins: {tally['WIN']}\nLosses: {tally['LOSS']}\n"
      f"Breakevens: {tally['BE']}\nStill open: {tally['OPEN']}\n"
      f"Win percentage of closed trades: {100*tally['WIN']/len(realized) if realized else 0:.2f}%\n"
      f"Realized gross price-unit P/L: {pnl:.3f}\nRealized total R (varying range-defined stops): {total_r:.3f}\n"
      f"Average R per resolved trade: {total_r/len(realized) if realized else 0:.3f}\n"
      f"OANDA bid/ask candles; no commission/financing/slippage; minute ties stop-first.\n"
      f"Positions may hold overnight; no forced 16:00 exit.\n"
      f"Calendar-month results are in per-trade CSV.\n")
    print(summary)
    with open(PREFIX+"_summary.txt","w") as f:f.write(summary)
if __name__=="__main__":main()
