# OrderFlow Terminal

An institutional-grade order flow & liquidity analysis platform — a
Bookmap-class liquidity heatmap fused with an **AI analyst that explains
*why* the market is doing what it's doing**, a market-structure engine,
tick-by-tick replay/backtesting, a no-code strategy builder, an enforced
risk engine and continuous learning from outcomes.

![stack: python + fastapi + canvas](https://img.shields.io/badge/stack-Python%203.11%20·%20FastAPI%20·%20Canvas-blue)

## What it does

| Area | Capabilities |
|---|---|
| **Visualization** | Liquidity heatmap (time × price), volume bubbles, candles + footprint data, DOM ladder, tape, delta histogram, cumulative delta, volume/session profile with POC · Value Area · developing POC, VWAP & anchored VWAP — all streaming in real time |
| **Liquidity engine** | Walls, spoofing, icebergs, absorption, sweeps, book/delta/volume imbalance, large participants, hidden liquidity, pulled/added liquidity, migration, exhaustion — every signal carries a **confidence score and a plain-language explanation** |
| **Market structure** | Swings, BOS, CHOCH, fair value gaps, order/breaker/mitigation blocks, equal highs/lows, liquidity pools, premium/discount zones |
| **Composite AI detections** | Stop hunts, fake breakouts, trapped buyers/sellers, institutional accumulation/distribution — cross-referencing order flow with structure |
| **AI analyst** | Who is in control, institutional bias, P(continuation), P(reversal), trade plans (entry/stop/targets/R:R) with confidence; ask it questions over the API/UI |
| **Learning** | Pattern outcome tracking (hit rate, R:R per signal type & regime) feeds back into fusion weights; online logistic model calibrates continuation probability; AI accuracy is tracked (accuracy, Brier score) |
| **Replay & backtest** | Tick-by-tick replay with pause / speed 0.05×–500× / jump-to-time, trade during replay, AI decisions recorded, AI-vs-trader comparison |
| **Strategy builder** | Strategies as pure JSON (liquidity + delta + VWAP + structure + AI metrics + time filters + risk) — validated, then backtested automatically through the full pipeline |
| **Risk engine** | Position sizing from stop distance, SL/TP, R:R, daily loss limit (enforced — the broker rejects orders), max drawdown, exposure caps |
| **Analytics** | Win rate, profit factor, Sharpe, Sortino, max drawdown, avg trade/duration, best & worst setup, AI accuracy, strategy comparison |

## Quick start

```bash
# local
pip install -r requirements.txt
./scripts/run_dev.sh
# → http://localhost:8720  (simulated exchange with injected institutional behaviours)

# docker
docker compose up --build
```

### Feeds

| Feed | Use |
|---|---|
| `simulated` (default) | Agent-based futures market with **labelled injections** of spoofing, icebergs, absorption, sweeps — the detectors have real behaviour to find, offline |
| `binance` | Live crypto L2 depth + trades (free, no key) — `ORDERFLOW_FEED=binance ORDERFLOW_SYMBOL=BTCUSDT ORDERFLOW_TICK_SIZE=0.01` |
| `replay` | Stream a recorded JSONL session through the live UI |

Record real or simulated sessions for replay/backtests:

```bash
python scripts/record_session.py --feed binance --symbol BTCUSDT --seconds 600 --out data/btc.jsonl
```

## The AI layer, in one example

```bash
curl -X POST localhost:8720/api/ask -d '{"question":"who is in control?"}'
# {"answer": "buyers (score +0.45)", "confidence": 0.71}
```

and in the narration feed:

> *"813 on the buy at 4995.75 pulled after 0.6s with almost no fills — likely
> spoof meant to prop price up; real intent is the other way. (confidence 90%)"*

Every detection explains its evidence; the fusion engine aggregates decayed,
weighted signals into control/bias/probabilities, and the learning module
re-weights signal types by their measured hit rate.

## API surface

- `WS /ws/stream` — full UI frame stream (book, heat, tape, candles, profile, signals, assessment, narration)
- `POST /api/ask` — question → structured answer + confidence
- `GET /api/status · /api/patterns · /api/performance · /api/risk`
- `POST /api/trade · /api/risk/size`
- `GET/POST /api/strategies`, `POST /api/strategies/{name}/backtest`
- `POST /api/replay/load · /api/replay/control · /api/replay/trade`, `GET /api/replay/performance`

## Tests

```bash
cd backend && python -m pytest
```

62 tests cover the order book, every detector (against hand-crafted event
sequences *and* the simulator's labelled injections), market structure,
fusion/narration/learning, the broker, replay determinism, strategy
validation/backtesting, risk enforcement and the API surface.

## Design in 30 seconds

Everything is an **event** on a deterministic bus; every engine is a
subscriber; live trading, replay and backtesting run the *same object
graph* — only the event source differs. Detectors, feeds and strategy
metrics are **plugins**. See [ARCHITECTURE.md](ARCHITECTURE.md) for the
full picture and the scaling path (Rust/GPU hot paths, multi-symbol
processes, persistent stores).

## Roadmap

- [ ] Footprint-chart rendering mode (data already streamed per candle)
- [ ] Rithmic / dxFeed / Polygon connectors for futures & equities L2
- [ ] Persistent tick store (Parquet/Arrow) + date-range replay UI
- [ ] Strategy-builder visual editor on top of the JSON schema
- [ ] GPU heatmap rendering (WebGL) for multi-hour sessions
- [ ] Multi-symbol workspaces & cross-market (options) expansion

**Disclaimer:** research/analysis software; not financial advice. Paper
trading only out of the box.
