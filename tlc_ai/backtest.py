"""TLC-AI research-only trend/liquidity backtester for XAUUSD M5 candles.

CSV columns: timestamp,open,high,low,close (UTC ISO timestamps).
No live orders. No claimed win rate. Flat spread/slippage are expressed as GOLD price.
"""
import argparse
import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

@dataclass
class Bar:
    ts: datetime
    o: float
    h: float
    l: float
    c: float

def read_bars(path):
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            t = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            if t.tzinfo is None:
                raise ValueError("timestamps must have explicit UTC offsets")
            t = t.astimezone(timezone.utc)
            b = Bar(t, *[float(row[k]) for k in ("open", "high", "low", "close")])
            if not (b.l <= min(b.o, b.c) <= max(b.o, b.c) <= b.h):
                raise ValueError("invalid OHLC at " + row["timestamp"])
            rows.append(b)
    rows.sort(key=lambda x: x.ts)
    if len(set(x.ts for x in rows)) != len(rows):
        raise ValueError("duplicate timestamps")
    return rows

def ema(values, n):
    out, v = [], None
    alpha = 2 / (n + 1)
    for x in values:
        v = x if v is None else alpha * x + (1 - alpha) * v
        out.append(v)
    return out

def atr(bars, n=14):
    trs = []
    for i,b in enumerate(bars):
        prior = bars[i-1].c if i else b.c
        trs.append(max(b.h-b.l, abs(b.h-prior), abs(b.l-prior)))
    return ema(trs,n)

def h1_bias(m5):
    """Map each M5 candle to the LAST FINISHED H1 candle: avoids lookahead."""
    groups = {}
    for b in m5:
        hour = b.ts.replace(minute=0,second=0,microsecond=0)
        groups.setdefault(hour, []).append(b)
    hours = sorted(groups)
    closes = [groups[h][-1].c for h in hours]
    e50,e200 = ema(closes,50),ema(closes,200)
    trend = {}
    for j,h in enumerate(hours):
        if j < 201 or len(groups[h]) != 12:
            continue
        if closes[j]>e50[j]>e200[j] and e50[j]>e50[j-1]:
            trend[h]=1
        elif closes[j]<e50[j]<e200[j] and e50[j]<e50[j-1]:
            trend[h]=-1
        else:
            trend[h]=0
    result={}
    for b in m5:
        hour=b.ts.replace(minute=0,second=0,microsecond=0)
        # Bias from previous complete hour, NEVER current incomplete candle.
        i=hours.index(hour)
        result[b.ts]=trend.get(hours[i-1],0) if i else 0
    return result

def review_signal(direction, entry, stop, target, sweep, mss, minimum_rr=2):
    """Mechanical reviewer. Any AI reviewer must be evaluated separately."""
    risk=abs(entry-stop)
    reward=(target-entry)*direction
    reasons=[]
    if risk <= 0 or reward/risk < minimum_rr: reasons.append("insufficient reward/risk")
    if (entry-stop)*direction <= 0: reasons.append("SL wrong side")
    if (entry-sweep)*direction <= 0: reasons.append("sweep not beyond entry")
    if mss is False: reasons.append("structure confirmation missing")
    return {"decision":"REJECT" if reasons else "ACCEPT",
            "rr":round(reward/risk,2) if risk else 0,"reasons":reasons}

