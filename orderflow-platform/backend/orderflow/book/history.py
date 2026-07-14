"""Time × price liquidity history — the data behind the heatmap.

Each frame stores the visible book as {price: signed size} (bids positive,
asks negative). The frontend renders frames as heatmap columns; detectors
use them to see how liquidity *evolved* (pulled walls, migration) rather
than only the current snapshot.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from ..core.models import OrderBook


@dataclass(slots=True)
class BookFrame:
    ts: float
    mid: float | None
    levels: dict[float, float]  # price -> +bid size / -ask size


class BookHistory:
    def __init__(self, max_frames: int = 600) -> None:
        self.frames: deque[BookFrame] = deque(maxlen=max_frames)

    def capture(self, ts: float, book: OrderBook, levels: int = 40) -> BookFrame:
        frame_levels: dict[float, float] = {}
        for p, s in book.top_bids(levels):
            frame_levels[p] = s
        for p, s in book.top_asks(levels):
            frame_levels[p] = -s
        frame = BookFrame(ts=ts, mid=book.mid, levels=frame_levels)
        self.frames.append(frame)
        return frame

    def level_series(self, price: float, last_n: int = 50) -> list[float]:
        """Absolute size history at one price — how a wall grew/shrank."""
        out = []
        for f in list(self.frames)[-last_n:]:
            out.append(abs(f.levels.get(price, 0.0)))
        return out

    def as_ui_frames(self, last_n: int = 240) -> list[dict]:
        out = []
        for f in list(self.frames)[-last_n:]:
            out.append(
                {
                    "ts": f.ts,
                    "mid": f.mid,
                    "levels": [[p, s] for p, s in f.levels.items()],
                }
            )
        return out
