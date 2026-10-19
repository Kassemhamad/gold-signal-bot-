"""Fetch a public XAUUSD 5m sample and run TLC-AI historical evaluation.
Date range and sample origin recorded in report. Not a live trading system.
"""
import csv
import io
import json
import urllib.request
from pathlib import Path
from backtest import read_bars,signals,backtest

URL="https://raw.githubusercontent.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data/main/XAUUSD_5m.csv"
raw=urllib.request.urlopen(URL,timeout=60).read().decode("utf-8-sig")
reader=csv.DictReader(io.StringIO(raw))
print("SOURCE HEADERS",reader.fieldnames)
rows=[]
for row in reader:
    key="datetime" if "datetime" in row else "Date" if "Date" in row else "timestamp"
    ts=row[key].strip()
    if not ts.endswith("Z") and "+" not in ts:
        ts+="Z" # data repository declares UTC
    rows.append(dict(timestamp=ts,open=row["open"] if "open" in row else row["Open"],
        high=row["high"] if "high" in row else row["High"],
        low=row["low"] if "low" in row else row["Low"],
        close=row["close"] if "close" in row else row["Close"]))
Path("gold_m5_normalized.csv").write_text("timestamp,open,high,low,close\n"+"\n".join(",".join(r.values()) for r in rows))
bars=read_bars("gold_m5_normalized.csv")
print("BARS",len(bars),"PERIOD",bars[0].ts,bars[-1].ts)
all_signals=signals(bars)
out={"dataset_url":URL,"bars":len(bars),"from":bars[0].ts.isoformat(),"to":bars[-1].ts.isoformat(),
    "candidate_count":len(all_signals),"results":{}}
for spread in [0.20,0.40,0.80]:
    for rr in [2,3]:
        filt=[s for s in all_signals if s["review"]["rr"]>=rr]
        r=backtest(bars,filt,spread=spread,slippage=0.10,risk_fraction=.01)
        out["results"][f"rr{rr}_spread{spread}"]={k:v for k,v in r.items() if k!="trade_log"}
Path("tlc_ai_results.json").write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
