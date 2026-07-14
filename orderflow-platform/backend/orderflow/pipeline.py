"""Pipeline assembly: one call wires every engine to a shared bus.

Live trading, replay and backtesting all run this exact object graph —
the only difference is what pushes events in (feed thread vs. replay loop),
which is the property that makes backtests trustworthy.
"""
from __future__ import annotations

from collections import deque

from .ai.composites import CompositeDetector
from .ai.fusion import SignalFusionEngine
from .ai.learning import LearningCoordinator
from .ai.narrator import Narrator
from .backtest.broker import PaperBroker
from .book.engine import OrderBookEngine
from .core import Config, EventBus, Signal, Topic, TradeEvent
from .liquidity.engine import LiquidityEngine
from .risk.engine import RiskConfig, RiskEngine
from .structure.candles import CandleAggregator
from .structure.engine import MarketStructureEngine


class Pipeline:
    def __init__(self, config: Config | None = None, learning: LearningCoordinator | None = None) -> None:
        self.config = config or Config()
        cfg = self.config
        self.bus = EventBus()
        # Subscription order matters: the book engine must see deltas before
        # detectors read the book, so it is constructed first.
        self.book_engine = OrderBookEngine(self.bus, cfg.symbol, cfg.tick_size, cfg.heatmap_history)
        self.candles = CandleAggregator(self.bus, cfg.symbol, seconds=cfg.candle_seconds)
        self.liquidity = LiquidityEngine(self.bus, self.book_engine.book, cfg.tick_size)
        self.structure = MarketStructureEngine(self.bus, cfg.symbol, cfg.tick_size, cfg.swing_strength)
        self.composites = CompositeDetector(self.bus, self.structure, cfg.symbol, cfg.tick_size)
        self.fusion = SignalFusionEngine(self.bus, cfg.symbol, self.liquidity, self.structure,
                                         cfg.tick_size, cfg.signal_halflife_s)
        self.narrator = Narrator(self.bus, cfg.symbol)
        self.risk = RiskEngine(RiskConfig(
            account_equity=cfg.account_equity,
            risk_per_trade_pct=cfg.risk_per_trade_pct,
            daily_loss_limit_pct=cfg.daily_loss_limit_pct,
            max_position=cfg.max_position,
        ))
        self.broker = PaperBroker(self.risk)
        self.learning = learning or LearningCoordinator(cfg.data_dir)
        # UI mirrors live on the pipeline so live and replay views both work
        self.ui_tape: deque[dict] = deque(maxlen=120)
        self.ui_bubbles: deque[dict] = deque(maxlen=400)
        self.ui_signals: deque[dict] = deque(maxlen=200)
        self._last_assess = 0.0
        self._weights_refreshed = 0.0
        self.bus.subscribe(Topic.TRADE, self._on_trade)
        self.bus.subscribe(Topic.SIGNAL, self._on_signal)

    # -- periodic driving ----------------------------------------------------
    def _on_trade(self, ev: TradeEvent) -> None:
        self.broker.on_trade(ev)
        self.learning.on_price(ev.ts, ev.price)
        row = {"ts": ev.ts, "p": ev.price, "q": ev.size, "s": ev.aggressor.value}
        self.ui_tape.append(row)
        self.ui_bubbles.append(row)
        self.maybe_assess(ev.ts)

    def _on_signal(self, sig: Signal) -> None:
        self.learning.on_signal(sig, regime=self.structure.state.trend)
        self.ui_signals.append(sig.as_dict())

    def maybe_assess(self, ts: float) -> None:
        if ts - self._last_assess < self.config.assessment_interval_s:
            return
        self._last_assess = ts
        self.liquidity.tick(ts)
        a = self.fusion.assess(ts, self.book_engine.book.mid)
        self.learning.on_assessment(ts, self.book_engine.book.mid or 0.0,
                                    a.features, a.control_score, a.p_continuation)
        # periodically fold learned pattern performance back into fusion weights
        if ts - self._weights_refreshed > 300:
            self._weights_refreshed = ts
            self.fusion.weights.update(self.learning.fusion_weights(self.fusion.weights))

    def capture_frame(self, ts: float) -> None:
        self.book_engine.capture_frame(ts, self.config.book_depth_levels)
