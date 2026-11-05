"""Read-only XAUUSD 1-minute backtest. No orders submitted.
October 2026, BUY 09:30 America/New_York on every weekday.
Entry ask; TP/SL $20 gold price; exits from bid.
Start 0.02 cent lots, double after each SL, reset after each TP.
Multiple positions can overlap; each still closes only on TP/SL.
OZ_PER_CENT_LOT default 1; no capital/margin limit modeled.
"""
import csv,datetime as dt,json,os,requests
from zoneinfo import ZoneInfo
NY=ZoneInfo("America/New_York")
UTC=dt.timezone.utc
START=dt.date(2026,10,1)
END=dt.date(2026,11,1)
BASE=.02
OZ=float(os.getenv("OZ_PER_CENT_LOT","1"))
DIST=20.
TOKEN=os.environ["OANDA_API_TOKEN"]
HOST="https://api-fxtrade.oanda.com" if os.getenv("OANDA_ENV","practice")=="live" else "https://api-fxpractice.oanda.com"

def fetch():
    t=dt.datetime(2026,10,1,0,tzinfo=NY).astimezone(UTC)-dt.timedelta(hours=4)
    end=min(dt.datetime.now(UTC).replace(second=0,microsecond=0),dt.datetime(2026,11,15,tzinfo=UTC))
    bars={}
    while t<end:
        nxt=min(t+dt.timedelta(days=2),end)
        r=requests.get(HOST+"/v3/instruments/XAU_USD/candles",
            headers={"Authorization":"Bearer "+TOKEN},
            params={"from":t.isoformat(),"to":nxt.isoformat(),"granularity":"M1","price":"BA"},
            timeout=60)
        r.raise_for_status()
        for c in r.json()["candles"]:
            if not c["complete"]:continue
            ts=dt.datetime.fromisoformat(c["time"].replace("Z","+00:00")).replace(second=0,microsecond=0)
            a=c["ask"];b=c["bid"]
            bars[ts]=(ts,float(a["o"]),float(b["o"]),float(b["h"]),float(b["l"]),float(b["c"]))
        t=nxt
    return [bars[k] for k in sorted(bars)]

def main():
    candles=fetch()
    if not candles:raise RuntimeError("No complete candles returned")
    active=[];ledger=[];last_date=None;next_lot=BASE
    realized=0.;peak=0.;dd_realized=0.;worst_equity=0.;max_total_lots=0.
    def finish(p,t,local,px,is_stop,both):
        nonlocal realized,peak,dd_realized,next_lot
        pnl=(px-p["entry_ask"])*p["lot"]*OZ
        p.update(exit_ny=local.isoformat(),exit_bid=round(px,3),
                 outcome="SL" if is_stop else "TP",pnl_usd=round(pnl,5),
                 hold_hours=round((t-dt.datetime.fromisoformat(p["entry_utc"])).total_seconds()/3600,3),
                 ambiguous_minute=both)
        realized+=pnl
        peak=max(peak,realized);dd_realized=max(dd_realized,peak-realized)
        next_lot=next_lot*2 if is_stop else BASE
        active.remove(p)
    for t,ask_o,bid_o,bid_h,bid_l,bid_c in candles:
        local=t.astimezone(NY)
        candidates=[]
        for p in active:
            stopped=bid_l<=p["sl"];target=bid_h>=p["tp"]
            if not (stopped or target):continue
            px=min(bid_o,p["sl"]) if stopped else p["tp"]
            candidates.append((p,px,stopped,stopped and target))
        # Conservative if open trades exit in the same minute: TP then SL.
        for p,px,stopped,both in sorted(candidates,key=lambda z:(z[2],z[0]["id"])):
            finish(p,t,local,px,stopped,both)
        d=local.date()
        if START<=d<END and d.weekday()<5 and (local.hour,local.minute)==(9,30) and last_date!=d:
            last_date=d;lot=next_lot
            p=dict(id=len(ledger)+1,date=str(d),entry_utc=t.isoformat(),entry_ny=local.isoformat(),
                   lot=round(lot,8),entry_ask=ask_o,tp=ask_o+DIST,sl=ask_o-DIST,
                   entry_spread=round(ask_o-bid_o,3))
            active.append(p);ledger.append(p)
            stopped=bid_l<=p["sl"];target=bid_h>=p["tp"]
            if stopped or target:
                px=min(bid_o,p["sl"]) if stopped else p["tp"]
                finish(p,t,local,px,stopped,stopped and target)
        # Conservative mark-to-market equity lower bound.
        float_low=sum((bid_l-p["entry_ask"])*p["lot"]*OZ for p in active)
        worst_equity=min(worst_equity,realized+float_low)
        max_total_lots=max(max_total_lots,sum(p["lot"] for p in active))
    float_pnl=sum((candles[-1][-1]-p["entry_ask"])*p["lot"]*OZ for p in active)
    summary=dict(period="2026-10-01 through 2026-10-30",last_bar_utc=candles[-1][0].isoformat(),
       candles=len(candles),entries=len(ledger),
       wins=sum(p.get("outcome")=="TP" for p in ledger),
       losses=sum(p.get("outcome")=="SL" for p in ledger),
       still_open=len(active),max_lot=max((p["lot"] for p in ledger),default=0),
       max_simultaneous_lots=round(max_total_lots,5),next_lot=round(next_lot,5),
       realized_usd=round(realized,4),open_float_usd=round(float_pnl,4),
       net_equity_usd=round(realized+float_pnl,4),
       worst_observed_equity_usd=round(worst_equity,4),
       max_realized_drawdown_usd=round(dd_realized,4),
       ambiguous_1min_candles=sum(bool(p.get("ambiguous_minute")) for p in ledger),
       oz_per_cent_lot=OZ)
    fields=["id","date","entry_ny","lot","entry_ask","tp","sl","entry_spread",
            "exit_ny","exit_bid","outcome","pnl_usd","hold_hours","ambiguous_minute"]
    with open("ny_buy_cent_oct2026_trades.csv","w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(ledger)
    with open("ny_buy_cent_oct2026_summary.json","w") as f:json.dump(summary,f,indent=2)
    print(json.dumps(summary,indent=2))
    print("TRADE LEDGER")
    for p in ledger:
        print(p["date"],"lot",p["lot"],p.get("outcome","OPEN"),"pnl_usd",p.get("pnl_usd","open"))
    print("Assumptions: cent lot = ",OZ,"oz; no commission, swaps, slippage beyond stop gaps, leverage/margin limits or liquidation.")
    print("1m same-candle TP/SL is decided as SL. Buy at ask; exit against bid.")

if __name__=="__main__":main()
