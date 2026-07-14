"""Server runtime: owns the pipeline, the feed thread, replay sessions and
the strategy store; assembles the UI frame payloads."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from ..backtest.replay import ReplayEngine
from ..core import Config, EventBus
from ..core.plugins import FEEDS
from ..feeds.base import MarketDataFeed
from ..pipeline import Pipeline
from ..strategy.schema import validate


class Runtime:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.pipeline = Pipeline(config)
        # RLock: bus publishes nest (trade handlers emit signals; frame builds
        # trigger assessments that publish) — a plain Lock would self-deadlock.
        self.lock = threading.RLock()
        self._wrap_bus(self.pipeline.bus)
        self.feed: MarketDataFeed | None = None
        self.feed_thread: threading.Thread | None = None
        self.replay: ReplayEngine | None = None
        self.strategies: dict[str, dict] = {}
        self._strategy_path = Path(config.data_dir) / "strategies.json"
        self._load_strategies()

    # feed thread publishes; frame builder reads — one lock serialises both
    def _wrap_bus(self, bus: EventBus) -> None:
        orig = bus.publish

        def locked_publish(topic, ev):
            with self.lock:
                orig(topic, ev)

        bus.publish = locked_publish  # type: ignore[method-assign]

    # -- feed lifecycle -----------------------------------------------------
    def start_feed(self) -> None:
        cls = FEEDS.get(self.config.feed)
        self.feed = cls(self.pipeline.bus, self.config.symbol,
                        tick_size=self.config.tick_size, **self.config.feed_options)
        self.feed_thread = self.feed.start()

    def stop(self) -> None:
        if self.feed:
            self.feed.stop()
        self.pipeline.learning.save()
        self._save_strategies()

    # -- strategy store ---------------------------------------------------------
    def _load_strategies(self) -> None:
        if self._strategy_path.exists():
            try:
                self.strategies = json.loads(self._strategy_path.read_text())
            except (OSError, json.JSONDecodeError):
                self.strategies = {}

    def _save_strategies(self) -> None:
        self._strategy_path.parent.mkdir(parents=True, exist_ok=True)
        self._strategy_path.write_text(json.dumps(self.strategies, indent=2))

    def put_strategy(self, strategy: dict) -> dict:
        s = validate(dict(strategy))
        self.strategies[s["name"]] = s
        self._save_strategies()
        return s

    # -- UI frame ------------------------------------------------------------------
    def build_frame(self, ts: float, pipeline: Pipeline | None = None) -> dict:
        p = pipeline or self.pipeline
        with self.lock:
            # engines run on market time (feed timestamps), never wall time —
            # live, replay and sim then behave identically and signal decay
            # is measured in market seconds.
            market_ts = p.book_engine.book.ts or ts
            p.capture_frame(market_ts)
            p.maybe_assess(market_ts)
            ts = market_ts
            book = p.book_engine.book
            a = p.fusion.last_assessment
            session = p.liquidity.session
            frame = {
                "type": "frame",
                "ts": ts,
                "symbol": p.config.symbol,
                "tick": p.config.tick_size,
                "mid": book.mid,
                "last": p.book_engine.last_trade_price,
                "dom": {
                    "bids": book.top_bids(18),
                    "asks": book.top_asks(18),
                },
                "heat": p.book_engine.history.as_ui_frames(1)[0] if p.book_engine.history.frames else None,
                "tape": list(p.ui_tape)[-30:],
                "bubbles": list(p.ui_bubbles)[-150:],
                "candles": p.candles.as_ui(90),
                "structure": p.structure.as_ui(),
                "profile": session.session_profile.as_dict(),
                "vwap": session.vwap.value,
                "cum_delta": session.cum_delta_series[-300:],
                "assessment": a.as_dict() if a else None,
                "signals": list(p.ui_signals)[-40:],
                "narration": p.narrator.as_ui(30),
                "broker": p.broker.summary(),
                "risk": p.risk.summary(),
            }
        return frame
