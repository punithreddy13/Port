"""Deterministic market simulator + real-time feed wrapper.

The simulator is a small agent-based model of a futures book:

* passive market makers keep ~N levels quoted around fair value,
* takers send heavy-tailed market orders whose side follows the regime,
* scripted "institutional" behaviours are injected on a schedule —
  spoofing, icebergs, absorption defence, liquidity sweeps / stop hunts.

Injected behaviours are labelled in ``injected_log`` so detector tests can
assert against ground truth instead of eyeballing output. The simulator is
pure and seeded: ``step()`` returns events, the feed pumps them in real
time, tests call ``step()`` directly.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from ..core import BookDelta, BookSnapshot, Event, EventBus, Side, Topic, TradeEvent
from ..core.plugins import FEEDS
from .base import MarketDataFeed

Emitted = tuple[Topic, Event]


@dataclass
class _Iceberg:
    side: Side
    price: float
    hidden_remaining: float
    display: float


@dataclass
class _Spoof:
    side: Side
    price: float
    size: float
    expires: float


@dataclass
class _Absorption:
    side: Side  # side doing the absorbing (BUY = defending the bid)
    price: float
    remaining: float
    until: float


@dataclass
class InjectedEvent:
    ts: float
    kind: str
    side: str
    price: float
    detail: dict = field(default_factory=dict)


class MarketSimulator:
    def __init__(self, symbol: str = "SIM-FUT", tick: float = 0.25, seed: int = 7,
                 start_price: float = 5000.0, inject: bool = True) -> None:
        self.symbol = symbol
        self.tick = tick
        self.rng = random.Random(seed)
        self.fair = start_price
        self.ts = 1_700_000_000.0  # deterministic epoch base for replayability
        self.inject = inject
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.trade_id = 0
        self.regime = "chop"  # chop | up | down
        self._regime_until = self.ts
        self._next_inject = self.ts + 5.0
        self.icebergs: list[_Iceberg] = []
        self.spoofs: list[_Spoof] = []
        self.absorptions: list[_Absorption] = []
        self.injected_log: list[InjectedEvent] = []
        self._seed_book()

    # ------------------------------------------------------------------
    def _rp(self, p: float) -> float:
        return round(round(p / self.tick) * self.tick, 10)

    def _seed_book(self) -> None:
        mid = self._rp(self.fair)
        for i in range(1, 61):
            self.bids[self._rp(mid - i * self.tick)] = self._base_size()
            self.asks[self._rp(mid + i * self.tick)] = self._base_size()

    def _base_size(self) -> float:
        return round(self.rng.gammavariate(2.2, 22.0) + 5, 1)

    def snapshot(self) -> BookSnapshot:
        return BookSnapshot(
            ts=self.ts,
            symbol=self.symbol,
            bids=sorted(self.bids.items(), key=lambda kv: -kv[0]),
            asks=sorted(self.asks.items()),
        )

    @property
    def best_bid(self) -> float:
        return max(self.bids)

    @property
    def best_ask(self) -> float:
        return min(self.asks)

    # ------------------------------------------------------------------
    def step(self, dt: float = 0.1) -> list[Emitted]:
        """Advance the market by dt seconds; return emitted events."""
        self.ts += dt
        out: list[Emitted] = []
        self._update_regime()
        self._drift_fair(dt)
        self._maker_flow(out)
        self._taker_flow(out, dt)
        if self.inject:
            self._run_injections(out)
        return out

    # -- regime & fair value -------------------------------------------
    def _update_regime(self) -> None:
        if self.ts >= self._regime_until:
            self.regime = self.rng.choices(["chop", "up", "down"], weights=[0.5, 0.25, 0.25])[0]
            self._regime_until = self.ts + self.rng.uniform(20, 60)

    def _drift_fair(self, dt: float) -> None:
        drift = {"chop": 0.0, "up": 0.35, "down": -0.35}[self.regime]
        self.fair += drift * dt + self.rng.gauss(0, 0.6) * dt**0.5

    # -- passive side ----------------------------------------------------
    def _maker_flow(self, out: list[Emitted]) -> None:
        """Makers re-centre quotes around fair value: add missing levels,
        occasionally pull/resize existing ones."""
        mid = self._rp(self.fair)
        for side, book, sign in ((Side.BUY, self.bids, -1), (Side.SELL, self.asks, +1)):
            # ensure quotes exist near mid
            for i in range(1, 25):
                p = self._rp(mid + sign * i * self.tick)
                if (sign < 0 and p >= self.best_ask) or (sign > 0 and p <= self.best_bid):
                    continue
                if p not in book and self.rng.random() < 0.6:
                    size = self._base_size()
                    book[p] = size
                    out.append((Topic.BOOK_DELTA, BookDelta(self.ts, self.symbol, side, p, size)))
            # random pulls / resizes away from protected levels
            protected = {a.price for a in self.absorptions} | {i.price for i in self.icebergs}
            for p in list(book):
                if p in protected or self.rng.random() > 0.02:
                    continue
                if self.rng.random() < 0.4:
                    del book[p]
                    out.append((Topic.BOOK_DELTA, BookDelta(self.ts, self.symbol, side, p, 0.0)))
                else:
                    book[p] = self._base_size()
                    out.append((Topic.BOOK_DELTA, BookDelta(self.ts, self.symbol, side, p, book[p])))
            # trim far levels
            for p in sorted(book, key=lambda x: abs(x - mid))[80:]:
                del book[p]
                out.append((Topic.BOOK_DELTA, BookDelta(self.ts, self.symbol, side, p, 0.0)))

    # -- aggressive side --------------------------------------------------
    def _taker_flow(self, out: list[Emitted], dt: float) -> None:
        lam = 3.0 * dt  # expected orders per step
        n = 0
        while self.rng.random() < lam and n < 5:
            n += 1
            lam *= 0.5
            bias = {"chop": 0.5, "up": 0.62, "down": 0.38}[self.regime]
            side = Side.BUY if self.rng.random() < bias else Side.SELL
            size = round(min(400.0, self.rng.paretovariate(1.6) * 8), 1)
            self.execute_market_order(side, size, out)

    def execute_market_order(self, side: Side, size: float, out: list[Emitted]) -> float:
        """Match a market order against the book; returns filled quantity."""
        book = self.asks if side is Side.BUY else self.bids
        filled = 0.0
        while size > 0 and book:
            level = min(book) if side is Side.BUY else max(book)
            avail = book[level]
            take = min(avail, size)
            # iceberg refill check happens after the visible size is consumed
            self.trade_id += 1
            out.append((Topic.TRADE, TradeEvent(self.ts, self.symbol, level, round(take, 2), side, self.trade_id)))
            filled += take
            size -= take
            remaining = avail - take
            refill = self._iceberg_refill(side.opposite, level, take)
            absorb = self._absorption_refill(side.opposite, level, take)
            new_size = remaining + refill + absorb
            if new_size <= 0:
                del book[level]
            else:
                book[level] = round(new_size, 2)
            out.append((Topic.BOOK_DELTA, BookDelta(self.ts, self.symbol, side.opposite, level, max(0.0, round(new_size, 2)))))
            if remaining > 0 and refill == 0 and absorb == 0:
                break  # level not exhausted; order done
        return filled

    def _iceberg_refill(self, passive_side: Side, price: float, consumed: float) -> float:
        for ib in self.icebergs:
            if ib.side is passive_side and abs(ib.price - price) < 1e-9 and ib.hidden_remaining > 0:
                refill = min(ib.display, ib.hidden_remaining)
                ib.hidden_remaining -= refill
                return refill
        return 0.0

    def _absorption_refill(self, passive_side: Side, price: float, consumed: float) -> float:
        for ab in self.absorptions:
            if ab.side is passive_side and abs(ab.price - price) < 1e-9 and ab.remaining > 0 and self.ts < ab.until:
                refill = min(consumed, ab.remaining)
                ab.remaining -= refill
                return refill
        return 0.0

    # -- scripted institutional behaviours --------------------------------
    def _run_injections(self, out: list[Emitted]) -> None:
        # expire spoofs (pull them untouched — the classic tell)
        for sp in list(self.spoofs):
            near = abs((self.best_bid + self.best_ask) / 2 - sp.price) <= 2 * self.tick
            if self.ts >= sp.expires or near:
                book = self.bids if sp.side is Side.BUY else self.asks
                if sp.price in book:
                    book[sp.price] = max(0.0, book[sp.price] - sp.size)
                    newsz = book[sp.price]
                    if newsz <= 0:
                        del book[sp.price]
                        newsz = 0.0
                    out.append((Topic.BOOK_DELTA, BookDelta(self.ts, self.symbol, sp.side, sp.price, newsz)))
                self.spoofs.remove(sp)
        self.icebergs = [i for i in self.icebergs if i.hidden_remaining > 0]
        self.absorptions = [a for a in self.absorptions if a.remaining > 0 and self.ts < a.until]

        if self.ts < self._next_inject:
            return
        self._next_inject = self.ts + self.rng.uniform(8, 20)
        kind = self.rng.choice(["spoof", "iceberg", "absorption", "sweep", "wall"])
        getattr(self, f"inject_{kind}")(out)

    def inject_spoof(self, out: list[Emitted], side: Side | None = None) -> None:
        side = side or self.rng.choice([Side.BUY, Side.SELL])
        book = self.bids if side is Side.BUY else self.asks
        ref = self.best_bid if side is Side.BUY else self.best_ask
        offset = self.rng.randint(4, 9) * self.tick
        price = self._rp(ref - offset if side is Side.BUY else ref + offset)
        size = round(self.rng.uniform(600, 1500), 0)
        book[price] = book.get(price, 0.0) + size
        out.append((Topic.BOOK_DELTA, BookDelta(self.ts, self.symbol, side, price, book[price])))
        self.spoofs.append(_Spoof(side, price, size, self.ts + self.rng.uniform(2.0, 5.0)))
        self.injected_log.append(InjectedEvent(self.ts, "spoof", side.value, price, {"size": size}))

    def inject_wall(self, out: list[Emitted], side: Side | None = None) -> None:
        """A genuine wall: big resting size that stays."""
        side = side or self.rng.choice([Side.BUY, Side.SELL])
        book = self.bids if side is Side.BUY else self.asks
        ref = self.best_bid if side is Side.BUY else self.best_ask
        offset = self.rng.randint(2, 5) * self.tick
        price = self._rp(ref - offset if side is Side.BUY else ref + offset)
        size = round(self.rng.uniform(800, 2000), 0)
        book[price] = book.get(price, 0.0) + size
        out.append((Topic.BOOK_DELTA, BookDelta(self.ts, self.symbol, side, price, book[price])))
        self.injected_log.append(InjectedEvent(self.ts, "wall", side.value, price, {"size": size}))

    def inject_iceberg(self, out: list[Emitted], side: Side | None = None) -> None:
        side = side or self.rng.choice([Side.BUY, Side.SELL])
        price = self.best_bid if side is Side.BUY else self.best_ask
        display = round(self.rng.uniform(15, 40), 0)
        hidden = round(self.rng.uniform(500, 1200), 0)
        book = self.bids if side is Side.BUY else self.asks
        book[price] = display
        out.append((Topic.BOOK_DELTA, BookDelta(self.ts, self.symbol, side, price, display)))
        self.icebergs.append(_Iceberg(side, price, hidden, display))
        self.injected_log.append(InjectedEvent(self.ts, "iceberg", side.value, price, {"hidden": hidden}))

    def inject_absorption(self, out: list[Emitted], side: Side | None = None) -> None:
        side = side or self.rng.choice([Side.BUY, Side.SELL])
        price = self.best_bid if side is Side.BUY else self.best_ask
        budget = round(self.rng.uniform(800, 2000), 0)
        self.absorptions.append(_Absorption(side, price, budget, self.ts + self.rng.uniform(6, 12)))
        self.injected_log.append(InjectedEvent(self.ts, "absorption", side.value, price, {"budget": budget}))
        # absorption is met by aggressive flow into it
        for _ in range(self.rng.randint(4, 8)):
            self.execute_market_order(side.opposite, round(self.rng.uniform(40, 140), 0), out)

    def inject_sweep(self, out: list[Emitted], side: Side | None = None) -> None:
        """Aggressive burst through several levels — sweep / stop hunt."""
        side = side or self.rng.choice([Side.BUY, Side.SELL])
        total = round(self.rng.uniform(600, 1400), 0)
        self.injected_log.append(
            InjectedEvent(self.ts, "sweep", side.value,
                          self.best_ask if side is Side.BUY else self.best_bid, {"size": total})
        )
        for _ in range(self.rng.randint(3, 6)):
            self.execute_market_order(side, total / 5, out)

    # convenience for tests ------------------------------------------------
    def run_for(self, seconds: float, dt: float = 0.1) -> list[Emitted]:
        out: list[Emitted] = []
        steps = int(seconds / dt)
        for _ in range(steps):
            out.extend(self.step(dt))
        return out


@FEEDS.register("simulated")
class SimulatedFeed(MarketDataFeed):
    def __init__(self, bus: EventBus, symbol: str, tick_size: float = 0.25,
                 seed: int | None = None, speed: float = 1.0, **_: object) -> None:
        super().__init__(bus, symbol)
        self.sim = MarketSimulator(symbol, tick=tick_size, seed=seed if seed is not None else int(time.time()))
        self.speed = speed

    def run(self) -> None:
        snap = self.sim.snapshot()
        self.bus.publish(Topic.BOOK_SNAPSHOT, snap)
        dt = 0.1
        while not self.stopped:
            for topic, ev in self.sim.step(dt):
                self.bus.publish(topic, ev)
            time.sleep(dt / max(self.speed, 1e-6))
