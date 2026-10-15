"""365-day replication of the original reversed previous-day 5m breakout test.
Deliberately preserves the 180-day test's entry, stop/TP and 1440-candle hold rules.
No live trading.
"""
import pandas as pd
import numpy as np
import os
p=os.getenv("DATA_PATH","gold_m1_bidask_history.csv")
df=pd.read_csv(p,parse_dates=["timestamp_utc"]).set_index("timestamp_utc").sort_index()
df.index=df.index.tz_convert("America/New_York")
df["mhigh"]=(df.bid_h+df.ask_h)/2
df["mlow"]=(df.bid_l+df.ask_l)/2
df["mclose"]=(df.bid_c+df.ask_c)/2
nydate=pd.Series(df.index.date,index=df.index)
byday=df.groupby(nydate).agg(high=("mhigh","max"),low=("mlow","min"),n=("mclose","size"))
byday=byday[byday.n>=800]
prev=byday[["high","low"]].shift(1)
prev.columns=["ph","pl"]
prev["mid"]=(prev.ph+prev.pl)/2
bars=df.resample("5min",label="left",closed="left").agg(close=("mclose","last"),n=("mclose","size"))
bars=bars[(bars.n==5)&(bars.index.dayofweek<5)&(bars.index.time>=pd.Timestamp("09:30").time())&(bars.index.time<pd.Timestamp("16:00").time())]
bars["date"]=bars.index.date
bars=bars.join(prev,on="date").dropna()
bars["side"]=np.select([bars.close>bars.ph,bars.close<bars.pl],[1,-1],0)
bars=bars[bars.side!=0].groupby("date",sort=True).head(1)
ix=df.index
bopen=df.bid_o.to_numpy();aopen=df.ask_o.to_numpy()
bl=df.bid_l.to_numpy();bh=df.bid_h.to_numpy()
al=df.ask_l.to_numpy();ah=df.ask_h.to_numpy()
results=[]
for r in bars.itertuples():
    t=r.Index+pd.Timedelta(minutes=5)
    i=ix.searchsorted(t)
    if i>=len(df) or ix[i]!=t:continue
    signal_side=int(r.side)
    side=-signal_side
    entry=aopen[i] if side==1 else bopen[i]
    tp=float(r.mid)
    risk=side*(tp-entry)
    stop=entry-side*risk
    if risk<=0:continue
    last=min(i+1440,len(df))
    outcome="OPEN";pnl=np.nan;exitdate=None
    for j in range(i,last):
        op=bopen[j] if side==1 else aopen[j]
        if j>i:
            if side*(op-stop)<=0:
                outcome="LOSS";pnl=side*(op-entry);exitdate=ix[j];break
            if side*(op-tp)>=0:
                outcome="WIN";pnl=side*(op-entry);exitdate=ix[j];break
        hitstop=(bl[j]<=stop if side==1 else ah[j]>=stop)
        hittp=(bh[j]>=tp if side==1 else al[j]<=tp)
        if hitstop:outcome="LOSS";pnl=-risk;exitdate=ix[j];break
        if hittp:outcome="WIN";pnl=risk;exitdate=ix[j];break
    if outcome=="OPEN":
        j=last-1
        px=df.bid_c.iloc[j] if side==1 else df.ask_c.iloc[j]
        pnl=side*(px-entry);outcome="TIME";exitdate=ix[j]
    results.append(dict(day=str(r.date),signal_side=signal_side,side=side,entry=str(ix[i]),
       price=entry,prev_high=r.ph,prev_low=r.pl,stop=stop,tp=tp,risk=risk,
       outcome=outcome,pnl=pnl,r_multiple=pnl/risk,exit=str(exitdate)))
out=pd.DataFrame(results)
out.to_csv("reversed_prevday_breakout_365_trades.csv",index=False)
lines=[f"Historical candles: {len(df)}",f"Eligible NY dates: {len(byday)}",
       f"Qualifying daily signals: {len(bars)}",f"Simulated trades: {len(out)}"]
if not out.empty:
    counts=out.outcome.value_counts()
    lines += [f"{k}: {int(counts.get(k,0))}" for k in ("WIN","LOSS","TIME")]
    lines += [f"Total R: {out.r_multiple.sum():.3f}",
      f"Gross XAUUSD price units: {out.pnl.sum():.3f}",
      f"Median stop distance: {out.risk.median():.3f}",
      f"Full-win fraction, all trades: {100*(out.outcome=='WIN').mean():.2f}%",
      f"Full-win fraction, TP/SL resolved: {100*counts.get('WIN',0)/max(1,counts.get('WIN',0)+counts.get('LOSS',0)):.2f}%"]
    tab=out.groupby(pd.to_datetime(out.day).dt.to_period("M")).agg(trades=("risk","size"),R=("r_multiple","sum"),wins=("outcome",lambda x:(x=="WIN").sum()))
    lines += ["", "Calendar-month results:",tab.to_string()]
summary="\n".join(lines)+"\n"
print(summary)
open("reversed_prevday_breakout_365_summary.txt","w").write(summary)
