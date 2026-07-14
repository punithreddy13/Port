"""Liquidity detectors.

Each detector observes book/trade events (via LiquidityEngine callbacks)
and emits Signals with a confidence score and a human explanation. All are
registered as plugins so deployments can enable/disable/extend the set.

Statistical framing: nearly every detector compares an observation against
a rolling baseline (z-score) rather than absolute thresholds, so the same
code works on ES futures, BTC perps or an illiquid alt.
"""
from __future__ import annotations

import abc
from collections import defaultdict, deque
from dataclasses import dataclass

from ..core import Side, Signal, SignalType
from ..core.models import OrderBook, RollingStats, clamp
from ..core.plugins import DETECTORS

# sentinel: "never happened" — far enough past that rate-limit gates always pass
NEVER = float("-inf")


@dataclass
class BookContext:
    """What detectors get to see on every event."""

    book: OrderBook
    ts: float


class Detector(abc.ABC):
    name = "detector"

    def __init__(self, tick_size: float) -> None:
        self.tick = tick_size

    def on_delta(self, ctx: BookContext, side: Side, price: float, new_size: float, prev_size: float) -> list[Signal]:
        return []

    def on_trade(self, ctx: BookContext, price: float, size: float, aggressor: Side) -> list[Signal]:
        return []

    def on_tick(self, ctx: BookContext) -> list[Signal]:
        """Called periodically (once per engine poll) for time-based logic."""
        return []

    def _signal(self, ctx: BookContext, type_: SignalType, side: Side | None, price: float,
                confidence: float, explanation: str, **data: object) -> Signal:
        return Signal(
            ts=ctx.ts, symbol=ctx.book.symbol, type=type_, side=side, price=price,
            # 0.95 cap: no heuristic detection is ever certain
            confidence=clamp(confidence, 0.0, 0.95), explanation=explanation,
            data=dict(data), source=self.name,
        )


# ---------------------------------------------------------------------------
@DETECTORS.register("liquidity_wall")
class LiquidityWallDetector(Detector):
    """A level holding several × the typical visible size. Emits on appearance
    and re-emits (refreshed) while the wall persists."""

    name = "liquidity_wall"
    RATIO = 4.0

    def __init__(self, tick_size: float) -> None:
        super().__init__(tick_size)
        self.level_stats = RollingStats(window=800)
        self._active: dict[tuple[str, float], float] = {}  # (side, price) -> last emit ts

    def on_delta(self, ctx: BookContext, side: Side, price: float, new_size: float, prev_size: float) -> list[Signal]:
        if new_size > 0:
            self.level_stats.add(new_size)
        key = (side.value, price)
        if new_size <= 0:
            self._active.pop(key, None)
            return []
        mean = self.level_stats.mean
        if len(self.level_stats) < 50 or mean <= 0:
            return []
        ratio = new_size / mean
        if ratio < self.RATIO:
            self._active.pop(key, None)
            return []
        last = self._active.get(key, NEVER)
        if ctx.ts - last < 5.0:  # refresh at most every 5s per wall
            return []
        self._active[key] = ctx.ts
        conf = clamp(0.45 + 0.08 * (ratio - self.RATIO))
        role = "support" if side is Side.BUY else "resistance"
        return [self._signal(
            ctx, SignalType.LIQUIDITY_WALL, side, price, conf,
            f"{new_size:,.0f} resting on the {side.value} at {price:g} — "
            f"{ratio:.1f}× the average level size; acting as {role} while it holds.",
            size=new_size, ratio=round(ratio, 2),
        )]


