"""Research only: previous daily candle direction, ADR(20) TP/SL, reverse and double on stop. No orders."""
import os, csv, datetime as dt, collections, requests, statistics
DAYS=int(os.getenv("BACKTEST_DAYS","365"))
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
    bars=fetch(); days=collections.OrderedDict()
    for i,b in enumerate(bars):days.setdefault(b["t"].date(),[]).append(i)
    ordered=list(days); daily={}
    for day,idx in days.items():
        bb=[bars[j] for j in idx]
        # incomplete daily sessions excluded for signal and ADR
        if len(idx)<18:continue
        daily[day]={"o":bb[0]["o"],"c":bb[-1]["c"],"h":max(x["h"] for x in bb),"l":min(x["l"] for x in bb)}
    triggers={}
    for n,day in enumerate(ordered):
        if n<21 or day not in daily:continue
        prev=ordered[n-1]
        if prev not in daily:continue
        hist=[daily[x]["h"]-daily[x]["l"] for x in ordered[:n] if x in daily][-20:]
        if len(hist)<20:continue
        if daily[prev]["c"]==daily[prev]["o"]:continue
        side=1 if daily[prev]["c"]>daily[prev]["o"] else -1
        triggers[days[day][0]]=(side,round(statistics.mean(hist),3))
    balance=CAPITAL; active=None;fail=False;cycle=0;trades=[];closed_cycles=0;highlevel=0
    for i,b in enumerate(bars):
        if b["t"]<(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=DAYS)):continue
        if active is None:
            if fail or i not in triggers:continue
            side,dist=triggers[i]
            cycle+=1;active={"side":side,"dist":dist,"lot":.01,"level":1,"entry":None}
        if active["entry"] is None:
            # No broker leverage details: require enough balance for the full stop loss.
            required=active["lot"]*UNIT*active["dist"]
            if balance<required:
                trades.append({"time":b["t"].isoformat(),"month":b["t"].strftime("%Y-%m"),"cycle":cycle,"level":active["level"],"direction":"ACCOUNT","lot":active["lot"],"adr":active["dist"],"outcome":"FUNDS_INSUFFICIENT","pnl_usd":0,"balance_usd":balance})
                fail=True;active=None;continue
            active["entry"]=b["o"]
        a=active;side=a["side"];d=a["dist"];entry=a["entry"]
        stop=entry-side*d;tp=entry+side*d
        # adverse first if both levels hit in the same hour; no spread or other costs
        loss=b["l"]<=stop if side==1 else b["h"]>=stop
        win=b["h"]>=tp if side==1 else b["l"]<=tp
        if not (loss or win):continue
        pnl=(-1 if loss else 1)*a["lot"]*UNIT*d
        balance+=pnl;highlevel=max(highlevel,a["level"])
        trades.append({"time":b["t"].isoformat(),"month":b["t"].strftime("%Y-%m"),"cycle":cycle,"level":a["level"],"direction":"BUY" if side==1 else "SELL","lot":round(a["lot"],6),"adr":d,"outcome":"SL" if loss else "TP","pnl_usd":round(pnl,6),"balance_usd":round(balance,6)})
        if win:closed_cycles+=1;active=None
        else:active={"side":-side,"dist":d,"lot":a["lot"]*2,"level":a["level"]+1,"entry":None}
    months=collections.OrderedDict()
    for t in trades:
        m=months.setdefault(t["month"],{"month":t["month"],"buy_tp":0,"sell_tp":0,"stop_losses":0,"failed_accounts":0,"pnl_lbp":0,"ending_balance_lbp":0})
        m["buy_tp"]+=(t["direction"]=="BUY" and t["outcome"]=="TP")
        m["sell_tp"]+=(t["direction"]=="SELL" and t["outcome"]=="TP")
        m["stop_losses"]+=(t["outcome"]=="SL")
        m["failed_accounts"]+=(t["outcome"]=="FUNDS_INSUFFICIENT")
        m["pnl_lbp"]+=t["pnl_usd"]*90000
        m["ending_balance_lbp"]=t["balance_usd"]*90000
    for name,rows in [("daily_color_adr_trades.csv",trades),("daily_color_adr_monthly.csv",list(months.values()))]:
        with open(name,"w",newline="") as f:
            if rows:
                w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    with open("daily_color_adr_summary.txt","w") as f:
        f.write(f"Prior daily candle color; ADR20 full range as fixed TP and SL per cycle; initial .01 lots; reverse and double on SL; 0 costs.\n")
        f.write(f"Data {bars[0]['t']} through {bars[-1]['t']}; bars={len(bars)}. Broker payout 150000 LBP per .01 lot per $10 gold move, conversion 90000 LBP/USD.\n")
        f.write(f"Initial USD {CAPITAL}, final USD {balance:.2f}, net USD {balance-CAPITAL:.2f}; cycles {cycle}, completed TP cycles {closed_cycles}, max level {highlevel}; funding failure {fail}; open cycle {active is not None}. Broker margin unknown.\n")
        for m in months.values():f.write(str(m)+"\n")
    print(open("daily_color_adr_summary.txt").read())
if __name__=="__main__":main()
