"""Order book engine: maintains the live L2 book from feed events and
exposes it to every other engine through the shared MarketState."""
from __future__ import annotations

from ..core import BookDelta, BookSnapshot, EventBus, OrderBook, Topic, TradeEvent
from .history import BookHistory


class OrderBookEngine:
    def __init__(self, bus: EventBus, symbol: str, tick_size: float, history_frames: int = 600) -> None:
        self.bus = bus
        self.book = OrderBook(symbol, tick_size=tick_size)
        self.history = BookHistory(max_frames=history_frames)
        self.last_trade_price: float | None = None
        self.session_volume = 0.0
        bus.subscribe(Topic.BOOK_SNAPSHOT, self._on_snapshot)
        bus.subscribe(Topic.BOOK_DELTA, self._on_delta)
        bus.subscribe(Topic.TRADE, self._on_trade)

    def _on_snapshot(self, ev: BookSnapshot) -> None:  # type: ignore[override]
        self.book.apply_snapshot(ev.bids, ev.asks, ev.ts)

    def _on_delta(self, ev: BookDelta) -> None:  # type: ignore[override]
        self.book.apply_delta(ev.side, ev.price, ev.size, ev.ts)

    def _on_trade(self, ev: TradeEvent) -> None:  # type: ignore[override]
        self.last_trade_price = ev.price
        self.session_volume += ev.size

    def capture_frame(self, ts: float, levels: int = 40) -> None:
        """Snapshot the book into heatmap history (called at UI frame rate)."""
        self.history.capture(ts, self.book, levels)
