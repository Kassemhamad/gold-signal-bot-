"""AEGIS read-only XAUUSD sweep/reclaim study. OANDA candles; no order endpoints."""
import os, csv, json, datetime as dt, requests
from collections import defaultdict
from zoneinfo import ZoneInfo
NY=ZoneInfo("America/New_York")
HOST="https://api-fxpractice.oanda.com" if os.getenv("OANDA_ENV","practice")=="practice" else "https://api-fxtrade.oanda.com"
HEAD={"Authorization":"Bearer "+os.environ["OANDA_API_TOKEN"]}
DAYS=int(os.getenv("BACKTEST_DAYS","180"))
SPREAD=float(os.getenv("ASSUMED_SPREAD","0.30"))
SLIP=float(os.getenv("ASSUMED_SLIPPAGE","0.05"))
def load():
    end=dt.datetime.now(dt.timezone.utc).replace(second=0,microsecond=0)
    t=end-dt.timedelta(days=DAYS); out={}
    while t<end:
        nxt=min(t+dt.timedelta(days=2),end)
        response=requests.get(HOST+"/v3/instruments/XAU_USD/candles",params={"from":t.isoformat(),"to":nxt.isoformat(),"granularity":"M1","price":"MBA"},headers=HEAD,timeout=60)
        response.raise_for_status()
        for c in response.json()["candles"]:
            if not c.get("complete"):continue
            stamp=dt.datetime.fromisoformat(c["time"].replace("Z","+00:00"))
            q=c.get("mid")
            if q:out[stamp]={"t":stamp,**{k:float(q[v]) for k,v in (("o","o"),("h","h"),("l","l"),("c","c"))}}
        t=nxt
    return list(sorted(out.values(),key=lambda x:x["t"]))
def levels(rows,i):
    # All reference ranges must end BEFORE the candidate candle.
    ts=rows[i]["t"].astimezone(NY)
    d=ts.date()
    previous=defaultdict(list)
    for r in rows[max(0,i-5500):i]:
        local=r["t"].astimezone(NY)
        day=(local-dt.timedelta(hours=17)).date()
        previous[day].append(r)
    prior=sorted(k for k,v in previous.items() if k<(ts-dt.timedelta(hours=17)).date() and len(v)>=600)
    lv=[]
    if prior:
        q=previous[prior[-1]]
        lv.extend([("previous_high",max(z["h"] for z in q)),("previous_low",min(z["l"] for z in q))])
    for key,start,end in [("asian",(-1,20),(0,2)),("london",(0,2),(0,8))]:
        a=dt.datetime.combine(d+dt.timedelta(days=start[0]),dt.time(start[1]),NY)
        b=dt.datetime.combine(d+dt.timedelta(days=end[0]),dt.time(end[1]),NY)
        q=[r for r in rows[max(0,i-2300):i] if a<=r["t"].astimezone(NY)<b]
        if len(q)>=120:lv.extend([(key+"_high",max(z["h"] for z in q)),(key+"_low",min(z["l"] for z in q))])
    return lv
def simulate(rows, candidate, variant, rr):
    i=candidate["idx"]; side=candidate["side"]; stop=candidate["stop"]
    if variant=="B":
        j=None
        for k in range(i+5,min(i+7,len(rows))):
            r=rows[k]
            if (side==1 and r["l"]<=candidate["extreme"]) or (side==-1 and r["h"]>=candidate["extreme"]):return None
            if (side==1 and r["c"]>candidate["close"] and r["c"]>r["o"]) or (side==-1 and r["c"]<candidate["close"] and r["c"]<r["o"]):
                j=k+1;break
        if j is None:return None
    else:j=i+5
    if j>=len(rows):return None
    entryclock=rows[j]["t"].astimezone(NY)
    if entryclock.date()!=rows[i]["t"].astimezone(NY).date() or not(dt.time(9,30)<=entryclock.time()<dt.time(11,30)):return None
    entry=rows[j]["o"]+side*(SPREAD/2+SLIP)
    distance=side*(entry-stop)
    if distance<=0 or SPREAD/distance>0.2:return None
    target=entry+side*rr*distance
    exitprice=None; exitreason=None
    for k in range(j,len(rows)):
        r=rows[k]; clock=r["t"].astimezone(NY)
        if clock.date()!=entryclock.date() or clock.time()>=dt.time(12,0):
            exitprice=rows[k-1]["c"]-side*(SPREAD/2+SLIP);exitreason="time";break
        # mid OHLC plus half-spread exit proxy; pessimistic stop-first intrabar ordering
        low=r["l"]-SPREAD/2-SLIP;high=r["h"]-SPREAD/2-SLIP
        if side==-1:low=r["l"]+SPREAD/2+SLIP;high=r["h"]+SPREAD/2+SLIP
        hitstop= low<=stop if side==1 else high>=stop
        hittarget= high>=target if side==1 else low<=target
        if hitstop:exitprice=min(stop,low) if side==1 else max(stop,high);exitreason="stop";break
        if hittarget:exitprice=target;exitreason="target";break
    if exitprice is None:return None
    return {"date":str(entryclock.date()),"level":candidate["level"],"side":"buy" if side==1 else "sell","variant":variant,"rr":rr,"entry":round(entry,4),"stop":round(stop,4),"target":round(target,4),"exit":round(exitprice,4),"reason":exitreason,"net_R":round(side*(exitprice-entry)/distance,5),"entry_time":entryclock.isoformat()}
