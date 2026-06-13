# BTCUSD 1-Minute Price-Action Scalper

A scalping bot for **BTCUSD on the 1-minute timeframe** built on a pure
**price-action** strategy (no oscillators / lagging indicators). Ships in two
forms that implement the exact same rules:

| File | Purpose |
| --- | --- |
| [`../pine/scalper_btc_priceaction_1m.pine`](../pine/scalper_btc_priceaction_1m.pine) | TradingView Pine v5 `strategy()` - backtest on full history in the Strategy Tester |
| [`scalper_backtest.py`](scalper_backtest.py) | Standalone Python backtest engine (reads an OHLCV CSV) |
| [`fetch_data.py`](fetch_data.py) | Pull fresh BTC-USDT 1m OHLCV into a CSV |
| [`data/btcusdt_1m_sample.csv`](data/btcusdt_1m_sample.csv) | Real Binance BTC-USDT 1m sample used for the result below |

## Strategy: Range-Break Pullback Scalper

Reads raw market structure - the rolling high/low of the last `lookback` bars -
and trades momentum **breakouts confirmed by the breakout candle**:

- **Long**  — `close > highest_high(prev lookback)` **and** a bullish candle
  (`close > open`) that closes in the **upper third** of its own range.
- **Short** — `close < lowest_low(prev lookback)` **and** a bearish candle
  (`close < open`) that closes in the **lower third** of its own range.

Risk is a fixed bracket (typical scalper) plus a time-stop:

| Param | Default | Meaning |
| --- | --- | --- |
| `lookback` | 20 | bars defining the breakout range |
| `TP` | 0.25% | take-profit distance from entry |
| `SL` | 0.15% | stop-loss distance (reward:risk ≈ 1.67:1) |
| `maxBars` | 30 | time-stop — exit at close if neither level hit |
| fees | 0.04%/side | Binance spot taker |
| slippage | 0.01%/side | conservative |

Fills are realistic: a signal on bar *i* is entered at the **open of bar i+1**
(no look-ahead); inside a trade the **stop is checked before the target** within
each bar; only one position at a time.

## Backtest result (real data)

Ran `scalper_backtest.py` on `data/btcusdt_1m_sample.csv` — **200 real Binance
BTC-USDT 1-minute candles**, 2026-06-13 15:51→19:10 UTC (~3.3 h):

```
Bars             : 200  (~199 min = 3.3 h)
Params           : lookback=20  TP=0.25%  SL=0.15%  maxBars=30
Costs            : fee=0.04%/side  slippage=0.01%/side
----------------------------------------------------------------
Trades           : 6  (1W / 5L)
Win rate         : 16.7%
Avg trade        : -0.163%  (net of costs)
Profit factor    : 0.15
Max drawdown     : 0.98%
Total return     : -0.98%
```

**Read of the result:** over this particular window BTC chopped sideways in a
~64.0k–64.3k range, so the breakout logic got repeatedly **whipsawed by false
breakouts** (5 of 6 trades stopped out). This is the expected weakness of a
momentum scalper in a range — it needs trending / expanding-volatility
conditions to be profitable. **A 3.3-hour, 6-trade window is far too small to
judge an edge**; treat it as a wired-up, runnable demonstration on genuine data,
not a verdict on the strategy.

### Same strategy on the 5-minute timeframe

Ran on `data/btcusdt_5m_sample.csv` — **200 real Binance BTC-USDT 5-minute
candles** (~16.6 h), identical parameters:

```
Trades           : 7  (2W / 5L)
Win rate         : 28.6%
Avg trade        : -0.116%  (net of costs)
Profit factor    : 0.30
Max drawdown     : 0.98%
Total return     : -0.81%
```

Marginally better than 1m (higher win rate, fewer false breaks) but still net
negative on this window. The fixed 0.25%/0.15% bracket is tuned for 1m noise; on
5m, candles are ~3-5x larger so the stop is hit inside normal range. For 5m,
scale TP/SL up (e.g. TP 0.6% / SL 0.4%) and/or widen `lookback`.

Run it yourself:
```bash
python3 scalper/scalper_backtest.py scalper/data/btcusdt_5m_sample.csv
```

### Same strategy on the 15-minute timeframe

Ran on `data/btcusdt_15m_sample.csv` — **200 real Binance BTC-USDT 15-minute
candles** (~50 h).

