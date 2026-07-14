"""Paper broker: fills simulated orders against the live/replayed tape,
tracks position & P&L, enforces the risk engine, and records every closed
trade for analytics. Used identically in live-sim, replay and backtests."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from ..analytics.performance import ClosedTrade
from ..core import Side, TradeEvent
from ..risk.engine import RiskEngine


@dataclass
class Order:
    id: int
    side: Side
    size: float
    type: str = "market"  # market | limit
    limit_price: float | None = None
    stop: float | None = None
    take_profit: float | None = None
    setup: str = ""
    trader: str = "manual"
    status: str = "open"  # open | filled | rejected | cancelled


@dataclass
class Position:
    size: float = 0.0  # signed; + long
    avg_price: float = 0.0
    stop: float | None = None
    take_profit: float | None = None
    ts_open: float = 0.0
    setup: str = ""
    trader: str = "manual"


@dataclass
class BrokerState:
    realized: float = 0.0
    unrealized: float = 0.0
    fills: int = 0
    rejected: list[str] = field(default_factory=list)


class PaperBroker:
    def __init__(self, risk: RiskEngine, point_value: float = 1.0) -> None:
        self.risk = risk
        self.point_value = point_value
        self.position = Position()
        self.state = BrokerState()
        self.open_orders: list[Order] = []
        self.closed_trades: list[ClosedTrade] = []
        self.last_price: float | None = None
        self._ids = itertools.count(1)

    # -- order entry ------------------------------------------------------
    def submit(self, side: Side, size: float, type: str = "market", limit_price: float | None = None,
               stop: float | None = None, take_profit: float | None = None,
               setup: str = "", trader: str = "manual") -> Order:
        order = Order(next(self._ids), side, size, type, limit_price, stop, take_profit, setup, trader)
        signed = size if side is Side.BUY else -size
        ref_price = limit_price or self.last_price or 0.0
        ok, reason = self.risk.allow_order(signed, self.position.size, ref_price, stop)
        if not ok:
            order.status = "rejected"
            self.state.rejected.append(reason)
            return order
        if type == "market" and self.last_price is not None:
            self._fill(order, self.last_price, ts=0.0 if self.last_price is None else self._now)
        else:
            self.open_orders.append(order)
        return order

    def cancel_all(self) -> None:
        for o in self.open_orders:
            o.status = "cancelled"
        self.open_orders.clear()

    def flatten(self, ts: float) -> None:
        if self.position.size != 0 and self.last_price is not None:
            self._close_position(self.last_price, ts, reason="flatten")

    _now: float = 0.0

    # -- market data ---------------------------------------------------------
    def on_trade(self, ev: TradeEvent) -> None:
        self.last_price = ev.price
        self._now = ev.ts
        # limit fills
        for o in list(self.open_orders):
            if o.type != "limit" or o.limit_price is None:
                continue
            crossed = ev.price <= o.limit_price if o.side is Side.BUY else ev.price >= o.limit_price
            if crossed:
                self.open_orders.remove(o)
                self._fill(o, o.limit_price, ev.ts)
        # stop / take-profit management on the open position
        pos = self.position
        if pos.size != 0:
            if pos.stop is not None and ((pos.size > 0 and ev.price <= pos.stop) or (pos.size < 0 and ev.price >= pos.stop)):
                self._close_position(pos.stop, ev.ts, reason="stop")
            elif pos.take_profit is not None and ((pos.size > 0 and ev.price >= pos.take_profit) or (pos.size < 0 and ev.price <= pos.take_profit)):
                self._close_position(pos.take_profit, ev.ts, reason="take_profit")
        self.state.unrealized = self.unrealized_pnl()

    # -- internals -------------------------------------------------------------
    def _fill(self, order: Order, price: float, ts: float) -> None:
        order.status = "filled"
        self.state.fills += 1
        signed = order.size if order.side is Side.BUY else -order.size
        pos = self.position
        if pos.size == 0 or (pos.size > 0) == (signed > 0):
            new_size = pos.size + signed
            pos.avg_price = (pos.avg_price * abs(pos.size) + price * abs(signed)) / max(abs(new_size), 1e-12)
            pos.size = new_size
            if pos.ts_open == 0.0:
                pos.ts_open = ts
                pos.setup = order.setup
                pos.trader = order.trader
            if order.stop is not None:
                pos.stop = order.stop
            if order.take_profit is not None:
                pos.take_profit = order.take_profit
        else:
            closing = min(abs(signed), abs(pos.size))
            direction = 1.0 if pos.size > 0 else -1.0
            pnl = (price - pos.avg_price) * direction * closing * self.point_value
            self._record_close(pos, price, ts, closing, pnl)
            remainder = abs(signed) - closing
            pos.size += signed if abs(signed) <= closing else direction * -closing
            if abs(pos.size) < 1e-12:
                pos.size = 0.0
            if remainder > 0:  # flipped
                pos.size = remainder if signed > 0 else -remainder
                pos.avg_price = price
                pos.ts_open = ts
                pos.setup = order.setup
                pos.trader = order.trader
                pos.stop = order.stop
                pos.take_profit = order.take_profit
            elif pos.size == 0.0:
                pos.stop = pos.take_profit = None
                pos.ts_open = 0.0

    def _close_position(self, price: float, ts: float, reason: str) -> None:
        pos = self.position
        direction = 1.0 if pos.size > 0 else -1.0
        qty = abs(pos.size)
        pnl = (price - pos.avg_price) * direction * qty * self.point_value
        self._record_close(pos, price, ts, qty, pnl)
        pos.size = 0.0
        pos.stop = pos.take_profit = None
        pos.ts_open = 0.0

    def _record_close(self, pos: Position, price: float, ts: float, qty: float, pnl: float) -> None:
        self.state.realized += pnl
        self.risk.on_realized_pnl(ts, pnl)
        self.closed_trades.append(ClosedTrade(
            ts_open=pos.ts_open, ts_close=ts,
            direction="buy" if pos.size > 0 else "sell",
            entry=pos.avg_price, exit=price, size=qty, pnl=round(pnl, 2),
            setup=pos.setup, trader=pos.trader,
        ))

    def unrealized_pnl(self) -> float:
        pos = self.position
        if pos.size == 0 or self.last_price is None:
            return 0.0
        direction = 1.0 if pos.size > 0 else -1.0
        return (self.last_price - pos.avg_price) * direction * abs(pos.size) * self.point_value

    def summary(self) -> dict:
        return {
            "position": self.position.size,
            "avg_price": round(self.position.avg_price, 6) if self.position.size else None,
            "stop": self.position.stop,
            "take_profit": self.position.take_profit,
            "realized": round(self.state.realized, 2),
            "unrealized": round(self.unrealized_pnl(), 2),
            "fills": self.state.fills,
            "closed_trades": len(self.closed_trades),
        }
