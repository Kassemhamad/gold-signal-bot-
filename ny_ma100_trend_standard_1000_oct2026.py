"""READ ONLY. October 2026 XAUUSD 09:30 NY; M1 SMA(100) trend, +/-$20 gold,
0.02 STANDARD LOT (100 oz/lot), double after SL/reset after TP.
Simulate $1000 account at various leverage rates; no trades transmitted.
Dukascopy bid/ask M1 data; include spread, omit fees/overnight swaps.
"""
import csv,datetime as dt,json,math
from collections import deque
from zoneinfo import ZoneInfo

UTC=dt.timezone.utc
NY=ZoneInfo("America/New_York")
FIRST=dt.date(2026,10,1)
LAST=dt.date(2026,11,1)
BASE_LOT=.02
CONTRACT_OZ=100.
TP_SL=20.
CAPITAL=1000.
MA_WINDOW=100
MAINTENANCE_MARGIN_LEVEL=.50

def get_bars():
    sides={}
    for side in ("bid","ask"):
        data={}
        with open("download/gold_"+side+".csv",newline="") as f:
            for row in csv.DictReader(f):
                stamp=dt.datetime.fromtimestamp(int(row["timestamp"])/1000,UTC)
                data[stamp]=tuple(float(row[k]) for k in ("open","high","low","close"))
        sides[side]=data
    ts=sorted(sides["bid"].keys() & sides["ask"].keys())
    bars=[]
    for t in ts:
        bo,bh,bl,bc=sides["bid"][t]
        ao,ah,al,ac=sides["ask"][t]
        bars.append(dict(t=t,bo=bo,bh=bh,bl=bl,bc=bc,ao=ao,ah=ah,al=al,ac=ac,mid=(bc+ac)/2))
    return bars

