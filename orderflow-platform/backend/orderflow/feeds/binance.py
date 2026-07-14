"""Binance L2 + trades connector (crypto is the one venue with free,
high-quality public depth data — ideal for live development).

Uses the combined stream: depth updates + aggTrades. Requires the
``websockets`` package and outbound network access; the platform runs fine
without it (simulated/replay feeds carry no network dependency).
"""
from __future__ import annotations

import json
import logging

from ..core import BookDelta, BookSnapshot, EventBus, Side, Topic, TradeEvent
from ..core.plugins import FEEDS
from .base import MarketDataFeed

log = logging.getLogger(__name__)


@FEEDS.register("binance")
class BinanceFeed(MarketDataFeed):
    WS_URL = "wss://stream.binance.com:9443/stream?streams={streams}"
    REST_DEPTH = "https://api.binance.com/api/v3/depth?symbol={symbol}&limit=1000"

    def __init__(self, bus: EventBus, symbol: str = "BTCUSDT", **_: object) -> None:
        super().__init__(bus, symbol.upper())

    def run(self) -> None:
        import asyncio

        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        import urllib.request

        import websockets

        sym = self.symbol.lower()
        streams = f"{sym}@depth@100ms/{sym}@aggTrade"
        url = self.WS_URL.format(streams=streams)
        while not self.stopped:
            try:
                async with websockets.connect(url, max_size=2**22) as ws:
                    # snapshot after stream open, per Binance's sync recipe
                    req = urllib.request.Request(self.REST_DEPTH.format(symbol=self.symbol))
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        snap = json.loads(resp.read())
                    last_update_id = snap["lastUpdateId"]
                    self.bus.publish(
                        Topic.BOOK_SNAPSHOT,
                        BookSnapshot(
                            ts=self._now(),
                            symbol=self.symbol,
                            bids=[(float(p), float(q)) for p, q in snap["bids"]],
                            asks=[(float(p), float(q)) for p, q in snap["asks"]],
                        ),
                    )
                    async for raw in ws:
                        if self.stopped:
                            return
                        msg = json.loads(raw)
                        data = msg.get("data", {})
                        etype = data.get("e")
                        if etype == "depthUpdate":
                            if data["u"] <= last_update_id:
                                continue
                            ts = data["E"] / 1000.0
                            for p, q in data.get("b", []):
                                self.bus.publish(Topic.BOOK_DELTA, BookDelta(ts, self.symbol, Side.BUY, float(p), float(q)))
                            for p, q in data.get("a", []):
                                self.bus.publish(Topic.BOOK_DELTA, BookDelta(ts, self.symbol, Side.SELL, float(p), float(q)))
                        elif etype == "aggTrade":
                            ts = data["E"] / 1000.0
                            # m=True means buyer is the maker → aggressor sold
                            aggressor = Side.SELL if data["m"] else Side.BUY
                            self.bus.publish(
                                Topic.TRADE,
                                TradeEvent(ts, self.symbol, float(data["p"]), float(data["q"]), aggressor, int(data["a"])),
                            )
            except Exception as exc:  # noqa: BLE001 - reconnect loop
                if self.stopped:
                    return
                log.warning("binance feed error (%s); reconnecting in 3s", exc)
                import time

                time.sleep(3)

    @staticmethod
    def _now() -> float:
        import time

        return time.time()