def signals(bars, lookback=12, confirm_bars=3, rr=2.5, buffer_atr=.15):
    """Trend aligned sweep -> close back through level -> M5 MSS.
    Signal after closed confirmation candle; enter next candle open.
    Liquidity target = prior 48-candle extreme; minimum RR applies.
    """
    bias=h1_bias(bars)
    volatility=atr(bars)
    output=[]
    cooldown=-1
    for i in range(max(2400,lookback+confirm_bars+50),len(bars)-1):
        if i <= cooldown: continue
        d=bias[bars[i].ts]
        if d == 0: continue
        found=None
        for s in range(i-confirm_bars, i):
            recent=bars[s-lookback:s]
            if d==1 and bars[s].l < min(x.l for x in recent) and bars[s].c>min(x.l for x in recent):
                found=s
            if d==-1 and bars[s].h > max(x.h for x in recent) and bars[s].c<max(x.h for x in recent):
                found=s
        if found is None: continue
        minor=bars[max(found-5,0):found]
        if not minor: continue
        mss = bars[i].c>max(x.h for x in minor) if d==1 else bars[i].c<min(x.l for x in minor)
        if not mss: continue
        # No lookahead: observed signal at i close; executable at i+1 open.
        entry=bars[i+1].o
        sweep=bars[found].l if d==1 else bars[found].h
        stop=sweep-d*volatility[i]*buffer_atr
        past=bars[i-48:i]
        target=max(x.h for x in past) if d==1 else min(x.l for x in past)
        verdict=review_signal(d,entry,stop,target,sweep,mss,rr)
        if verdict["decision"]=="ACCEPT":
            output.append(dict(index=i+1,time=bars[i+1].ts.isoformat(),direction=d,
                entry=entry,stop=stop,target=target,review=verdict))
            cooldown=i+confirm_bars
    return output

def backtest(bars, candidates, spread=0.25, slippage=0.10, max_hold=48, risk_fraction=.01,
             starting_balance=30):
    """Conservative OHLC execution: if SL and TP hit on same bar, SL wins.
    Spread+slippage added as round-trip adverse GOLD price cost.
    One open trade at a time. Results in R units and illustrative equity.
    """
    trades=[]
    balance=starting_balance
    last_exit=-1
    for item in candidates:
        j=item["index"]
        if j<=last_exit: continue
        d=item["direction"]
        entry=item["entry"] + d*(spread/2+slippage/2)
        stop=item["stop"]
        target=item["target"]-d*(spread/2+slippage/2)
        risk=(entry-stop)*d
        if risk<=0 or (target-entry)*d < 2*risk: continue
        exit_price=None
        last=min(len(bars),j+max_hold)
        for k in range(j,last):
            b=bars[k]
            hit_sl=b.l<=stop if d==1 else b.h>=stop
            hit_tp=b.h>=target if d==1 else b.l<=target
            if hit_sl: exit_price=stop
            elif hit_tp: exit_price=target
            if exit_price is not None:
                last_exit=k
                break
        if exit_price is None:
            last_exit=last-1
            exit_price=bars[last_exit].c-d*(spread/2+slippage/2)
        realized_r=(exit_price-entry)*d/risk
        pnl=balance*risk_fraction*realized_r
        balance=max(0,balance+pnl)
        trades.append(dict(time=item["time"],side="BUY" if d==1 else "SELL",
            entry=round(entry,3),stop=round(stop,3),target=round(target,3),
            R=round(realized_r,3),equity=round(balance,2)))
        if balance==0:break
    wins=sum(x["R"]>0 for x in trades)
    peak=starting_balance
    drawdown=0
    for t in trades:
        peak=max(peak,t["equity"])
        drawdown=max(drawdown,(peak-t["equity"])/peak)
    return {"trades":len(trades),"win_rate":round(wins/len(trades),4) if trades else None,
            "net_R":round(sum(x["R"] for x in trades),2),
            "max_drawdown_pct":round(drawdown*100,2),
            "ending_balance":round(balance,2),"trade_log":trades}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--csv",required=True)
    p.add_argument("--out",default="results.json")
    p.add_argument("--spread",type=float,default=.25)
    p.add_argument("--slippage",type=float,default=.10)
    p.add_argument("--risk",type=float,default=.01)
    args=p.parse_args()
    if not 0 < args.risk <= .03: p.error("--risk must be greater than 0 and <= .03")
    bars=read_bars(args.csv)
    found=signals(bars)
    result=backtest(bars,found,spread=args.spread,slippage=args.slippage,risk_fraction=args.risk)
    Path(args.out).write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!="trade_log"},indent=2))

if __name__=="__main__":
    main()
