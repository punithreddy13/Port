"""LiquidityEngine: routes book/trade events into every registered detector,
maintains volume analytics, and publishes resulting Signals on the bus."""
from __future__ import annotations

from ..core import BookDelta, EventBus, Side, Signal, Topic, TradeEvent
from ..core.models import OrderBook
from ..core.plugins import DETECTORS
from . import detectors as _detectors  # noqa: F401 - registers built-ins
from .detectors import BookContext, Detector
from .profile import SessionTracker


class LiquidityEngine:
    def __init__(self, bus: EventBus, book: OrderBook, tick_size: float,
                 enabled: list[str] | None = None) -> None:
        self.bus = bus
        self.book = book
        self.session = SessionTracker(tick_size)
        names = enabled if enabled is not None else DETECTORS.names()
        self.detectors: list[Detector] = [DETECTORS.get(n)(tick_size) for n in names]
        self.signals_emitted = 0
        self._prev_sizes: dict[tuple[str, float], float] = {}
        bus.subscribe(Topic.BOOK_DELTA, self._on_delta)
        bus.subscribe(Topic.TRADE, self._on_trade)

    def _ctx(self, ts: float) -> BookContext:
        return BookContext(book=self.book, ts=ts)

    def _emit(self, signals: list[Signal]) -> None:
        for s in signals:
            self.signals_emitted += 1
            self.bus.publish(Topic.SIGNAL, s)

    def _on_delta(self, ev: BookDelta) -> None:  # type: ignore[override]
        # OrderBookEngine subscribes first, so self.book is already updated;
        # detectors need the previous size, which we track here.
        key = (ev.side.value, self.book.round_price(ev.price))
        prev = self._prev_sizes.get(key, 0.0)
        new = ev.size
        if new <= 0:
            self._prev_sizes.pop(key, None)
        else:
            self._prev_sizes[key] = new
        ctx = self._ctx(ev.ts)
        for d in self.detectors:
            self._emit(d.on_delta(ctx, ev.side, self.book.round_price(ev.price), new, prev))

    def _on_trade(self, ev: TradeEvent) -> None:  # type: ignore[override]
        self.session.add_trade(ev.ts, ev.price, ev.size, ev.aggressor)
        ctx = self._ctx(ev.ts)
        for d in self.detectors:
            self._emit(d.on_trade(ctx, ev.price, ev.size, ev.aggressor))

    def tick(self, ts: float) -> None:
        """Periodic pass for time-based detectors (imbalance, migration...)."""
        ctx = self._ctx(ts)
        for d in self.detectors:
            self._emit(d.on_tick(ctx))

    # -- queries used by AI layer & strategies ------------------------------
    def features(self, ts: float) -> dict[str, float]:
        book = self.book
        bid_d = book.depth(Side.BUY, 10)
        ask_d = book.depth(Side.SELL, 10)
        total = bid_d + ask_d
        return {
            "bid_depth": bid_d,
            "ask_depth": ask_d,
            "book_imbalance": (bid_d - ask_d) / total if total > 0 else 0.0,
            "spread_ticks": (book.spread or 0.0) / book.tick_size,
            "cum_delta": self.session.cum_delta,
            "session_volume": self.session.profile.total,
            "vwap_distance": ((book.mid or 0.0) - (self.session.vwap.value or book.mid or 0.0)),
        }
