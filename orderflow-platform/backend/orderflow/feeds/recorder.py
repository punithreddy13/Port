"""Event recorder → JSONL, the storage format for replay & backtesting.

One line per market-data event keeps files streamable and append-only;
gzip-friendly and trivially partitionable by day in production.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import IO

from ..core import BookDelta, BookSnapshot, Event, EventBus, Side, Topic, TradeEvent

RECORDED_TOPICS = {Topic.BOOK_SNAPSHOT, Topic.BOOK_DELTA, Topic.TRADE}


def encode_event(topic: Topic, ev: Event) -> dict:
    if isinstance(ev, BookSnapshot):
        return {"t": "S", "ts": ev.ts, "sym": ev.symbol, "b": ev.bids, "a": ev.asks}
    if isinstance(ev, BookDelta):
        return {"t": "D", "ts": ev.ts, "sym": ev.symbol, "s": ev.side.value, "p": ev.price, "q": ev.size}
    if isinstance(ev, TradeEvent):
        return {"t": "T", "ts": ev.ts, "sym": ev.symbol, "p": ev.price, "q": ev.size,
                "s": ev.aggressor.value, "id": ev.trade_id}
    raise TypeError(f"not a recordable event: {ev!r}")


def decode_event(row: dict) -> tuple[Topic, Event]:
    kind = row["t"]
    if kind == "S":
        return Topic.BOOK_SNAPSHOT, BookSnapshot(
            ts=row["ts"], symbol=row["sym"],
            bids=[tuple(x) for x in row["b"]], asks=[tuple(x) for x in row["a"]],
        )
    if kind == "D":
        return Topic.BOOK_DELTA, BookDelta(
            ts=row["ts"], symbol=row["sym"], side=Side(row["s"]), price=row["p"], size=row["q"]
        )
    if kind == "T":
        return Topic.TRADE, TradeEvent(
            ts=row["ts"], symbol=row["sym"], price=row["p"], size=row["q"],
            aggressor=Side(row["s"]), trade_id=row.get("id", 0),
        )
    raise ValueError(f"unknown record type {kind!r}")


class DataRecorder:
    def __init__(self, bus: EventBus, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: IO[str] | None = None
        self.rows_written = 0
        bus.subscribe_all(self._on_event)

    def _on_event(self, topic: Topic, ev: Event) -> None:
        if topic not in RECORDED_TOPICS:
            return
        if self._fh is None:
            self._fh = self.path.open("a", encoding="utf-8")
        self._fh.write(json.dumps(encode_event(topic, ev)) + "\n")
        self.rows_written += 1

    def close(self) -> None:
        if self._fh:
            self._fh.flush()
            self._fh.close()
            self._fh = None


def load_events(path: str | Path) -> list[tuple[Topic, Event]]:
    out: list[tuple[Topic, Event]] = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(decode_event(json.loads(line)))
    return out