def simulation(bars,leverage):
    history=deque(maxlen=MA_WINDOW)
    active=[];trades=[];skips=[]
    balance=CAPITAL;nextlot=BASE_LOT;halt=False
    min_equity=CAPITAL;max_equity=CAPITAL;max_dd=0.;peak_equity=CAPITAL
    max_lots=0.; max_used_margin=0.; margin_closeouts=0
    ambiguous_bars=0; min_free_margin=CAPITAL
    lastdate=None
    def mark(p,b,a):
        return (b-p["entry"])*p["lot"]*CONTRACT_OZ if p["side"]=="BUY" else (p["entry"]-a)*p["lot"]*CONTRACT_OZ
    def equity(b,a):
        return balance+sum(mark(p,b,a) for p in active)
    def used_margin(b,a):
        if math.isinf(leverage):return 0.
        return sum(p["lot"]*CONTRACT_OZ*((a+b)/2)/leverage for p in active)
    def close(p,t,px,outcome,both=False):
        nonlocal balance,nextlot,margin_closeouts
        pnl=(px-p["entry"])*p["lot"]*CONTRACT_OZ*(1 if p["side"]=="BUY" else -1)
        balance+=pnl
        p["outcome"]=outcome
        p["exit_ny"]=t.astimezone(NY).isoformat()
        p["exit_price"]=round(px,4)
        p["pnl_usd"]=round(pnl,5)
        p["sim_balance_after"]=round(balance,5)
        p["hold_hours"]=round((t-p["t"]).total_seconds()/3600,2)
        p["both_hit_1m"]=bool(both)
        active.remove(p)
        if outcome=="SL": nextlot*=2
        if outcome=="TP": nextlot=BASE_LOT
        if outcome=="MARGIN_CLOSE":margin_closeouts+=1
    for b in bars:
        t=b["t"];local=t.astimezone(NY);d=local.date()
        # Trade month, use only 100 fully COMPLETED minute bars for MA.
        # Overnight gaps close existing TP/SL at candle OPEN first.
        gaps=[]
        for p in active:
            if p["side"]=="BUY":
                if b["bo"]<=p["sl"]:gaps.append((p,b["bo"],"SL"))
                elif b["bo"]>=p["tp"]:gaps.append((p,p["tp"],"TP"))
            else:
                if b["ao"]>=p["sl"]:gaps.append((p,b["ao"],"SL"))
                elif b["ao"]<=p["tp"]:gaps.append((p,p["tp"],"TP"))
        for p,px,outcome in sorted(gaps,key=lambda x:(x[2]!="TP",x[0]["id"])):
            close(p,t,px,outcome)
        if FIRST<=d<LAST and d.weekday()<5 and (local.hour,local.minute)==(9,30) and d!=lastdate:
            lastdate=d
            if len(history)==MA_WINDOW and not halt:
                sma=sum(history)/MA_WINDOW
                prev=history[-1]
                side="BUY" if prev>sma else "SELL"
                if prev==sma:side=None
                lot=round(nextlot,8)
                needed=(lot*CONTRACT_OZ*(b["bo"]+b["ao"])/2/leverage) if not math.isinf(leverage) else 0.
                eq=equity(b["bo"],b["ao"])
                free=eq-used_margin(b["bo"],b["ao"])
                if side is None:
                    skips.append(dict(date=str(d),reason="MA_EQUAL",lot=lot))
                elif eq<=0:
                    halt=True
                    skips.append(dict(date=str(d),reason="BANKRUPT",lot=lot))
                elif needed>free+1e-6:
                    skips.append(dict(date=str(d),reason="INSUFFICIENT_MARGIN",lot=lot,
                                      free_usd=round(free,2),needed_usd=round(needed,2)))
                else:
                    entry=b["ao"] if side=="BUY" else b["bo"]
                    p=dict(id=len(trades)+1,date=str(d),entry_ny=local.isoformat(),t=t,side=side,
                           lot=lot,ma100=round(sma,3),signal_previous_close=round(prev,3),
                           entry=entry,entry_spread=round(b["ao"]-b["bo"],4),
                           tp=entry+(TP_SL if side=="BUY" else -TP_SL),
                           sl=entry+(-TP_SL if side=="BUY" else TP_SL))
                    active.append(p);trades.append(p)
            elif not halt:
                skips.append(dict(date=str(d),reason="MISSING_MA_DATA"))
        # Determine stop/target using side-correct bid/ask candle high/low.
        touched=[]
        for p in active:
            if p["side"]=="BUY":
                stop=b["bl"]<=p["sl"];target=b["bh"]>=p["tp"]
            else:
                stop=b["ah"]>=p["sl"];target=b["al"]<=p["tp"]
            if stop or target:
                touched.append((p,"SL" if stop else "TP",stop and target))
        ambiguous_bars+=sum(x[2] for x in touched)
        for p,outcome,both in sorted(touched,key=lambda x:(x[1]!="TP",x[0]["id"])):
            close(p,t,p["sl"] if outcome=="SL" else p["tp"],outcome,both)
        # Approximate margin-call trigger (50% of used margin). No exact intrabar sequence.
        if active and not halt:
            bstress=b["bl"];astress=b["ah"]
            stressed=equity(bstress,astress)
            margin=used_margin(b["bc"],b["ac"])
            if stressed <= MAINTENANCE_MARGIN_LEVEL*margin:
                for p in active.copy():
                    px=bstress if p["side"]=="BUY" else astress
                    close(p,t,px,"MARGIN_CLOSE")
                halt=True
        low_eq=equity(b["bl"],b["ah"])
        eq_end=equity(b["bc"],b["ac"])
        min_equity=min(min_equity,eq_end,low_eq)
        max_equity=max(max_equity,eq_end)
        peak_equity=max(peak_equity,eq_end)
        max_dd=max(max_dd,peak_equity-low_eq)
        m=used_margin(b["bc"],b["ac"])
        max_used_margin=max(max_used_margin,m)
        min_free_margin=min(min_free_margin,eq_end-m)
        max_lots=max(max_lots,sum(p["lot"] for p in active))
        history.append(b["mid"])
    last=bars[-1]
    end_eq=equity(last["bc"],last["ac"])
    sumry=dict(period="October 2026",leverage="unlimited" if math.isinf(leverage) else leverage,
        starting_usd=CAPITAL,ending_balance_usd=round(balance,2),
        ending_equity_usd=round(end_eq,2),net_equity_usd=round(end_eq-CAPITAL,2),
        entries=len(trades),wins=sum(p.get("outcome")=="TP" for p in trades),
        losses=sum(p.get("outcome")=="SL" for p in trades),
        margin_closeouts=margin_closeouts,open_at_end=len(active),
        buys=sum(p["side"]=="BUY" for p in trades),sells=sum(p["side"]=="SELL" for p in trades),
        margin_skips=sum(s["reason"]=="INSUFFICIENT_MARGIN" for s in skips),
        total_skips=len(skips),max_lot=max([p["lot"] for p in trades],default=0),
        next_lot=round(nextlot,6),min_equity_usd=round(min_equity,2),
        max_equity_drawdown_usd=round(max_dd,2),
        max_used_margin_usd=round(max_used_margin,2),
        max_aggregate_lots=max_lots,ambiguous_bars=ambiguous_bars,
        halted=halt,last_data_utc=last["t"].isoformat())
    fields=["id","date","entry_ny","side","lot","ma100","signal_previous_close",
            "entry","entry_spread","tp","sl","outcome","exit_ny","exit_price","pnl_usd",
            "hold_hours","sim_balance_after","both_hit_1m"]
    name="no_margin" if math.isinf(leverage) else str(leverage)
    with open("ma100_"+name+"_trades.csv","w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore");w.writeheader();w.writerows(trades)
    with open("ma100_"+name+"_skips.csv","w",newline="") as f:
        keys=["date","reason","lot","free_usd","needed_usd"]
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(skips)
    return sumry,trades

def main():
    bars=get_bars()
    if len(bars)<300:raise RuntimeError("Not enough M1 bars downloaded")
    result={}
    for lev in [20,100,500,float("inf")]:
        summary,trades=simulation(bars,lev)
        result[str(lev)]=summary
        print(json.dumps(summary,sort_keys=True))
        print("LEDGER FOR",lev)
        for p in trades:
            print(p["date"],p["side"],p["lot"],p.get("outcome","OPEN"),p.get("pnl_usd","OPEN"))
    with open("ma100_2026_october_summary.json","w") as f:json.dump(result,f,indent=2)
    print("Downloaded",len(bars),"one-minute bid/ask bars.")
    print("Only fully closed 1m mid-price bars feed SMA100. Entry at 09:30 NY; 20-price TP/SL; SL first on ambiguous 1m candles.")
    print("Broker-specific spreads represented by bid/ask; commissions, overnight swaps and slippage not modeled.")
    print("Normal contract assumed 100 oz/lot, 50% margin close-out threshold. Margin-check execution approximated from 1m extremes.")
if __name__=="__main__":main()
