"""Read-only XAU_USD New York 1-minute opening backtest using OANDA candles.
No orders are submitted. Results are gross of spread/slippage.
"""
import csv, datetime as dt, os, sys, requests
from zoneinfo import ZoneInfo

TOKEN=os.environ["OANDA_API_TOKEN"]
HOST="https://api-fxpractice.oanda.com" if os.getenv("OANDA_ENV","practice")=="practice" else "https://api-fxtrade.oanda.com"
NY=ZoneInfo("America/New_York")
HEAD={"Authorization":f"Bearer {TOKEN}"}

def get_bars(days=90):
    end=dt.datetime.now(dt.timezone.utc).replace(second=0,microsecond=0)
    begin=end-dt.timedelta(days=days)
    out=[]
    t=begin
    while t<end:
        nxt=min(t+dt.timedelta(days=3),end)
        res=requests.get(HOST+"/v3/instruments/XAU_USD/candles",
            params={"from":t.isoformat(),"to":nxt.isoformat(),"granularity":"M1","price":"M"},
            headers=HEAD,timeout=45)
        res.raise_for_status()
        for c in res.json()["candles"]:
            if not c["complete"]:continue
            q=c["mid"]
            stamp=dt.datetime.fromisoformat(c["time"].replace("Z","+00:00"))
            out.append(dict(t=stamp,o=float(q["o"]),h=float(q["h"]),l=float(q["l"]),c=float(q["c"])))
        t=nxt
    return sorted({b["t"]:b for b in out}.values(),key=lambda b:b["t"])

def execute(group, start, end):
    # Entries based on last 3 CLOSED bars, enter at next bar open.
    for i in range(3,len(group)-1):
        current=group[i]; ny=current["t"].astimezone(NY)
        clock=ny.strftime("%H:%M")
        if not(start<=clock<end):continue
        prev=group[i-1]; earlier=group[i-2]
        if any((group[k]["t"]-group[k-1]["t"]).total_seconds()!=60 for k in range(i-2,i+2)):continue
        bullish=(earlier["h"]<prev["h"]<current["h"] and
                 earlier["l"]<prev["l"]<current["l"] and current["c"]>prev["h"])
        bearish=(earlier["h"]>prev["h"]>current["h"] and
                 earlier["l"]>prev["l"]>current["l"] and current["c"]<prev["l"])
        if not(bullish or bearish):continue
        side=1 if bullish else -1
        entry=group[i+1]["o"]
        stop=entry-10*side
        tp=entry+10*side
        arm=entry+2*side
        be=False
        for future in group[i+1:]:
            # Constrain each scenario to session close at 16:00 NY.
            if future["t"].astimezone(NY).strftime("%H:%M")>=end:break
            hi=future["h"];lo=future["l"]
            if side==1:
                adverse=lo<=stop
                good=hi>=tp
                armed=hi>=arm
            else:
                adverse=hi>=stop
                good=lo<=tp
                armed=lo<=arm
            # If same OHLC bar touches stop and arm/target, assume adverse first.
            # Breakeven active only on next candle when trigger hits in this candle.
            if adverse:
                return [str(ny.date()),clock,"BUY" if side==1 else "SELL",entry,"BE" if be else "LOSS",0 if be else -10]
            if good:
                return [str(ny.date()),clock,"BUY" if side==1 else "SELL",entry,"WIN",10]
            if armed:be=True;stop=entry
        last=next((b for b in reversed(group[i+1:]) if b["t"].astimezone(NY).strftime("%H:%M")<end),None)
        if last:
            pnl=side*(last["c"]-entry)
            return [str(ny.date()),clock,"BUY" if side==1 else "SELL",entry,"TIME",round(pnl,3)]
        return None
    return None

def run():
    days=int(os.getenv("BACKTEST_DAYS","90"))
    start=os.getenv("BACKTEST_START","09:30")
    end=os.getenv("BACKTEST_END","16:00")
    bars=get_bars(days)
    groups={}
    for b in bars:
        ny=b["t"].astimezone(NY)
        if ny.weekday()<5:
            groups.setdefault(ny.date(),[]).append(b)
    slots=[("09:30","10:30"),("10:30","11:30"),("11:30","12:30")]
    trades=[]
    for day,gg in sorted(groups.items()):
        for slot_start,slot_end in slots:
            trade=execute(gg,slot_start,slot_end)
            if trade is not None:
                trades.append([slot_start+"-"+slot_end]+trade)
    with open("ny_gold_1m_hourly_overlap_trades.csv","w",newline="") as f:
        w=csv.writer(f);w.writerow(["window_ny","date","signal_time_ny","direction","entry_price","outcome","price_pnl"]);w.writerows(trades)
    counts={k:sum(x[5]==k for x in trades) for k in ("WIN","LOSS","BE","TIME")}
    total=sum(float(x[6]) for x in trades)
    summary=(f"XAUUSD 1m three overlapping hourly entries\nDays requested: {days}\n"
       f"Bars fetched: {len(bars)}\nEntry windows: 09:30-10:30, 10:30-11:30, 11:30-12:30 New York\n"
       f"Trades: {len(trades)}\nWins: {counts['WIN']}\nLosses: {counts['LOSS']}\n"
       f"Breakevens: {counts['BE']}\nTime exits: {counts['TIME']}\n"
       f"Gross price units total: {total:.2f}\n"
       "SL 10; move SL to breakeven at +2; TP 10. One trade/hour window; up to three independent simultaneous positions/day.\n"
       "Conservative same-bar ordering. Mid-price candles; excludes spreads, fees and slippage.\n"
       "1-minute OHLC data cannot determine intrabar event order accurately.\n")
    print(summary)
    open("ny_gold_1m_hourly_overlap_summary.txt","w").write(summary)

if __name__=="__main__":run()
