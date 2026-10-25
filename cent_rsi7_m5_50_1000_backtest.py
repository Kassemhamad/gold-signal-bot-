"""Simulation only: RSI(7) H4 XAUUSD cent-account paired BUY/SELL doubling backtest."""
import os,csv,datetime as dt,collections,requests
from zoneinfo import ZoneInfo
NY=ZoneInfo("America/New_York")
DAYS=int(os.getenv("BACKTEST_DAYS","365"))
LEVERAGE=float(os.getenv("SIM_LEVERAGE","20"))
OZ_PER_CENT_LOT=float(os.getenv("OZ_PER_CENT_LOT","1"))
BASE="https://api-fxpractice.oanda.com" if os.getenv("OANDA_ENV","practice").lower()=="practice" else "https://api-fxtrade.oanda.com"
def fetch():
    end=dt.datetime.now(dt.timezone.utc).replace(minute=0,second=0,microsecond=0)
    cur=end-dt.timedelta(days=DAYS+8)
    out={}
    while cur<end:
        nxt=min(cur+dt.timedelta(days=5),end)
        r=requests.get(BASE+"/v3/instruments/XAU_USD/candles",headers={"Authorization":"Bearer "+os.environ["OANDA_API_TOKEN"]},
          params={"from":cur.isoformat(),"to":nxt.isoformat(),"granularity":"M5","price":"BA"},timeout=60)
        r.raise_for_status()
        for c in r.json().get("candles",[]):
            if not c.get("complete"):continue
            t=dt.datetime.fromisoformat(c["time"].replace("Z","+00:00"))
            out[t]={"time":t,**{s+k:float(c[v][k]) for v,s in (("bid","b"),("ask","a")) for k in "ohlc"}}
        cur=nxt
    return [out[x] for x in sorted(out)]
