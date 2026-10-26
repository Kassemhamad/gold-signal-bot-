"""Research only: previous daily candle direction, ADR(20) TP/SL, reverse and double on stop. No orders."""
import os, csv, datetime as dt, collections, requests
DAYS=int(os.getenv("BACKTEST_DAYS","365"))
TP=float(os.getenv("TP_GOLD","5"))
REV=float(os.getenv("REVERSE_GOLD","10"))
CAPITAL=float(os.getenv("START_BALANCE","100"))
UNIT=150000/(10*.01*90000)  # broker-stated LBP PnL converted to USD; per lot per gold-price dollar
BASE="https://api-fxpractice.oanda.com" if os.getenv("OANDA_ENV","practice")=="practice" else "https://api-fxtrade.oanda.com"
def fetch():
    end=dt.datetime.now(dt.timezone.utc).replace(minute=0,second=0,microsecond=0)
    cur=end-dt.timedelta(days=DAYS+35);out={}
    while cur<end:
        nxt=min(end,cur+dt.timedelta(days=7))
        r=requests.get(BASE+"/v3/instruments/XAU_USD/candles",headers={"Authorization":"Bearer "+os.environ["OANDA_API_TOKEN"]},
           params={"from":cur.isoformat(),"to":nxt.isoformat(),"granularity":"H1","price":"M"},timeout=60)
        r.raise_for_status()
        for c in r.json().get("candles",[]):
            if not c.get("complete"):continue
            t=dt.datetime.fromisoformat(c["time"].replace("Z","+00:00"))
            out[t]={"t":t,**{k:float(c["mid"][k]) for k in "ohlc"}}
        cur=nxt
    return [out[x] for x in sorted(out)]
def main():
    bars=fetch()
    closes=[b["c"] for b in bars]
    gain=loss=None; changes=[]; prev_rsi=None; signals=set()
    for i in range(1,len(bars)):
        d=closes[i]-closes[i-1]
        u=max(d,0);down=max(-d,0)
        if gain is None:
            changes.append((u,down))
            if len(changes)==7:
                gain=sum(v[0] for v in changes)/7;loss=sum(v[1] for v in changes)/7
        else:
            gain=(gain*6+u)/7;loss=(loss*6+down)/7
        if gain is not None:
            rsi=100 if loss==0 else 100-100/(1+gain/loss)
            if prev_rsi is not None and prev_rsi>30 and rsi<=30:signals.add(i+1)
            prev_rsi=rsi
    balance=CAPITAL; cycle=0;completed=0;failure=False;levelmax=0;active=None;events=[]
    for i,b in enumerate(bars):
        if b["t"] < dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=DAYS):continue
        if active is None:
            if failure or i not in signals:continue
            cycle+=1;active={"side":1,"qty":.02,"level":1,"entry":None}
        if active["entry"] is None:
            risk=active["qty"]*UNIT*REV
            if balance<risk:
                events.append({"month":b["t"].strftime("%Y-%m"),"time":b["t"].isoformat(),"cycle":cycle,"level":active["level"],"side":"ACCOUNT","lot":active["qty"],"outcome":"FUNDS_INSUFFICIENT","pnl_usd":0,"balance_usd":round(balance,4)})
                active=None;failure=True;continue
            active["entry"]=b["o"]
        s=active;direction=s["side"];entry=s["entry"]
        sl=entry-direction*REV;tp=entry+direction*TP
        hit_sl=(b["l"]<=sl if direction==1 else b["h"]>=sl)
        hit_tp=(b["h"]>=tp if direction==1 else b["l"]<=tp)
        if not (hit_tp or hit_sl):continue
        # Stop-first if both touched in same H1 candle.
        win=hit_tp and not hit_sl
        pnl=(TP if win else -REV)*s["qty"]*UNIT
        balance+=pnl;levelmax=max(levelmax,s["level"])
        events.append({"month":b["t"].strftime("%Y-%m"),"time":b["t"].isoformat(),"cycle":cycle,"level":s["level"],"side":"BUY" if direction==1 else "SELL","lot":s["qty"],"outcome":"TP" if win else "SL","pnl_usd":round(pnl,5),"balance_usd":round(balance,5)})
        if win:completed+=1;active=None
        else:
            if balance<=0:
                events.append({"month":b["t"].strftime("%Y-%m"),"time":b["t"].isoformat(),"cycle":cycle,"level":s["level"],"side":"ACCOUNT","lot":s["qty"],"outcome":"BALANCE_EXHAUSTED","pnl_usd":0,"balance_usd":round(balance,5)})
                failure=True;active=None
            else:active={"side":-direction,"qty":s["qty"]*2,"level":s["level"]+1,"entry":None}
    monthly=collections.OrderedDict()
    for e in events:
        a=monthly.setdefault(e["month"],{"month":e["month"],"buy_tp":0,"sell_tp":0,"stops":0,"funding_failures":0,"net_lbp":0,"ending_lbp":0})
        a["buy_tp"]+=e["side"]=="BUY" and e["outcome"]=="TP"
        a["sell_tp"]+=e["side"]=="SELL" and e["outcome"]=="TP"
        a["stops"]+=e["outcome"]=="SL"
        a["funding_failures"]+=e["outcome"] in ("FUNDS_INSUFFICIENT","BALANCE_EXHAUSTED")
        a["net_lbp"]+=e["pnl_usd"]*90000
        a["ending_lbp"]=e["balance_usd"]*90000
    for name,rows in (("h1_rsi_reversal_monthly.csv",list(monthly.values())),("h1_rsi_reversal_trades.csv",events)):
        if rows:
            with open(name,"w",newline="") as f:
                w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    print(f"H1 RSI7 below30; first BUY .02, TP gold {TP}, reverse every adverse gold {REV}, double lots; zero costs. LBP 150000 / .01 / $10 gold, 90000 LBP/USD.")
    print(f"Account USD {CAPITAL}; ending {balance:.2f}; net {balance-CAPITAL:.2f}; completed cycles {completed}; failures {failure}; max level {levelmax}; open {active is not None}")
    print(f"bars {len(bars)} from {bars[0]['t']} through {bars[-1]['t']}")
    for a in monthly.values():print(a)
if __name__=="__main__":main()
