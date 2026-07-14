"""Feed interface. A feed's only job is to publish market data events onto
the bus; everything downstream is feed-agnostic, which is what makes the
platform multi-market (futures/stocks/crypto/forex) by construction."""
from __future__ import annotations

import abc
import threading

from ..core import EventBus


class MarketDataFeed(abc.ABC):
    def __init__(self, bus: EventBus, symbol: str) -> None:
        self.bus = bus
        self.symbol = symbol
        self._stop = threading.Event()

    @abc.abstractmethod
    def run(self) -> None:
        """Block and publish events until stop() is called."""

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name=f"feed-{self.symbol}", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()