# ---------------------------------------------------------------------------
@DETECTORS.register("spoofing")
class SpoofingDetector(Detector):
    """Large order appears away from the touch, then is pulled without being
    traded into. Fill-free disappearance is the discriminator vs. a real
    wall that got consumed."""

    name = "spoofing"
    RATIO = 4.0
    MAX_LIFETIME = 30.0

    def __init__(self, tick_size: float) -> None:
        super().__init__(tick_size)
        self.level_stats = RollingStats(window=800)
        self._watch: dict[tuple[str, float], dict] = {}
        self._traded: dict[tuple[str, float], float] = defaultdict(float)

    def on_trade(self, ctx: BookContext, price: float, size: float, aggressor: Side) -> list[Signal]:
        # trade at a price consumes the passive side there
        self._traded[(aggressor.opposite.value, price)] += size
        return []

    def on_delta(self, ctx: BookContext, side: Side, price: float, new_size: float, prev_size: float) -> list[Signal]:
        if new_size > 0:
            self.level_stats.add(new_size)
        key = (side.value, price)
        mean = self.level_stats.mean if len(self.level_stats) >= 50 else 0.0

        # candidate appears
        added = new_size - prev_size
        if mean > 0 and added > self.RATIO * mean:
            best = ctx.book.best_bid if side is Side.BUY else ctx.book.best_ask
            if best is not None and abs(price - best) >= 2 * self.tick:
                self._watch[key] = {"ts": ctx.ts, "size": added, "traded_at_start": self._traded[key]}
                return []

        # candidate pulled
        w = self._watch.get(key)
        if w and prev_size > 0:
            pulled = prev_size - new_size
            if pulled >= 0.8 * w["size"]:
                traded = self._traded[key] - w["traded_at_start"]
                lifetime = ctx.ts - w["ts"]
                del self._watch[key]
                if traded < 0.1 * w["size"] and lifetime <= self.MAX_LIFETIME:
                    conf = clamp(0.5 + 0.25 * (1 - lifetime / self.MAX_LIFETIME) + 0.15 * (traded == 0))
                    intent = "pressure price down" if side is Side.SELL else "prop price up"
                    return [self._signal(
                        ctx, SignalType.SPOOFING, side.opposite, price, conf,
                        f"{w['size']:,.0f} on the {side.value} at {price:g} pulled after {lifetime:.1f}s "
                        f"with almost no fills — likely spoof meant to {intent}; real intent is the other way.",
                        size=w["size"], lifetime=round(lifetime, 2), traded=round(traded, 1),
                    )]
        elif w and ctx.ts - w["ts"] > self.MAX_LIFETIME:
            del self._watch[key]  # lived long enough to be real
        return []


# ---------------------------------------------------------------------------
@DETECTORS.register("iceberg")
class IcebergDetector(Detector):
    """Executed volume at one price far exceeds what was ever displayed
    there — hidden size is refilling behind a small visible order."""

    name = "iceberg"
    MULT = 3.0

    def __init__(self, tick_size: float) -> None:
        super().__init__(tick_size)
        self._exec: dict[tuple[str, float], float] = defaultdict(float)
        self._max_visible: dict[tuple[str, float], float] = defaultdict(float)
        self._emitted: dict[tuple[str, float], float] = {}
        self._window: deque[tuple[float, str, float, float]] = deque()

    def on_delta(self, ctx: BookContext, side: Side, price: float, new_size: float, prev_size: float) -> list[Signal]:
        key = (side.value, price)
        self._max_visible[key] = max(self._max_visible[key], new_size)
        return []

    def on_trade(self, ctx: BookContext, price: float, size: float, aggressor: Side) -> list[Signal]:
        passive = aggressor.opposite
        key = (passive.value, price)
        self._exec[key] += size
        self._window.append((ctx.ts, passive.value, price, size))
        while self._window and ctx.ts - self._window[0][0] > 120:
            old_ts, s, p, q = self._window.popleft()
            k = (s, p)
            self._exec[k] -= q
            if self._exec[k] <= 0:
                self._exec.pop(k, None)
                self._max_visible.pop(k, None)
                self._emitted.pop(k, None)

        visible = max(self._max_visible[key], 1e-9)
        executed = self._exec[key]
        if visible < 5:
            return []
        ratio = executed / visible
        if ratio < self.MULT:
            return []
        if ctx.ts - self._emitted.get(key, NEVER) < 10.0:
            return []
        self._emitted[key] = ctx.ts
        conf = clamp(0.5 + 0.1 * (ratio - self.MULT))
        return [self._signal(
            ctx, SignalType.ICEBERG, passive, price, conf,
            f"{executed:,.0f} traded into the {passive.value} at {price:g} but never more than "
            f"{visible:,.0f} was displayed — an iceberg is refilling hidden size ({ratio:.1f}× visible).",
            executed=round(executed, 1), max_visible=round(visible, 1), ratio=round(ratio, 2),
        )]


