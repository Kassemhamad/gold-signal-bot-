"""Optional LLM second-opinion for TLC-AI signals.

Research only. This reviewer is NOT used to claim historical performance.
Requires: pip install openai ; export OPENAI_API_KEY=...
Usage: python -m tlc_ai.ai_review --signal signal.json
Fail-closed: unavailable model, invalid JSON, or model disagreement => REJECT.
"""
import argparse
import json
import os
from pathlib import Path

REVIEW_POLICY = """You are a conservative XAUUSD trade reviewer, not a predictor.
Review ONLY the supplied CLOSED candle data and derived features.
Never claim access to live market data.
Reject if data is incomplete, spread/news risk unknown, trend and sweep
are not aligned, stop is invalid, or nearest liquidity target is <2R.
A plausible setup is not evidence of profitability.
Return JSON with keys decision (ACCEPT|WAIT|REJECT), reasons (array of strings),
and confidence (LOW|MEDIUM). Never output HIGH confidence.
"""

def review(payload):
    if not os.getenv("OPENAI_API_KEY"):
        return {"decision":"REJECT","reasons":["OPENAI_API_KEY missing"],"confidence":"LOW"}
    required=("symbol","time","side","entry","stop","target","h1_trend",
              "liquidity_sweep","m5_structure_shift","recent_closed_candles",
              "spread","news_status")
    missing=[k for k in required if k not in payload]
    if missing:
        return {"decision":"REJECT","reasons":["Missing: "+", ".join(missing)],"confidence":"LOW"}
    try:
        from openai import OpenAI
        client=OpenAI()
        response=client.chat.completions.create(
            model=os.getenv("TLC_AI_MODEL","gpt-4.1-mini"),
            messages=[{"role":"system","content":REVIEW_POLICY},
                      {"role":"user","content":json.dumps(payload)}],
            response_format={"type":"json_object"},temperature=0)
        verdict=json.loads(response.choices[0].message.content)
        if verdict.get("decision") not in ("ACCEPT","WAIT","REJECT"):
            raise ValueError("invalid decision")
        return {"decision":verdict["decision"],"reasons":verdict.get("reasons",[]),
                "confidence":verdict.get("confidence","LOW")}
    except Exception as e:
        return {"decision":"REJECT","reasons":["Reviewer failed: "+type(e).__name__],
                "confidence":"LOW"}

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--signal",required=True)
    a=parser.parse_args()
    print(json.dumps(review(json.loads(Path(a.signal).read_text())),indent=2))