def main():
    bars=fetch()
    if len(bars)<200:raise RuntimeError("Insufficient gold data")
    # RSI signals on completed M5 candles; enter at the following M5 open.
    h4=[{"end_index":i,"close":(b["bc"]+b["ac"])/2} for i,b in enumerate(bars)]
    gain=loss=None;prev_close=None;previous_rsi=None;signals=set()
    for c in h4:
        if prev_close is not None:
            diff=c["close"]-prev_close
            u=max(diff,0);d=max(-diff,0)
            if gain is None:
                # accumulate initial 14 changes
                if not hasattr(main,"changes"):main.changes=[]
                main.changes.append((u,d))
                if len(main.changes)==7:
                    gain=sum(x[0] for x in main.changes)/7
                    loss=sum(x[1] for x in main.changes)/7
            else:
                gain=(gain*6+u)/7
                loss=(loss*6+d)/7
            if gain is not None:
                rsi=100 if loss==0 else 100-100/(1+gain/loss)
                if previous_rsi is not None and previous_rsi>30 and rsi<=30:
                    signals.add(c["end_index"]+1)
                previous_rsi=rsi
        prev_close=c["close"]
    balance=1000.;peak=1000.;maxdd=0.;state=None;paused=False;events=[];cycles=0;wins=0;max_level=0
    def log(b,side,level,qty,pnl,outcome):
        events.append({"timestamp_utc":b["time"].isoformat(),"month":b["time"].astimezone(NY).strftime("%Y-%m"),
                       "cycle":cycles,"level":level,"side":side,"cent_lots":round(qty,6),
                       "outcome":outcome,"pnl_usd":round(pnl,5),"balance_usd":round(balance,5)})
    for i,b in enumerate(bars):
        if i<180:continue
        if state is None:
            if i not in signals or paused:continue
            cycles+=1;state={"level":1,"qty":0.02,"entry":None}
        if state["entry"] is None:
            q=state["qty"];entry=(b["ao"]+b["bo"])/2
            # Reserve conservative margin for both gross hedge legs, actual broker may net margin.
            margin=entry*(q+q/2)*OZ_PER_CENT_LOT/LEVERAGE
            if margin>balance or q*OZ_PER_CENT_LOT*50/2>balance:
                log(b,"ACCOUNT",state["level"],q,0,"INSUFFICIENT_MARGIN")
                paused=True;state=None;continue
            state["entry"]=entry;state["buy_open"]=b["ao"];state["sell_open"]=b["bo"]
        s=state;entry=s["entry"];q=s["qty"]
        # Same hourly bar can touch both bounds. Choose SL first (conservative).
        down=b["bl"]<=entry-50 or b["al"]<=entry-50
        up=b["bh"]>=entry+50 or b["ah"]>=entry+50
        if not (down or up):continue
        hit="DOWN" if down else "UP";exit_mid=entry-50 if down else entry+50
        # Include approximate half spread on exit, using current bar spread.
        spr=max(0,b["ac"]-b["bc"])
        buy_exit=exit_mid-spr/2
        sell_exit=exit_mid+spr/2
        bp=(buy_exit-s["buy_open"])*q*OZ_PER_CENT_LOT
        sp=(s["sell_open"]-sell_exit)*q/2*OZ_PER_CENT_LOT
        balance+=bp+sp
        log(b,"BUY",s["level"],q,bp,"TP" if hit=="UP" else "SL")
        log(b,"SELL",s["level"],q/2,sp,"SL" if hit=="UP" else "TP")
        peak=max(peak,balance);maxdd=max(maxdd,peak-balance)
        max_level=max(max_level,s["level"])
        if hit=="UP":
            wins+=1;state=None
        elif balance<=0:
            log(b,"ACCOUNT",s["level"],q,0,"BANKRUPT");paused=True;state=None
        else:
            state={"level":s["level"]+1,"qty":q*2,"entry":None}
    # Research cycle ledger: each paired level is one BUY attempt and one SELL.
    cycle_map=collections.OrderedDict()
    for e in events:
        c=cycle_map.setdefault(e["cycle"],{"cycle":e["cycle"],"start_utc":"","last_utc":"","buy_attempts":0,"sell_trades":0,"sell_wins":0,"sell_pnl":0.0,"net_pnl":0.0,"buy_tp":False,"highest_level":0})
        c["last_utc"]=e["timestamp_utc"]
        if not c["start_utc"]:c["start_utc"]=e["timestamp_utc"]
        if e["side"]=="BUY":
            c["buy_attempts"]+=1;c["highest_level"]=max(c["highest_level"],e["level"])
            c["buy_tp"]|=(e["outcome"]=="TP")
            c["net_pnl"]+=e["pnl_usd"]
        if e["side"]=="SELL":
            c["sell_trades"]+=1;c["sell_wins"]+=(e["outcome"]=="TP")
            c["sell_pnl"]+=e["pnl_usd"];c["net_pnl"]+=e["pnl_usd"]
        if e["outcome"]=="INSUFFICIENT_MARGIN":c["margin_stop"]=True
    with open("cent_rsi7_m5_50_1000_cycles.csv","w",newline="") as f:
        fields=["cycle","start_utc","last_utc","buy_attempts","sell_trades","sell_wins","sell_pnl","net_pnl","buy_tp","highest_level","margin_stop"]
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for c in cycle_map.values():
            c.setdefault("margin_stop",False)
            w.writerow(c)
    months=collections.OrderedDict()
    for e in events:
        m=months.setdefault(e["month"],{"month":e["month"],"buy_profit":0,"sell_profit":0,"buy_loss":0,"sell_loss":0,"net":0,"trades":0,"wins":0,"ending_balance":0})
        if e["side"] in ("BUY","SELL"):
            key=("buy_" if e["side"]=="BUY" else "sell_")+("profit" if e["pnl_usd"]>=0 else "loss")
            m[key]+=e["pnl_usd"];m["net"]+=e["pnl_usd"];m["trades"]+=1
            if e["side"]=="BUY" and e["outcome"]=="TP":m["wins"]+=1
        m["ending_balance"]=e["balance_usd"]
    fields=["month","buy_profit","sell_profit","buy_loss","sell_loss","net","trades","wins","ending_balance"]
    with open("cent_rsi7_m5_50_1000_monthly.csv","w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for m in months.values():w.writerow({k:round(v,3) if isinstance(v,float) else v for k,v in m.items()})
    with open("cent_rsi7_m5_50_1000_trades.csv","w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["timestamp_utc","month","cycle","level","side","cent_lots","outcome","pnl_usd","balance_usd"])
        w.writeheader();w.writerows(events)
    with open("cent_rsi7_m5_50_1000_summary.txt","w") as f:
        f.write(f"XAUUSD M5 RSI7 crossing below 30, paired cent-lot hedge (sell=half buy)\n")
        f.write(f"Starting balance $1000; starting BUY .02, SELL .01; +/- $50 gold price; doubles after DOWN; simulation only.\n")
        f.write(f"Historical M5 candles {len(bars)} from {bars[0]['time']} to {bars[-1]['time']}\n")
        f.write(f"Cent lot contract {OZ_PER_CENT_LOT} oz; leverage {LEVERAGE}:1; spread approximated; swaps/commission/slippage omitted.\n")
        f.write(f"End balance {balance:.2f}; net PnL {balance-1000:.2f}; cycles {cycles}; completed winners {wins}; max level {max_level}; peak-to-trough realized drawdown {maxdd:.2f}; stopped {paused}; open recovery {state is not None}\n")
        for m in months.values():f.write(str(m)+"\n")
    print(open("cent_rsi7_m5_50_1000_summary.txt").read())
if __name__=="__main__":main()