# ---------------------------------------------------------------------------
@DETECTORS.register("absorption")
class AbsorptionDetector(Detector):
    """Heavy aggressive volume into a level while price refuses to move
    through it — passive side is absorbing. Bullish when the bid absorbs."""

    name = "absorption"
    WINDOW = 8.0

    def __init__(self, tick_size: float) -> None:
        super().__init__(tick_size)
        self.flow_stats = RollingStats(window=400)
        self._at_level: dict[tuple[str, float], deque] = defaultdict(deque)
        self._emitted: dict[tuple[str, float], float] = {}

    def on_trade(self, ctx: BookContext, price: float, size: float, aggressor: Side) -> list[Signal]:
        self.flow_stats.add(size)
        passive = aggressor.opposite
        key = (passive.value, price)
        dq = self._at_level[key]
        dq.append((ctx.ts, size))
        while dq and ctx.ts - dq[0][0] > self.WINDOW:
            dq.popleft()
        hit = sum(q for _, q in dq)
        if len(self.flow_stats) < 60:
            return []
        expected = self.flow_stats.mean * max(len(dq), 1)
        # price must still be AT the level: the defence is holding
        best = ctx.book.best_bid if passive is Side.BUY else ctx.book.best_ask
        still_there = best is not None and abs(best - price) <= self.tick
        if hit < 4 * expected or not still_there or len(dq) < 5:
            return []
        if ctx.ts - self._emitted.get(key, NEVER) < 10.0:
            return []
        self._emitted[key] = ctx.ts
        conf = clamp(0.45 + 0.1 * (hit / max(expected, 1e-9) - 4))
        who = "buyers" if passive is Side.BUY else "sellers"
        return [self._signal(
            ctx, SignalType.ABSORPTION, passive, price, conf,
            f"{hit:,.0f} of aggressive {aggressor.value} volume hit {price:g} in {self.WINDOW:.0f}s "
            f"and the level is still holding — passive {who} are absorbing the pressure.",
            hit_volume=round(hit, 1), trades=len(dq),
        )]


# ---------------------------------------------------------------------------
@DETECTORS.register("sweep")
class SweepDetector(Detector):
    """Multiple price levels consumed by one aggressive burst."""

    name = "sweep"
    WINDOW = 1.5
    MIN_LEVELS = 3

    def __init__(self, tick_size: float) -> None:
        super().__init__(tick_size)
        self._recent: deque[tuple[float, Side, float, float]] = deque()
        self._last_emit = NEVER

    def on_trade(self, ctx: BookContext, price: float, size: float, aggressor: Side) -> list[Signal]:
        self._recent.append((ctx.ts, aggressor, price, size))
        while self._recent and ctx.ts - self._recent[0][0] > self.WINDOW:
            self._recent.popleft()
        burst = [(t, s, p, q) for t, s, p, q in self._recent if s is aggressor]
        prices = {p for _, _, p, _ in burst}
        if len(prices) < self.MIN_LEVELS:
            return []
        if ctx.ts - self._last_emit < 5.0:
            return []
        span = (max(prices) - min(prices)) / self.tick
        vol = sum(q for _, _, _, q in burst)
        self._last_emit = ctx.ts
        conf = clamp(0.4 + 0.06 * len(prices) + 0.05 * (span / 5))
        direction = "upside" if aggressor is Side.BUY else "downside"
        return [self._signal(
            ctx, SignalType.SWEEP, aggressor, price, conf,
            f"Aggressive {aggressor.value} burst swept {len(prices)} levels "
            f"({span:.0f} ticks, {vol:,.0f} volume) in {self.WINDOW}s — {direction} liquidity sweep.",
            levels=len(prices), span_ticks=span, volume=round(vol, 1),
        )]


