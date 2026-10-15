"""Independent one-year HA 5m doji + strong body test; paper only."""
import os,pandas as pd,numpy as np
p=os.getenv("DATA_PATH","gold_m1_bidask_history.csv")
df=pd.read_csv(p,parse_dates=["timestamp_utc"]).set_index("timestamp_utc").sort_index()
df.index=df.index.tz_convert("America/New_York")
for k in "ohlc":df["m"+k]=(df["bid_"+k]+df["ask_"+k])/2
agg={f"m{k}":v for k,v in zip("ohlc",["first","max","min","last"])}
a=df.resample("5min").agg(agg)
a["count"]=df.mo.resample("5min").count()
a=a[a["count"]==5].copy()
o=a.mo.to_numpy();h=a.mh.to_numpy();l=a.ml.to_numpy();c=a.mc.to_numpy()
ho=np.empty(len(a));hc=(o+h+l+c)/4
ho[0]=(o[0]+c[0])/2
for k in range(1,len(a)):
 ho[k]=(o[k]+c[k])/2 if (a.index[k]-a.index[k-1]).total_seconds()>300 else (ho[k-1]+hc[k-1])/2
hh=np.maximum.reduce([h,ho,hc]);hl=np.minimum.reduce([l,ho,hc])
ratio=np.abs(hc-ho)/np.maximum(hh-hl,1e-8)
ix=df.index;bo=df.bid_o.to_numpy();ao=df.ask_o.to_numpy()
bh=df.bid_h.to_numpy();bl=df.bid_l.to_numpy();ah=df.ask_h.to_numpy();al=df.ask_l.to_numpy()
bc=df.bid_c.to_numpy();ac=df.ask_c.to_numpy()
signals=[]
for z in range(1,len(a)):
 t=a.index[z];minute=t.hour*60+t.minute
 if t.weekday()>4 or not 570<=minute<750 or (t-a.index[z-1]).total_seconds()!=300:continue
 if not(ratio[z-1]<=.1 and ratio[z]>=.55):continue
 enter=t+pd.Timedelta(minutes=5);pos=ix.searchsorted(enter)
 if pos>=len(ix) or ix[pos]!=enter:continue
 side=1 if hc[z]>ho[z] else -1
 signals.append((pos,side))
def settle(pos,side):
 entry=ao[pos] if side==1 else bo[pos]
 stop=entry-side*10;tp=entry+side*2
 end=min(pos+1440,len(ix))
 outcome="TIME";price=bc[end-1] if side==1 else ac[end-1];exitidx=end-1
 for j in range(pos,end):
  op=bo[j] if side==1 else ao[j]
  if j>pos and side*(op-stop)<=0:outcome="LOSS";price=op;exitidx=j;break
  if j>pos and side*(op-tp)>=0:outcome="WIN";price=op;exitidx=j;break
  if (bl[j]<=stop if side==1 else ah[j]>=stop):outcome="LOSS";price=stop;exitidx=j;break
  if (bh[j]>=tp if side==1 else al[j]<=tp):outcome="WIN";price=tp;exitidx=j;break
 return {"entry_time":str(ix[pos]),"date":str(ix[pos].date()),"side":"BUY" if side==1 else "SELL",
  "entry":entry,"outcome":outcome,"price_pnl":side*(price-entry),"exit_time":str(ix[exitidx]),"exit_index":exitidx}
trades=[];last=-1
for pos,side in signals:
 if pos<=last:continue
 row=settle(pos,side);last=row.pop("exit_index");trades.append(row)
out=pd.DataFrame(trades)
if out.empty:raise RuntimeError("No trades")
out["period"]=np.where(out.date<"2026-04-19","EARLIER","RECENT")
out.to_csv("ha_doji_one_year_trades.csv",index=False)
def bankroll(x,mode):
 balance=100.;deposits=100.;withdrawals=0.;wins=0;cycles=0;full_losses=0
 for t in x.itertuples():
  ounces=balance/10 if mode=="risk_full" else balance*100/t.entry
  balance+=t.price_pnl*ounces
  if t.outcome=="WIN":wins+=1
  else:wins=0
  if t.outcome=="LOSS":full_losses+=1
  if wins>=5:
   withdrawals+=balance;balance=100.;deposits+=100.;cycles+=1;wins=0
  elif t.outcome=="LOSS" and balance<100:
   deposits+=100-balance;balance=100.
 return {"cycles":cycles,"deposits":round(deposits,2),"withdrawals":round(withdrawals,2),
  "end_balance":round(balance,2),"net":round(withdrawals+balance-deposits,2),"losses":full_losses}
lines=[f"Raw signals: {len(signals)}; sequential trades: {len(out)}",""]
for label,x in [("EARLIER",out[out.period=="EARLIER"]),("RECENT",out[out.period=="RECENT"]),("FULL_YEAR",out)]:
 line=f"{label}: trades={len(x)} wins={(x.outcome=='WIN').sum()} losses={(x.outcome=='LOSS').sum()} price_pnl={x.price_pnl.sum():.2f}"
 lines.append(line)
 for mode in ("risk_full","leverage_100"):
  lines.append(f"  {mode}: {bankroll(x,mode)}")
 lines.append("")
summary="\n".join(lines)
print(summary)
open("ha_doji_one_year_summary.txt","w").write(summary)
