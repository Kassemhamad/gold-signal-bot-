"""Read-only export of historical XAUUSD M1 bid/ask candles for independent review."""
import csv,datetime as dt,os,requests
days=int(os.getenv("DAYS","180"))
end=dt.datetime.now(dt.timezone.utc).replace(second=0,microsecond=0)
start=end-dt.timedelta(days=days)
base="https://api-fxpractice.oanda.com" if os.getenv("OANDA_ENV","practice")=="practice" else "https://api-fxtrade.oanda.com"
rows={}
while start<end:
    nxt=min(start+dt.timedelta(days=2),end)
    r=requests.get(base+"/v3/instruments/XAU_USD/candles",headers={"Authorization":"Bearer "+os.environ["OANDA_API_TOKEN"]},params={"from":start.isoformat(),"to":nxt.isoformat(),"granularity":"M1","price":"BA"},timeout=60)
    r.raise_for_status()
    for c in r.json().get("candles",[]):
        if not c.get("complete"):continue
        t=c["time"]
        rows[t]={"timestamp_utc":t,**{s+"_"+k:float(c[x][k]) for x,s in (("bid","bid"),("ask","ask")) for k in ("o","h","l","c")}}
    start=nxt
fields=["timestamp_utc"]+[s+"_"+k for s in ("bid","ask") for k in ("o","h","l","c")]
with open("gold_m1_bidask_history.csv","w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
    for key in sorted(rows):w.writerow(rows[key])
print(f"Exported {len(rows)} complete M1 bid/ask candles across {days} calendar days.")
