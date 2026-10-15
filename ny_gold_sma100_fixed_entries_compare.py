"""Paired fixed-entry historical comparison; READ ONLY, does not place orders.
Entry timestamps are determined only by the original SMA100 / 1:1 strategy.
Each timestamp is simulated both originally and reversed, without reselecting signals.
"""
import csv
import os
from collections import Counter
from zoneinfo import ZoneInfo
import ny_gold_1m_three_trades_sma100_rr1 as base

NY=ZoneInfo("America/New_York")
PREFIX="ny_gold_sma100_fixed_entries_compare"

def compute_sma(bars):
    window=[]
    total=0.0
    previous_time=None
    for bar in bars:
        if previous_time and (bar["t"]-previous_time).total_seconds()!=60:
            window=[]
            total=0.0
        value=base.mid(bar,"c")
        window.append(value)
        total+=value
        if len(window)>100:
            total-=window.pop(0)
        bar["sma100"]=total/100 if len(window)==100 else None
        previous_time=bar["t"]

def simulate(bars,index,side):
    first=bars[index]
    entry=first["ao"] if side==1 else first["bo"]
    stop=entry-10*side
    target=entry+10*side
    exit_index=index
    status=None
    pnl=None
    for j in range(index,len(bars)):
        candle=bars[j]
        if candle["t"].astimezone(NY).strftime("%H:%M")>=base.END:
            break
        if j>index and (candle["t"]-bars[j-1]["t"]).total_seconds()!=60:
            break
        stop_hit=candle["bl"]<=stop if side==1 else candle["ah"]>=stop
        target_hit=candle["bh"]>=target if side==1 else candle["al"]<=target
        if stop_hit:
            status,pnl="LOSS",-10.0
        elif target_hit:
            status,pnl="WIN",10.0
        if status:
            exit_index=j
            break
    if status is None:
        j=min(j,len(bars)-1)
        exit_index=max(index,j-1 if bars[j]["t"].astimezone(NY).strftime("%H:%M")>=base.END else j)
        last=bars[exit_index]
        close=last["bc"] if side==1 else last["ac"]
        status,pnl="SESSION_OR_GAP",side*(close-entry)
    return {"status":status,"pnl":round(pnl,3),"entry":round(entry,3),
            "exit_ny":bars[exit_index]["t"].astimezone(NY).strftime("%H:%M")}

def main():
    bars=base.fetch_bars()
    compute_sma(bars)
    grouped={}
    for bar in bars:
        local=bar["t"].astimezone(NY)
        if local.weekday()<5:
            grouped.setdefault(local.date(),[]).append(bar)
    rows=[]
    for day,group in sorted(grouped.items()):
        original=base.test_day(day,group)  # original strategy selects up to 3 sequential signals
        index_by_clock={b["t"].astimezone(NY).strftime("%H:%M"):i for i,b in enumerate(group)}
        for number,trade in enumerate(original,1):
            entry_clock=trade["entry_time_ny"]
            index=index_by_clock[entry_clock]
            prior=group[index-3:index]
            side=base.signal(*prior)
            assert side!=0, "Selected timestamp has no original pattern"
            sma=prior[-1]["sma100"]
            assert sma is not None and (side*(base.mid(prior[-1],"c")-sma)>0)
            normal=simulate(group,index,side)
            reverse=simulate(group,index,-side)
            # Assert original simulator exactly reproduces the original trade.
            assert normal["status"]==trade["outcome"], (normal,trade)
            assert abs(normal["pnl"]-trade["gross_price_pnl"])<0.002,(normal,trade)
            rows.append({"date":str(day),"trade_num":number,"entry_ny":entry_clock,
              "original_signal":"BUY" if side==1 else "SELL",
              "original_exit_ny":normal["exit_ny"],"original_result":normal["status"],
              "original_pnl":normal["pnl"],"reverse_action":"SELL" if side==1 else "BUY",
              "reverse_exit_ny":reverse["exit_ny"],"reverse_result":reverse["status"],
              "reverse_pnl":reverse["pnl"],"paired_difference":round(reverse["pnl"]-normal["pnl"],3)})
    columns=list(rows[0]) if rows else []
    with open(PREFIX+".csv","w",newline="") as f:
        if columns:
            w=csv.DictWriter(f,fieldnames=columns)
            w.writeheader()
            w.writerows(rows)
    def aggregate(prefix):
        count=Counter(r[prefix+"_result"] for r in rows)
        total=sum(r[prefix+"_pnl"] for r in rows)
        return count,total
    o,original_pnl=aggregate("original")
    r,reversed_pnl=aggregate("reverse")
    summary=(f"XAUUSD SMA100 1:1 matched-entry reverse comparison\n"
      f"Days requested: {base.DAYS}\n"
      f"Entry times: locked from original SMA100 3 sequential trade/day model\n"
      f"Entries tested: {len(rows)}\n"
      f"Original: wins {o['WIN']}, losses {o['LOSS']}, session/gap {o['SESSION_OR_GAP']}, total {original_pnl:.3f}\n"
      f"Reversed SAME timestamps: wins {r['WIN']}, losses {r['LOSS']}, session/gap {r['SESSION_OR_GAP']}, total {reversed_pnl:.3f}\n"
      f"Reversed minus original: {reversed_pnl-original_pnl:+.3f} price units\n"
      f"No breakeven; SL 10 and TP 10 for both sides. OANDA bid/ask M1.\n"
      f"All paired positions are simulated independently; reversed trades may overlap.\n"
      f"Intraminute sequence unknown, stop-first ties; excludes fees and slippage.\n"
      f"Gold price units are not account USD unless lot contract size is specified.\n")
    print(summary)
    with open(PREFIX+"_summary.txt","w") as f:f.write(summary)

if __name__=="__main__":
    main()
