"""Reverse the original NY 09:30 M5 candle signal.
Green => SELL, TP at opening low, SL equidistant above entry.
Red => BUY, TP at opening high, SL equidistant below entry.
No SMA filter. Uses same source, time window and cost assumptions as baseline.
"""
import json
from pathlib import Path
from datetime import time
from collections import defaultdict
from zoneinfo import ZoneInfo
from ny_open_backtest import download, URL

NY=ZoneInfo("America/New_York")
def test(bars,spread=.2,slippage=.1,max_bars=78):
    dates=defaultdict(list)
    for i,b in enumerate(bars):
        ny=b.ts.astimezone(NY)
        if ny.time()==time(9,30):
            dates[ny.date()].append((i,b))
    equity=30.;peak=30.;dd=0.;trades=[]
    for day,opens in sorted(dates.items()):
        i,b=opens[0]
        if i+1>=len(bars):continue
        nxt=bars[i+1]
        if nxt.ts.astimezone(NY).date()!=day or nxt.ts.astimezone(NY).time()!=time(9,35):continue
        if b.c==b.o:continue
        side=-1 if b.c>b.o else 1
        entry=nxt.o+side*(spread+slippage)/2
        # Exit on the opposing opening-candle wick; risk mirrored at 1:1.
        tp=b.l if side==-1 else b.h
        reward=(tp-entry)*side
        if reward<=0:continue
        sl=entry-side*reward
        exit_price=None;reason=None;last=None
        for j in range(i+1,min(len(bars),i+1+max_bars)):
            bar=bars[j]
            if bar.ts.astimezone(NY).date()!=day:break
            hit_sl=bar.h>=sl if side==-1 else bar.l<=sl
            hit_tp=bar.l<=tp if side==-1 else bar.h>=tp
            if hit_sl:
                exit_price=sl;reason="SL";last=j;break
            if hit_tp:
                # Round trip cost: unfavorable target fill
                exit_price=tp-side*(spread+slippage)/2;reason="TP";last=j;break
            last=j
        if exit_price is None:
            if last is None:continue
            exit_price=bars[last].c-side*(spread+slippage)/2
            reason="TIME"
        R=side*(exit_price-entry)/reward
        equity*=1+.01*R
        peak=max(peak,equity);dd=max(dd,(peak-equity)/peak)
        trades.append(dict(date=str(day),side="SELL" if side==-1 else "BUY",
            entry=round(entry,3),tp=round(tp,3),sl=round(sl,3),
            outcome=reason,R=round(R,4),equity=round(equity,2)))
    wins=sum(t["R"]>0 for t in trades)
    return dict(trades=len(trades),wins=wins,losses=len(trades)-wins,
        win_rate_pct=round(100*wins/len(trades),2) if trades else None,
        total_R=round(sum(t["R"] for t in trades),2),
        ending_balance=round(equity,2),max_drawdown_pct=round(dd*100,2),
        outcomes={k:sum(t["outcome"]==k for t in trades) for k in ("SL","TP","TIME")},
        trade_log=trades)

if __name__=="__main__":
    bars=download()
    data={"data_source":URL,"period":[bars[0].ts.isoformat(),bars[-1].ts.isoformat()],
      "rules":"green => SELL to opening candle low, red => BUY to opening candle high; SL equal distance; entry at next M5 open; max 78 M5 bars",
      "scenarios":{}}
    for spread in (.2,.4,.8):
        r=test(bars,spread=spread,slippage=.1)
        data["scenarios"][str(spread)]=r
        print(json.dumps({"spread":spread,**{k:v for k,v in r.items() if k!="trade_log"}},indent=2))
    Path("ny_reverse_results.json").write_text(json.dumps(data,indent=2))
