"""READ-ONLY paired SMA100 test. Original entry schedule; no forced session close."""
import csv
from collections import Counter
from zoneinfo import ZoneInfo
import ny_gold_sma100_fixed_entries_compare as prior
import ny_gold_1m_three_trades_sma100_rr1 as base

NY=ZoneInfo("America/New_York")
PREFIX="ny_gold_sma100_reversed_hold_until_sl_tp"

def exit_from(bars, index, side):
    start=bars[index]
    entry=start["ao"] if side==1 else start["bo"]
    stop=entry-10*side
    target=entry+10*side
    for j in range(index,len(bars)):
        b=bars[j]
        # Check executable opening quote at every bar, including Sunday reopening
        # or market gaps; do not assume a stop fills at its requested level.
        opening=b["bo"] if side==1 else b["ao"]
        if j>index:
            if (side==1 and opening<=stop) or (side==-1 and opening>=stop):
                return "LOSS",round(side*(opening-entry),3),j
            if (side==1 and opening>=target) or (side==-1 and opening<=target):
                return "WIN",round(side*(opening-entry),3),j
        stopped=(b["bl"]<=stop) if side==1 else (b["ah"]>=stop)
        targeted=(b["bh"]>=target) if side==1 else (b["al"]<=target)
        # Pessimistic if both levels touched within one M1 candle.
        if stopped:return "LOSS",-10.0,j
        if targeted:return "WIN",10.0,j
    return "OPEN",None,None

def main():
    bars=base.fetch_bars()
    prior.compute_sma(bars)
    time_index={b["t"]:i for i,b in enumerate(bars)}
    grouped={}
    for b in bars:
        ny=b["t"].astimezone(NY)
        if ny.weekday()<5:grouped.setdefault(ny.date(),[]).append(b)
    rows=[]
    for date,group in sorted(grouped.items()):
        # Preserve the exact original 3-per-day signal timestamps used by the prior test.
        original_entries=base.test_day(date,group)
        by_clock={b["t"].astimezone(NY).strftime("%H:%M"):b for b in group}
        for sequence,trade in enumerate(original_entries,1):
            clock=trade["entry_time_ny"]
            bar=by_clock[clock]
            idx=time_index[bar["t"]]
            side=1 if trade["side"]=="BUY" else -1
            original=exit_from(bars,idx,side)
            reverse=exit_from(bars,idx,-side)
            def stamp(result):
                return bars[result[2]]["t"].astimezone(NY).isoformat() if result[2] is not None else ""
            rows.append(dict(date=str(date),trade=sequence,entry_ny=bar["t"].astimezone(NY).isoformat(),
                original_side=trade["side"],original_result=original[0],original_pnl=original[1],
                original_exit=stamp(original),reversed_side="SELL" if side==1 else "BUY",
                reversed_result=reverse[0],reversed_pnl=reverse[1],reversed_exit=stamp(reverse)))
    columns=["date","trade","entry_ny","original_side","original_result","original_pnl","original_exit",
             "reversed_side","reversed_result","reversed_pnl","reversed_exit"]
    with open(PREFIX+".csv","w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    def summary(side):
        outcomes=Counter(x[side+"_result"] for x in rows)
        total=sum(x[side+"_pnl"] for x in rows if x[side+"_pnl"] is not None)
        return outcomes,total
    o,opnl=summary("original")
    r,rpnl=summary("reversed")
    text=(f"SMA100 1:1 matched-entry reversed-versus-original, hold until SL/TP\n"
        f"Historical calendar days requested: {base.DAYS}\n"
        f"Original SMA100 entry timestamps: {len(rows)}\n"
        f"Positions allowed to overlap and carry overnight/weekend.\n"
        f"Original: {o['WIN']} wins, {o['LOSS']} losses, {o['OPEN']} still open; realized P/L {opnl:.3f}\n"
        f"Reversed: {r['WIN']} wins, {r['LOSS']} losses, {r['OPEN']} still open; realized P/L {rpnl:.3f}\n"
        f"Reversed minus original realized result: {rpnl-opnl:+.3f} price units\n"
        f"No time exits; open trades excluded from realized P/L.\n"
        f"Gap fills at next available bid/ask opening quote; slippage across gaps may exceed SL.\n"
        f"Both-side intraminute ties go to stop; bid/ask candles; no fee, financing, or commission.\n"
        f"Positions left open beyond available historical data cannot be resolved.\n")
    print(text)
    with open(PREFIX+"_summary.txt","w") as f:f.write(text)

if __name__=="__main__":main()
