"""Trade → candle aggregation with per-price footprint (buy/sell volume at
each price inside the candle) for footprint charting."""
from __future__ import annotations

from ..core import Candle, CandleEvent, EventBus, Side, Topic, TradeEvent


class CandleAggregator:
    def __init__(self, bus: EventBus, symbol: str, seconds: float = 60.0, keep: int = 500) -> None:
        self.bus = bus
        self.symbol = symbol
        self.seconds = seconds
        self.keep = keep
        self.candles: list[Candle] = []
        self.current: Candle | None = None
        bus.subscribe(Topic.TRADE, self._on_trade)

    def _bucket_start(self, ts: float) -> float:
        return ts - (ts % self.seconds)

    def _on_trade(self, ev: TradeEvent) -> None:  # type: ignore[override]
        start = self._bucket_start(ev.ts)
        c = self.current
        if c is None or start >= c.ts_close:
            if c is not None:
                self._close_current(c)
            self.current = Candle(
                ts_open=start, ts_close=start + self.seconds,
                open=ev.price, high=ev.price, low=ev.price, close=ev.price,
                volume=0.0, buy_volume=0.0, sell_volume=0.0,
            )
            c = self.current
        c.high = max(c.high, ev.price)
        c.low = min(c.low, ev.price)
        c.close = ev.price
        c.volume += ev.size
        if ev.aggressor is Side.BUY:
            c.buy_volume += ev.size
        else:
            c.sell_volume += ev.size
        fp = c.footprint.setdefault(ev.price, [0.0, 0.0])
        fp[0 if ev.aggressor is Side.BUY else 1] += ev.size
        self.bus.publish(Topic.CANDLE, CandleEvent(ts=ev.ts, symbol=self.symbol, candle=c, closed=False))

    def _close_current(self, c: Candle) -> None:
        self.candles.append(c)
        if len(self.candles) > self.keep:
            del self.candles[: len(self.candles) - self.keep]
        self.bus.publish(Topic.CANDLE, CandleEvent(ts=c.ts_close, symbol=self.symbol, candle=c, closed=True))

    def as_ui(self, last_n: int = 120) -> list[dict]:
        rows = self.candles[-last_n:] + ([self.current] if self.current else [])
        return [
            {
                "t": c.ts_open, "o": c.open, "h": c.high, "l": c.low, "c": c.close,
                "v": round(c.volume, 1), "bv": round(c.buy_volume, 1), "sv": round(c.sell_volume, 1),
                "d": round(c.delta, 1),
                "fp": [[p, round(bs[0], 1), round(bs[1], 1)] for p, bs in sorted(c.footprint.items())],
            }
            for c in rows
        ]
