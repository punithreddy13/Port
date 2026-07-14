# Architecture

## Principles

1. **Event-driven, deterministic core.** All market data becomes typed
   events (`BookSnapshot`, `BookDelta`, `TradeEvent`) on a synchronous
   `EventBus`. Handlers run in subscription order on the publisher's
   thread — replaying a recorded stream reproduces every downstream signal
   bit-for-bit. Concurrency lives at the edges (feed threads, the async
   server), never inside the analytical core.

2. **One pipeline for every mode.** `Pipeline` wires the object graph:
   book engine → candle aggregator → liquidity engine → structure engine →
   composites → AI fusion → narrator → learning → broker/risk. Live
   trading, interactive replay and headless backtests construct the same
   graph and differ only in what pushes events in. This is what makes
   backtest results transferable to live behaviour.

3. **Explanations are part of the contract.** A `Signal` cannot exist
   without `confidence` and `explanation`. The UI, the narrator, the
   fusion reasons and the learning layer all consume the same object.

4. **Plugins over branches.** Detectors, feeds and strategy metrics
   register in `core/plugins.py` registries. Adding a venue or a detector
   is a new module + registration, no core edits.

```
 feeds/ (thread)          core/EventBus            server/ (asyncio)
 ┌───────────────┐   BOOK_*, TRADE   ┌──────────────────────────────┐
 │ simulated     │ ────────────────▶ │ book/OrderBookEngine + history│──▶ heatmap frames
 │ binance       │                   │ structure/CandleAggregator    │──▶ candles/footprint
 │ replay        │                   │ liquidity/LiquidityEngine     │──▶ SIGNAL events
 └───────────────┘                   │ structure/MarketStructureEngine│─▶ SIGNAL events
        ▲                            │ ai/CompositeDetector          │──▶ SIGNAL events
        │ JSONL                      │ ai/SignalFusionEngine         │──▶ ASSESSMENT
 feeds/recorder ◀────────────────────│ ai/Narrator                   │──▶ NARRATION
                                     │ ai/LearningCoordinator        │──▶ weight updates
                                     │ backtest/PaperBroker ◀ risk/  │──▶ fills, P&L
                                     └──────────────────────────────┘
                                                │ WS /ws/stream + REST
                                                ▼
                                     frontend/ (canvas, no deps)
```

## Module map

| Path | Responsibility |
|---|---|
| `core/events.py` | Event & signal taxonomy (the platform's vocabulary) |
| `core/bus.py` | Deterministic pub/sub |
| `core/models.py` | `OrderBook` (tick-rounded L2, cached sorted views), `RollingStats` (O(1) z-scores) |
| `core/plugins.py` | Registries: detectors, feeds, strategy metrics |
| `core/config.py` | Dataclass config; JSON file + `ORDERFLOW_*` env overrides |
| `book/` | Live book maintenance + time×price `BookHistory` (heatmap & liquidity-evolution queries) |
| `feeds/` | Simulator (agent-based, labelled injections), Binance L2 connector, JSONL recorder, replay feed |
| `liquidity/` | 10 detector classes + session analytics (profiles, VWAP, cumulative delta) |
| `structure/` | Candles w/ footprints; swings, BOS/CHOCH, FVG, order/breaker/mitigation blocks, pools, premium/discount |
| `ai/composites.py` | Cross-engine detections (stop hunt, fake breakout, trapped traders, acc/dist) |
| `ai/fusion.py` | Decayed weighted evidence → control, bias, probabilities, trade plan; Q&A interface |
| `ai/narrator.py` | Signal/assessment → human narration with change-driven throttling |
| `ai/learning.py` | PatternStats (per-type hit rates → weight updates) + online logistic model; JSON persistence |
| `backtest/` | PaperBroker (fills, stops/targets, position, risk-gated), ReplayEngine (transport controls, AI decision log) |
| `strategy/` | JSON schema + validation, condition evaluator, auto-backtester |
| `risk/` | Sizing maths + enforced account-level limits |
| `analytics/` | Trade-list metrics & group comparison (AI vs trader, strategy vs strategy) |
| `server/` | FastAPI: REST control plane, WS streaming plane, frame builder |
| `frontend/` | Dependency-free canvas terminal (heatmap, DOM, tape, delta, profile, AI panels) |

## Key mechanics

**Statistical thresholds, not magic numbers.** Detectors baseline
everything with rolling z-scores (`RollingStats`), so the same code works
across instruments with wildly different sizes — a "wall" is *n×* the
rolling mean level size, a "block trade" is a 4σ print.

**Liquidity evolution, not snapshots.** `BookHistory` keeps signed
depth frames; detectors query how levels *changed* (spoof pull vs
consumed wall, migration of depth centre-of-mass), which is the actual
institutional-detection edge over DOM-only tools.

**Market time everywhere.** Engines run on feed timestamps, never wall
clocks. Signal decay, assessment intervals and rate limits are measured in
market seconds, so replay at 64× behaves identically to live.

**Learning loop.** Every directional signal is scored against the
forward move over a horizon (hit rate, MFE/MAE). Aggregates update fusion
weights (shrunk toward priors at low sample counts); an online logistic
model learns continuation probability from assessment features. Both
persist as JSON in `data/` and improve across sessions.

**Risk is enforced, not advisory.** `PaperBroker.submit` consults
`RiskEngine.allow_order`; breaching the daily loss limit halts trading at
the broker, not in the UI.

## Threading model

- Feed thread publishes market events.
- The analytical core is single-threaded-per-pipeline by design.
- The server serialises feed publishing and frame building with one
  re-entrant lock (publishes nest: trade → signal → assessment).
- The asyncio loop only does fan-out (WS broadcast) and replay pacing.

## Scaling path (deliberate seams)

- **Hot path → native:** `OrderBook`/`BookHistory` and detector loops are
  isolated behind small interfaces; port to Rust/Cython without touching
  engines. GPU (WebGL) rendering slots into the frontend heatmap only.
- **Multi-symbol:** one `Pipeline` per symbol per process; the bus is
  process-local, so horizontal scaling is process-per-market with a thin
  router in front.
- **Storage:** JSONL is the interchange format; swap `DataRecorder` for a
  Parquet/Arrow writer behind the same `encode/decode` pair.
- **Feeds:** implement `MarketDataFeed.run()` and register — Rithmic,
  dxFeed, IBKR, Polygon connectors are additive.

## Testing strategy

- Hand-crafted event sequences assert each detector's *exact* trigger
  geometry (e.g. spoof = appear off-touch → pulled unfilled within TTL).
- The simulator injects labelled behaviours (`injected_log`) and
  end-to-end tests assert the engines find them — ground truth without
  recorded data.
- Replay determinism is asserted (same events → identical downstream state).
- The API surface is tested against a cold pipeline (no feed), catching
  order-of-initialisation bugs.