# ---------------------------------------------------------------------------
@DETECTORS.register("imbalance")
class ImbalanceDetector(Detector):
    """Bid/ask depth imbalance, evaluated on the periodic tick."""

    name = "imbalance"
    LEVELS = 10
    THRESH = 0.65

    def __init__(self, tick_size: float) -> None:
        super().__init__(tick_size)
        self._last_emit = NEVER

    def on_tick(self, ctx: BookContext) -> list[Signal]:
        bid_d = ctx.book.depth(Side.BUY, self.LEVELS)
        ask_d = ctx.book.depth(Side.SELL, self.LEVELS)
        total = bid_d + ask_d
        if total <= 0 or ctx.ts - self._last_emit < 8.0:
            return []
        ratio = bid_d / total
        if self.THRESH < ratio or ratio < 1 - self.THRESH:
            self._last_emit = ctx.ts
            side = Side.BUY if ratio > 0.5 else Side.SELL
            pct = max(ratio, 1 - ratio) * 100
            conf = clamp((max(ratio, 1 - ratio) - self.THRESH) / (1 - self.THRESH) * 0.6 + 0.3)
            return [self._signal(
                ctx, SignalType.IMBALANCE, side, ctx.book.mid or 0.0, conf,
                f"Top-{self.LEVELS} depth is {pct:.0f}% {side.value}-side "
                f"({bid_d:,.0f} bid vs {ask_d:,.0f} ask) — book imbalance favours {side.value}ers.",
                bid_depth=round(bid_d, 1), ask_depth=round(ask_d, 1), ratio=round(ratio, 3),
            )]
        return []


# ---------------------------------------------------------------------------
@DETECTORS.register("delta_imbalance")
class DeltaImbalanceDetector(Detector):
    """Rolling delta (buy vol − sell vol) z-score — aggressive flow skew."""

    name = "delta_imbalance"
    WINDOW = 30.0

    def __init__(self, tick_size: float) -> None:
        super().__init__(tick_size)
        self._trades: deque[tuple[float, float]] = deque()  # ts, signed size
        self.delta_stats = RollingStats(window=200)
        self._last_emit = NEVER
        self._last_sample = NEVER

    def on_trade(self, ctx: BookContext, price: float, size: float, aggressor: Side) -> list[Signal]:
        self._trades.append((ctx.ts, size if aggressor is Side.BUY else -size))
        while self._trades and ctx.ts - self._trades[0][0] > self.WINDOW:
            self._trades.popleft()
        return []

    def on_tick(self, ctx: BookContext) -> list[Signal]:
        if ctx.ts - self._last_sample < 2.0:
            return []
        self._last_sample = ctx.ts
        delta = sum(q for _, q in self._trades)
        self.delta_stats.add(delta)
        if len(self.delta_stats) < 30 or ctx.ts - self._last_emit < 10.0:
            return []
        z = self.delta_stats.zscore(delta)
        if abs(z) < 2.0:
            return []
        self._last_emit = ctx.ts
        side = Side.BUY if z > 0 else Side.SELL
        conf = clamp(0.4 + 0.12 * (abs(z) - 2))
        return [self._signal(
            ctx, SignalType.DELTA_IMBALANCE, side, ctx.book.mid or 0.0, conf,
            f"{self.WINDOW:.0f}s delta {delta:+,.0f} is {abs(z):.1f}σ from normal — "
            f"aggressive {side.value}ers dominating the tape.",
            delta=round(delta, 1), zscore=round(z, 2),
        ), self._signal(
            ctx, SignalType.AGGRESSIVE_FLOW, side, ctx.book.mid or 0.0, clamp(conf * 0.9),
            f"Sustained aggressive {side.value}ing: rolling delta {delta:+,.0f}.",
            delta=round(delta, 1),
        )]