def main():
    rows=load(); print("Downloaded M1 bars:",len(rows))
    if len(rows)<5000:raise RuntimeError("Not enough 1-minute bars")
    buckets=defaultdict(list)
    for r in rows:
        ny=r["t"].astimezone(NY); buckets[(ny.date(),ny.hour,ny.minute//5)].append(r)
    groups=[q for q in buckets.values() if len(q)==5 and all((q[j]["t"]-q[j-1]["t"]).total_seconds()==60 for j in range(1,5))]
    index={r["t"]:i for i,r in enumerate(rows)}
    candidates=[]; seen=set()
    for q in groups:
        ny=q[0]["t"].astimezone(NY)
        if ny.weekday()>4 or not(dt.time(9,30)<=ny.time()<dt.time(11,25)):continue
        i=index[q[0]["t"]]; past=rows[max(0,i-70):i]
        if len(past)<70:continue
        tr=[max(z["h"]-z["l"],abs(z["h"]-past[j-1]["c"]),abs(z["l"]-past[j-1]["c"])) for j,z in enumerate(past) if j>0]
        atr=sum(tr[-70:])/len(tr[-70:]) *5
        hi=max(z["h"] for z in q);lo=min(z["l"] for z in q);cl=q[-1]["c"]
        for name,lev in levels(rows,i):
            side=1 if name.endswith("low") else -1
            penetration=(lev-lo) if side==1 else (hi-lev)
            reclaimed=cl>lev if side==1 else cl<lev
            if not(reclaimed and 0.10*atr<=penetration<=0.75*atr):continue
            key=(ny.date(),name)
            if key in seen:continue
            seen.add(key)
            extreme=lo if side==1 else hi
            candidates.append({"idx":i,"side":side,"level":name,"extreme":extreme,"close":cl,"stop":extreme-side*.10*atr})
    print("Candidates:",len(candidates))
    import pathlib
    pathlib.Path("aegis_results").mkdir(exist_ok=True)
    report={}
    for variant in "AB":
        for rr in (1.5,2.0):
            alltrades=[t for c in candidates if (t:=simulate(rows,c,variant,rr))]
            selected=[]; byday=defaultdict(list)
            for t in alltrades:byday[t["date"]].append(t)
            for day, ts in sorted(byday.items()):
                ts.sort(key=lambda z:z["entry_time"])
                day_r=0;losses=0
                for t in ts:
                    if len([z for z in selected if z["date"]==day])>=3 or losses>=2 or day_r<=-4:break
                    # 0.25% fixed fraction: daily 1% stop = -4R. Independent portfolios.
                    selected.append(t);day_r+=t["net_R"];losses=losses+1 if t["net_R"]<0 else 0
            key=f"{variant}_{rr}"
            with open("aegis_results/"+key+".csv","w",newline="") as f:
                fields=["date","level","side","variant","rr","entry","stop","target","exit","reason","net_R","entry_time"]
                writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(selected)
            n=len(selected); net=sum(t["net_R"] for t in selected)
            win=sum(t["net_R"]>0 for t in selected)
            report[key]={"trades":n,"wins":win,"win_rate":round(100*win/n,2) if n else None,"total_R":round(net,4),"avg_R":round(net/n,4) if n else None}
    report["metadata"]={"bars":len(rows),"candidates":len(candidates),"days":DAYS,"assumed_spread":SPREAD,"assumed_slippage":SLIP,"warning":"Research proxy: midpoint candles + assumed costs, not tick-accurate fills. No live execution."}
    with open("aegis_results/summary.json","w") as f:json.dump(report,f,indent=2)
    print(json.dumps(report,indent=2))
if __name__=="__main__":main()
