"""Replay feed: streams a recorded JSONL file through the bus in real time
(with speed control) so the full live pipeline — including the UI — can run
against historical data. Tick-accurate control lives in backtest.replay;
this feed is the "watch history live" mode."""
from __future__ import annotations

import time

from ..core import EventBus
from ..core.plugins import FEEDS
from .base import MarketDataFeed
from .recorder import load_events


@FEEDS.register("replay")
class ReplayFeed(MarketDataFeed):
    def __init__(self, bus: EventBus, symbol: str, path: str = "", speed: float = 1.0, **_: object) -> None:
        super().__init__(bus, symbol)
        self.path = path
        self.speed = speed

    def run(self) -> None:
        events = load_events(self.path)
        if not events:
            return
        prev_ts = events[0][1].ts
        for topic, ev in events:
            if self.stopped:
                return
            gap = max(0.0, ev.ts - prev_ts)
            if gap > 0:
                time.sleep(min(gap / max(self.speed, 1e-6), 2.0))
            prev_ts = ev.ts
            self.bus.publish(topic, ev)