# ---------------------------------------------------------------------------
@DETECTORS.register("large_participant")
class LargeParticipantDetector(Detector):
    """Individual prints far out on the size distribution."""

    name = "large_participant"

    def __init__(self, tick_size: float) -> None:
        super().__init__(tick_size)
        self.size_stats = RollingStats(window=600)
        self._last_emit = NEVER

    def on_trade(self, ctx: BookContext, price: float, size: float, aggressor: Side) -> list[Signal]:
        self.size_stats.add(size)
        if len(self.size_stats) < 100:
            return []
        z = self.size_stats.zscore(size)
        if z < 4.0 or ctx.ts - self._last_emit < 5.0:
            return []
        self._last_emit = ctx.ts
        conf = clamp(0.5 + 0.06 * (z - 4))
        return [self._signal(
            ctx, SignalType.LARGE_PARTICIPANT, aggressor, price, conf,
            f"Block print: {size:,.0f} {aggressor.value} at {price:g} ({z:.1f}σ above typical size) — "
            f"large participant active on the {aggressor.value} side.",
            size=size, zscore=round(z, 2),
        )]


# ---------------------------------------------------------------------------
@DETECTORS.register("liquidity_migration")
class LiquidityMigrationDetector(Detector):
    """Depth centre-of-mass drifting — passive money repositioning ahead of
    a move (bid mass rising toward price = buyers stepping up)."""

    name = "liquidity_migration"

    def __init__(self, tick_size: float) -> None:
        super().__init__(tick_size)
        self._history: deque[tuple[float, float, float]] = deque(maxlen=60)  # ts, bid_com, ask_com
        self._last_emit = NEVER
        self._last_sample = NEVER

    def on_tick(self, ctx: BookContext) -> list[Signal]:
        if ctx.ts - self._last_sample < 1.0:
            return []
        self._last_sample = ctx.ts
        b = ctx.book.depth_center_of_mass(Side.BUY)
        a = ctx.book.depth_center_of_mass(Side.SELL)
        if b is None or a is None:
            return []
        self._history.append((ctx.ts, b, a))
        if len(self._history) < 20 or ctx.ts - self._last_emit < 20.0:
            return []
        t0, b0, a0 = self._history[0]
        db = (b - b0) / self.tick
        da = (a - a0) / self.tick
        # both sides shifting the same way = liquidity migrating with intent
        if abs(db) < 3 or abs(da) < 3 or (db > 0) != (da > 0):
            return []
        self._last_emit = ctx.ts
        side = Side.BUY if db > 0 else Side.SELL
        conf = clamp(0.35 + 0.04 * min(abs(db), abs(da)))
        direction = "higher" if side is Side.BUY else "lower"
        return [self._signal(
            ctx, SignalType.LIQUIDITY_MIGRATION, side, ctx.book.mid or 0.0, conf,
            f"Passive liquidity migrating {direction}: bid mass {db:+.0f} ticks, ask mass {da:+.0f} ticks "
            f"over {ctx.ts - t0:.0f}s — makers repositioning for {direction} prices.",
            bid_shift_ticks=round(db, 1), ask_shift_ticks=round(da, 1),
        )]


