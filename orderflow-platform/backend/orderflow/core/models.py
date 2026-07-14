"""Shared runtime models: the L2 order book and rolling statistics helpers."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from .events import Side


class OrderBook:
    """Mutable L2 order book with lazily cached sorted views.

    Levels are keyed by price rounded to the instrument tick to avoid float
    drift. Depth is capped so a runaway feed cannot grow memory unbounded.
    """

    def __init__(self, symbol: str, tick_size: float = 0.01, max_depth: int = 200) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.max_depth = max_depth
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.ts: float = 0.0
        self._sorted_bids: list[tuple[float, float]] | None = None
        self._sorted_asks: list[tuple[float, float]] | None = None

    # -- mutation ---------------------------------------------------------
    def round_price(self, price: float) -> float:
        return round(round(price / self.tick_size) * self.tick_size, 10)

    def apply_snapshot(self, bids: list[tuple[float, float]], asks: list[tuple[float, float]], ts: float) -> None:
        self.bids = {self.round_price(p): s for p, s in bids if s > 0}
        self.asks = {self.round_price(p): s for p, s in asks if s > 0}
        self.ts = ts
        self._invalidate()

    def apply_delta(self, side: Side, price: float, size: float, ts: float) -> float:
        """Apply one delta; returns the previous size at that level."""
        book = self.bids if side is Side.BUY else self.asks
        price = self.round_price(price)
        prev = book.get(price, 0.0)
        if size <= 0:
            book.pop(price, None)
        else:
            book[price] = size
        self.ts = ts
        self._invalidate()
        return prev

    def _invalidate(self) -> None:
        self._sorted_bids = None
        self._sorted_asks = None

    # -- views ------------------------------------------------------------
    def top_bids(self, n: int | None = None) -> list[tuple[float, float]]:
        if self._sorted_bids is None:
            self._sorted_bids = sorted(self.bids.items(), key=lambda kv: -kv[0])[: self.max_depth]
        return self._sorted_bids if n is None else self._sorted_bids[:n]

    def top_asks(self, n: int | None = None) -> list[tuple[float, float]]:
        if self._sorted_asks is None:
            self._sorted_asks = sorted(self.asks.items())[: self.max_depth]
        return self._sorted_asks if n is None else self._sorted_asks[:n]

    @property
    def best_bid(self) -> float | None:
        top = self.top_bids(1)
        return top[0][0] if top else None

    @property
    def best_ask(self) -> float | None:
        top = self.top_asks(1)
        return top[0][0] if top else None

    @property
    def mid(self) -> float | None:
        bb, ba = self.best_bid, self.best_ask
        if bb is None or ba is None:
            return None
        return (bb + ba) / 2.0

    @property
    def spread(self) -> float | None:
        bb, ba = self.best_bid, self.best_ask
        if bb is None or ba is None:
            return None
        return ba - bb

    def depth(self, side: Side, levels: int = 10) -> float:
        rows = self.top_bids(levels) if side is Side.BUY else self.top_asks(levels)
        return sum(s for _, s in rows)

    def depth_center_of_mass(self, side: Side, levels: int = 20) -> float | None:
        """Size-weighted average price of the top of book — used to track
        liquidity migration (where the passive money is sitting)."""
        rows = self.top_bids(levels) if side is Side.BUY else self.top_asks(levels)
        total = sum(s for _, s in rows)
        if total <= 0:
            return None
        return sum(p * s for p, s in rows) / total


@dataclass(slots=True)
class RollingStats:
    """O(1) rolling mean/std over a fixed window (Welford on a deque)."""

    window: int
    values: deque = field(default_factory=deque)
    _sum: float = 0.0
    _sum_sq: float = 0.0

    def add(self, x: float) -> None:
        self.values.append(x)
        self._sum += x
        self._sum_sq += x * x
        if len(self.values) > self.window:
            old = self.values.popleft()
            self._sum -= old
            self._sum_sq -= old * old

    def __len__(self) -> int:
        return len(self.values)

    @property
    def mean(self) -> float:
        n = len(self.values)
        return self._sum / n if n else 0.0

    @property
    def std(self) -> float:
        n = len(self.values)
        if n < 2:
            return 0.0
        var = max(0.0, self._sum_sq / n - self.mean**2)
        return math.sqrt(var)

    def zscore(self, x: float) -> float:
        s = self.std
        if s < 1e-12:
            return 0.0
        return (x - self.mean) / s


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def sigmoid(x: float) -> float:
    if x < -60:
        return 0.0
    if x > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))
