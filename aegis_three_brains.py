"""AEGIS three-brain CONSENSUS research. Deterministic proxies, NOT trained AI; no orders."""
import json, csv, pathlib, datetime as dt
from collections import defaultdict
import aegis_liquidity_sweep as base

def all_candidates(rows):
    buckets=defaultdict(list)
    for r in rows:
        ny=r["t"].astimezone(base.NY)
        buckets[(ny.date(),ny.hour,ny.minute//5)].append(r)
    idx={r["t"]:i for i,r in enumerate(rows)}
    output=[];seen=set()
    for q in buckets.values():
        if len(q)!=5 or any((q[k]["t"]-q[k-1]["t"]).total_seconds()!=60 for k in range(1,5)):continue
        ny=q[0]["t"].astimezone(base.NY)
        if ny.weekday()>4 or not(dt.time(9,30)<=ny.time()<dt.time(11,25)):continue
        i=idx[q[0]["t"]];past=rows[max(0,i-70):i]
        if len(past)<70:continue
        tr=[max(z["h"]-z["l"],abs(z["h"]-past[j-1]["c"]),abs(z["l"]-past[j-1]["c"])) for j,z in enumerate(past) if j>0]
        atr=sum(tr[-70:])/len(tr[-70:])*5
        hi=max(z["h"] for z in q);lo=min(z["l"] for z in q);cl=q[-1]["c"]
        for name,lev in base.levels(rows,i):
            side=1 if name.endswith("low") else -1
            depth=(lev-lo) if side==1 else (hi-lev)
            if not((cl>lev if side==1 else cl<lev) and .10*atr<=depth<=.75*atr):continue
            key=(ny.date(),name)
            if key in seen:continue
            seen.add(key)
            extreme=lo if side==1 else hi
            output.append({"idx":i,"side":side,"level":name,"extreme":extreme,"close":cl,"stop":extreme-side*.1*atr,
                "atr":atr,"depth_ratio":depth/atr,"level_price":lev,"candle_open":q[0]["o"],"candle_high":hi,"candle_low":lo,
                "signal_time":q[-1]["t"].astimezone(base.NY).isoformat()})
    return output

def brains(rows,c):
    i=c["idx"];side=c["side"];lev=c["level"]
    # PROFESSOR: static, hypothesis-based level/time restriction (NOT a learned edge).
    professor=lev.startswith("previous_") or lev.startswith("london_")
    # SCOUT: prior trend/slope must not oppose reversal; only historical completed candles.
    prev=rows[i-1]["c"];old=rows[i-61]["c"]
    scout=(side*(prev-old)>=0)
    # SNIPER: reclaim close in favorable portion of candidate range, and moderate penetration.
    rng=max(c["candle_high"]-c["candle_low"],1e-9)
    close_position=(c["close"]-c["candle_low"])/rng
    sniper=(close_position>=.65 if side==1 else close_position<=.35) and .15<=c["depth_ratio"]<=.60
    return {"professor":professor,"scout":scout,"sniper":sniper}

def selected_portfolio(candidates,rows,variant,rr):
    options=[]
    for c in candidates:
        t=base.simulate(rows,c,variant,rr)
        if t:options.append(t)
    options.sort(key=lambda t:t["entry_time"])
    byday=defaultdict(list)
    for t in options:byday[t["date"]].append(t)
    result=[]
    for day,ts in sorted(byday.items()):
        losses=0; dayr=0
        for t in ts:
            if sum(x["date"]==day for x in result)>=3 or losses>=2 or dayr<=-4:break
            result.append(t)
            dayr+=t["net_R"]; losses=(losses+1 if t["net_R"]<0 else 0)
    return result

def metrics(ts):
    n=len(ts);net=sum(t["net_R"] for t in ts)
    return {"trades":n,"win_rate":round(100*sum(t["net_R"]>0 for t in ts)/n,2) if n else None,
            "total_R":round(net,4),"mean_R":round(net/n,4) if n else None}

def main():
    rows=base.load()
    print("Downloaded minute bars:",len(rows))
    if len(rows)<5000:raise ValueError("Insufficient history")
    allc=all_candidates(rows)
    votes=[(c,brains(rows,c)) for c in allc]
    approved=[c for c,b in votes if all(b.values())]
    print("Total candidates:",len(allc),"unanimously approved:",len(approved))
    out={"metadata":{"bars":len(rows),"candidates":len(allc),"consensus_candidates":len(approved),
        "important":"Rule-based research proxies, NOT LLM brains or statistically trained Professor. Results in-sample, not prospective; trade simulator has OHLC and portfolio limitations."},
        "vote_counts":{k:sum(v[k] for c,v in votes) for k in ("professor","scout","sniper")},
        "variants":{}}
    pathlib.Path("aegis_consensus_results").mkdir(exist_ok=True)
    with open("aegis_consensus_results/votes.csv","w",newline="") as f:
        w=csv.writer(f);w.writerow(["signal_time","level","direction","professor","scout","sniper","all_approve"])
        for c,v in votes:w.writerow([c["signal_time"],c["level"],c["side"],*[v[k] for k in ("professor","scout","sniper")],all(v.values())])
    for variant in ("A","B"):
        for rr in (1.5,2.0):
            base_trades=selected_portfolio(allc,rows,variant,rr)
            new_trades=selected_portfolio(approved,rows,variant,rr)
            key=f"{variant}_{rr}"
            out["variants"][key]={"baseline":metrics(base_trades),"consensus":metrics(new_trades)}
            if new_trades:
                with open("aegis_consensus_results/"+key+".csv","w",newline="") as f:
                    w=csv.DictWriter(f,fieldnames=new_trades[0].keys());w.writeheader();w.writerows(new_trades)
    with open("aegis_consensus_results/summary.json","w") as f:json.dump(out,f,indent=2)
    print(json.dumps(out,indent=2))
if __name__=="__main__":main()