```
Default (TP0.25/SL0.15/LB20/MB30):  7 trades, 28.6% win,  -0.81%
Scaled  (TP0.6 /SL0.4 /LB30/MB60):  3 trades, 66.7% win,  +0.56%   <- best on window
```

Same pattern as 5m: the default 1m-tuned bracket loses; widening TP/SL **and**
lengthening the time-stop + lookback flips this window positive. But it's only
**3 trades over 50h** — almost certainly overfit to this sample, not a real edge.

Run it yourself:
```bash
python3 scalper/scalper_backtest.py scalper/data/btcusdt_15m_sample.csv
python3 scalper/scalper_backtest.py scalper/data/btcusdt_15m_sample.csv --tp 0.6 --sl 0.4 --lookback 30 --maxbars 60
```

For a statistically meaningful result, backtest over weeks/months:

- **TradingView (recommended):** load the `.pine` strategy on a 1m BTCUSD chart
  and read the Strategy Tester — it runs the same rules over full history.
- **Python:** fetch more data, then re-run:
  ```bash
  python3 scalper/fetch_data.py --limit 2000 --out scalper/data/btcusdt_1m.csv
  python3 scalper/scalper_backtest.py scalper/data/btcusdt_1m.csv
  ```

## Higher win-rate: mean-reversion mode + out-of-sample testing

The breakout mode is a *momentum* strategy — it wins ~17-40% of trades (big
winners, many small false breaks). To target **≥50% of trades in profit**, the
backtester also ships a **mean-reversion mode** (`--mode reversion`): it fades a
stretch away from an EMA anchor and takes a small profit as price snaps back —
which structurally wins more often.

```bash
# mean-reversion scalper
python3 scalper/scalper_backtest.py scalper/data/btcusdt_15m_sample.csv --mode reversion

# out-of-sample test: tune on first half, judge on second half (anti-overfit)
python3 scalper/scalper_backtest.py scalper/data/btcusdt_15m_sample.csv --optimize
```

On the real 15m sample, reversion wins **47.8%** of trades full-sample, and the
`--optimize` walk-forward picked a reversion config (`EMA20, dev 0.2%,
TP 0.3% / SL 0.6%`) that stayed **100% win** on both train and test halves.
**But** each test half here is only ~100 bars / 2-3 trades — *nowhere near*
enough to call it an edge. High win rate ≠ profit: with TP<SL you must keep win
rate well above ~66% to stay positive after costs.

### Why "months of 1m data" can't be validated *in this tool* (yet)

This backtester is correct and runs on any CSV, but the data it ships with is
small because of an environment limit: there is **no outbound internet** in the
build sandbox, and price data only arrives 100 candles at a time. One month of
1-minute candles is ~43,000 bars — assembling Nov/Dec/Jan/Feb of minute data
here is not practical. To run the **real multi-month** backtest you asked for:

1. **TradingView (no setup):** load `pine/scalper_btc_priceaction_1m.pine` on a
   1m/3m/5m BTCUSD chart → Strategy Tester reports win rate, profit factor, net
   profit, and max drawdown over full history. This is the fastest path to a
   trustworthy, months-long, multi-timeframe result.
2. **Local Python (full control):** on any machine with internet,
   ```bash
   python3 scalper/fetch_data.py --limit 2000 --out scalper/data/btc_1m.csv
   python3 scalper/scalper_backtest.py scalper/data/btc_1m.csv --optimize
   ```
   `fetch_data.py` paginates as far back as the provider allows, so you can build
   month-long 1m/5m files and run the same out-of-sample optimizer.

> ⚠️ **Reality check.** Nothing here is a validated, "trade-on-my-behalf"
> profitable bot — and no honest backtest on a few hundred bars could be. A
> system worth risking money on needs months of out-of-sample data, realistic
> fee/slippage/funding modelling, and forward (paper) testing first. Treat this
> as a research harness, not trading advice.

## Tuning ideas

- Widen `lookback` (30–50) or require a larger breakout buffer to cut false
  breaks in chop.
- Add a session / volatility filter so it only trades when range is expanding.
- Adjust the TP/SL ratio; the time-stop (`maxBars`) caps exposure per trade.

> Educational backtesting tool. Not financial advice — past performance does not
> guarantee future results.
