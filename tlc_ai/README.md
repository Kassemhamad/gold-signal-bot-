# TLC-AI — Trend + Liquidity + Entry Review (research prototype)

**Status:** implemented as research code; not deployed, not live-tested, and **no verified win rate**. No live order placement. This feature lives on `feature/tlc-ai-review` so the existing gold bot is not modified.

## Trading idea
- **Market:** XAUUSD; data must be chronologically ordered *5-minute* OHLC candles with UTC timestamps.
- **Trend:** previous fully completed H1 candle, 50 EMA above 200 EMA and rising for longs; reverse for shorts.
- **Liquidity:** M5 low sweeps the prior 12 completed M5 lows and closes back inside; reverse for shorts.
- **Confirmation:** within 3 bars, M5 close breaks a pre-sweep local 5-bar high (bullish) or low (bearish).
- **Entry:** following M5 candle's open.
- **SL:** beyond swept low/high with 0.15 ATR buffer.
- **TP:** previous 48-bar high/low liquidity level. Trade only if the realistic level gives at least 2R; no invented fixed TP.
- **Execution model:** trades simulated using candle highs/lows; if SL and TP touch on the same bar, SL is assumed first. A trade times out after 48 bars. Only one position is open at a time.
- **Risk:** initial balance $30, default risk 1% of then-current balance per trade, capped 3%. Assumes fractional position sizing: broker minimum lot, margin, tick value, swap, commissions and actual contract details are NOT modelled. For a $30 gold account, even 1% risk may be impossible due to minimum lots and margin.

## Run
Requires Python 3.10+. No packages required for the deterministic backtester.

```bash
python tlc_ai/backtest.py --csv gold_m5.csv --out results.json --spread 0.25 --slippage 0.10 --risk 0.01
```

CSV header:
```csv
timestamp,open,high,low,close
2026-01-05T00:00:00Z,4400,4401,4399,4400.5
```

Use a sufficiently long **uninterrupted** real M5 series: 200+ completed H1 bars are needed just to warm up the H1 EMA, plus the strategy warmup. Gaps, DST mistakes, non-M5 timestamps, broker-specific prices and historical bid/ask differences must be checked separately before trusting results. Spread and slippage parameters are GOLD *price points*, not dollars of P&L. Test on a holdout period and walk-forward; do not tune on the same test period.

Results include number of trades, win rate, total R, illustrative ending equity, maximum drawdown and individual trade logs. These are calculated only when a real CSV is supplied.

## Optional AI review (signal-by-signal)

```bash
pip install openai
export OPENAI_API_KEY=... # use a local secret, never commit it
python -m tlc_ai.ai_review --signal signal.json
```

A `signal.json` must contain: `symbol`, `time`, `side`, `entry`, `stop`, `target`, `h1_trend`, `liquidity_sweep`, `m5_structure_shift`, `recent_closed_candles`, `spread`, and `news_status`. The reviewer says ACCEPT, WAIT, or REJECT, and fails closed on missing data or API errors.

**Important limitation:** The AI reviewer is a separate tool, and its accept/reject decisions are **not** included in the deterministic backtest. A valid evaluation requires replaying each historical candidate using only the information available at that candle (no future price/news), recording AI responses and costs, then comparing out-of-sample expectancy and drawdown to the mechanical baseline. AI output is not a calibrated probability. It should never be used to bypass hard risk and liquidity constraints.

## Recommended next steps
1. Import real broker M5 history and verify continuous timestamps and costs.
2. Run deterministic backtest, then 60/40 chronological train/holdout comparison.
3. Add broker contract-size, lot-step and margin-based position sizing for $30 challenge.
4. Record each AI decision on live paper-trading alerts; do not auto-trade real money.
5. Evaluate whether AI review actually improves out-of-sample expectancy.

Not financial advice or a promise that $30 can grow to $1,000.