# ---------------------------------------------------------------------------
@DETECTORS.register("liquidity_dynamics")
class LiquidityDynamicsDetector(Detector):
    """Tracks add/pull rates per side; emits pulled-liquidity, added-liquidity
    and exhaustion signals from the balance of the two."""

    name = "liquidity_dynamics"
    WINDOW = 10.0

    def __init__(self, tick_size: float) -> None:
        super().__init__(tick_size)
        self._flows: deque[tuple[float, str, float]] = deque()  # ts, 'add'/'pull' + side, qty
        self._last_emit = NEVER

    def on_delta(self, ctx: BookContext, side: Side, price: float, new_size: float, prev_size: float) -> list[Signal]:
        diff = new_size - prev_size
        kind = ("add-" if diff > 0 else "pull-") + side.value
        self._flows.append((ctx.ts, kind, abs(diff)))
        while self._flows and ctx.ts - self._flows[0][0] > self.WINDOW:
            self._flows.popleft()
        return []

    def on_tick(self, ctx: BookContext) -> list[Signal]:
        if ctx.ts - self._last_emit < 12.0 or not self._flows:
            return []
        sums: dict[str, float] = defaultdict(float)
        for _, kind, qty in self._flows:
            sums[kind] += qty
        out: list[Signal] = []
        for side in (Side.BUY, Side.SELL):
            added = sums[f"add-{side.value}"]
            pulled = sums[f"pull-{side.value}"]
            total = added + pulled
            if total < 200:
                continue
            if pulled > 2.5 * max(added, 1e-9):
                self._last_emit = ctx.ts
                out.append(self._signal(
                    ctx, SignalType.PULLED_LIQUIDITY, side.opposite, ctx.book.mid or 0.0,
                    clamp(0.4 + 0.1 * (pulled / max(added, 1) - 2.5)),
                    f"{side.value.capitalize()}-side liquidity being pulled: {pulled:,.0f} removed vs "
                    f"{added:,.0f} added in {self.WINDOW:.0f}s — {side.value} support is thinning.",
                    added=round(added, 0), pulled=round(pulled, 0),
                ))
            elif added > 2.5 * max(pulled, 1e-9):
                self._last_emit = ctx.ts
                out.append(self._signal(
                    ctx, SignalType.ADDED_LIQUIDITY, side, ctx.book.mid or 0.0,
                    clamp(0.35 + 0.08 * (added / max(pulled, 1) - 2.5)),
                    f"{side.value.capitalize()}-side liquidity building: {added:,.0f} added vs "
                    f"{pulled:,.0f} pulled in {self.WINDOW:.0f}s.",
                    added=round(added, 0), pulled=round(pulled, 0),
                ))
        # exhaustion: both sides thinning while flow continues
        b_net = sums["add-buy"] - sums["pull-buy"]
        a_net = sums["add-sell"] - sums["pull-sell"]
        if b_net < -300 and a_net < -300:
            self._last_emit = ctx.ts
            out.append(self._signal(
                ctx, SignalType.LIQUIDITY_EXHAUSTION, None, ctx.book.mid or 0.0,
                clamp(0.4 + 0.0002 * (abs(b_net) + abs(a_net))),
                f"Liquidity exhaustion: both sides net-pulling (bids {b_net:+,.0f}, asks {a_net:+,.0f}) — "
                "expect faster, less orderly moves.",
                bid_net=round(b_net, 0), ask_net=round(a_net, 0),
            ))
        return out


# ---------------------------------------------------------------------------
@DETECTORS.register("hidden_liquidity")
class HiddenLiquidityDetector(Detector):
    """Trades printing at prices with little/no displayed size — dark or
    pegged liquidity executing inside the visible book."""

    name = "hidden_liquidity"

    def __init__(self, tick_size: float) -> None:
        super().__init__(tick_size)
        self._hits: deque[tuple[float, float]] = deque()
        self._last_emit = NEVER

    def on_trade(self, ctx: BookContext, price: float, size: float, aggressor: Side) -> list[Signal]:
        passive_book = ctx.book.bids if aggressor is Side.SELL else ctx.book.asks
        visible = passive_book.get(ctx.book.round_price(price), 0.0)
        if visible < size * 0.25:  # printed well beyond displayed
            self._hits.append((ctx.ts, size))
        while self._hits and ctx.ts - self._hits[0][0] > 30:
            self._hits.popleft()
        if len(self._hits) < 6 or ctx.ts - self._last_emit < 15.0:
            return []
        self._last_emit = ctx.ts
        vol = sum(q for _, q in self._hits)
        return [self._signal(
            ctx, SignalType.HIDDEN_LIQUIDITY, aggressor.opposite, price, clamp(0.35 + 0.02 * len(self._hits)),
            f"{len(self._hits)} prints ({vol:,.0f}) executed against near-zero displayed size in 30s — "
            "hidden liquidity is active inside the spread.",
            prints=len(self._hits), volume=round(vol, 1),
        )]
