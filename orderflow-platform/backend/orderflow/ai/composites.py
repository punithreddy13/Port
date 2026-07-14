"""Composite detections that need BOTH order flow and market structure:
stop hunts, fake breakouts, trapped traders, accumulation/distribution.

These subscribe to the signal stream itself — they are consumers of
lower-level detections, which keeps each base engine single-purpose.
"""
from __future__ import annotations

from collections import deque

from ..core import EventBus, Side, Signal, SignalType, Topic, TradeEvent
from ..core.models import clamp
from ..structure.engine import MarketStructureEngine


class CompositeDetector:
    LOOKBACK = 60.0

    def __init__(self, bus: EventBus, structure: MarketStructureEngine, symbol: str, tick: float) -> None:
        self.bus = bus
        self.structure = structure
        self.symbol = symbol
        self.tick = tick
        self.recent_signals: deque[Signal] = deque(maxlen=300)
        self.recent_trades: deque[TradeEvent] = deque(maxlen=800)
        self._sweep_watch: list[dict] = []  # sweeps waiting for reclaim/failure
        self._breakout_watch: list[dict] = []
        self._last_accdist = float("-inf")
        bus.subscribe(Topic.SIGNAL, self._on_signal)
        bus.subscribe(Topic.TRADE, self._on_trade)

    def _emit(self, ts: float, type_: SignalType, side: Side | None, price: float,
              conf: float, explanation: str, **data: object) -> None:
        self.bus.publish(Topic.SIGNAL, Signal(
            ts=ts, symbol=self.symbol, type=type_, side=side, price=price,
            confidence=clamp(conf, 0.0, 0.95), explanation=explanation,
            data=dict(data), source="composite",
        ))

    # ------------------------------------------------------------------
    def _on_signal(self, sig: Signal) -> None:
        if sig.source == "composite":
            return
        self.recent_signals.append(sig)
        if sig.type is SignalType.SWEEP:
            self._arm_stop_hunt(sig)
        elif sig.type in (SignalType.BOS, SignalType.CHOCH):
            self._arm_breakout(sig)

    def _arm_stop_hunt(self, sweep: Signal) -> None:
        pools = self.structure.state.liquidity_pools()
        if sweep.side is Side.SELL:
            hit = [p for p in pools if p["side"] == "below" and abs(p["price"] - sweep.price) <= 6 * self.tick]
        else:
            hit = [p for p in pools if p["side"] == "above" and abs(p["price"] - sweep.price) <= 6 * self.tick]
        if hit:
            self._sweep_watch.append({
                "ts": sweep.ts, "side": sweep.side, "price": sweep.price,
                "pool": hit[0], "deadline": sweep.ts + 45.0,
            })

    def _arm_breakout(self, sig: Signal) -> None:
        self._breakout_watch.append({
            "ts": sig.ts, "side": sig.side, "level": sig.price, "deadline": sig.ts + 60.0,
        })

    # ------------------------------------------------------------------
    def _on_trade(self, ev: TradeEvent) -> None:
        self.recent_trades.append(ev)
        self._check_stop_hunts(ev)
        self._check_breakouts(ev)
        self._check_accumulation(ev)

    def _check_stop_hunts(self, ev: TradeEvent) -> None:
        for w in list(self._sweep_watch):
            if ev.ts > w["deadline"]:
                self._sweep_watch.remove(w)
                continue
            # sweep down through a low pool then price back above → stop hunt
            reclaimed = ev.price > w["pool"]["price"] + 2 * self.tick if w["side"] is Side.SELL \
                else ev.price < w["pool"]["price"] - 2 * self.tick
            if reclaimed:
                self._sweep_watch.remove(w)
                bias = Side.BUY if w["side"] is Side.SELL else Side.SELL
                self._emit(
                    ev.ts, SignalType.STOP_HUNT, bias, w["pool"]["price"],
                    0.6 + 0.1 * (w["pool"]["kind"].startswith("equal")),
                    f"Sweep {'below' if w['side'] is Side.SELL else 'above'} the "
                    f"{w['pool']['kind'].replace('_', ' ')} at {w['pool']['price']:g} was immediately "
                    f"reclaimed — stops were harvested; bias flips {bias.value}.",
                    pool=w["pool"], sweep_ts=w["ts"],
                )
                trapped = SignalType.TRAPPED_SELLERS if bias is Side.BUY else SignalType.TRAPPED_BUYERS
                who = "sellers who chased the low" if bias is Side.BUY else "buyers who chased the high"
                self._emit(ev.ts, trapped, bias, ev.price, 0.55,
                           f"The failed sweep leaves {who} trapped — their exits fuel the {bias.value} side.")

    def _check_breakouts(self, ev: TradeEvent) -> None:
        for w in list(self._breakout_watch):
            if ev.ts > w["deadline"]:
                self._breakout_watch.remove(w)
                continue
            failed = ev.price < w["level"] - 3 * self.tick if w["side"] is Side.BUY \
                else ev.price > w["level"] + 3 * self.tick
            if failed:
                self._breakout_watch.remove(w)
                bias = w["side"].opposite
                # confirming evidence: opposing absorption/spoof around the level?
                support = [s for s in self.recent_signals
                           if ev.ts - s.ts < 60 and s.side is bias
                           and s.type in (SignalType.ABSORPTION, SignalType.SPOOFING, SignalType.DELTA_IMBALANCE)]
                conf = clamp(0.5 + 0.08 * len(support))
                self._emit(
                    ev.ts, SignalType.FAKE_BREAKOUT, bias, w["level"], conf,
                    f"Breakout {'above' if w['side'] is Side.BUY else 'below'} {w['level']:g} failed — price is back "
                    f"{'below' if w['side'] is Side.BUY else 'above'} the level"
                    + (f" with {len(support)} opposing order-flow signals" if support else "")
                    + "; breakout traders are offside.",
                    level=w["level"], supporting_signals=len(support),
                )
                trapped = SignalType.TRAPPED_BUYERS if w["side"] is Side.BUY else SignalType.TRAPPED_SELLERS
                self._emit(ev.ts, trapped, bias, ev.price, clamp(conf - 0.05),
                           f"{'Buyers' if w['side'] is Side.BUY else 'Sellers'} who entered the failed breakout "
                           f"at {w['level']:g} are trapped.")

    def _check_accumulation(self, ev: TradeEvent) -> None:
        """Institutional accumulation: persistent passive absorption + positive
        delta while price stays rangebound (distribution = mirror image)."""
        if ev.ts - self._last_accdist < 45.0 or len(self.recent_trades) < 200:
            return
        window = [t for t in self.recent_trades if ev.ts - t.ts <= 90.0]
        if len(window) < 100:
            return
        prices = [t.price for t in window]
        rng = max(prices) - min(prices)
        mid = sum(prices) / len(prices)
        if mid <= 0 or rng / mid > 0.004:  # must be rangebound
            return
        delta = sum(t.size if t.aggressor is Side.BUY else -t.size for t in window)
        volume = sum(t.size for t in window)
        if volume <= 0 or abs(delta) / volume < 0.18:
            return
        absorbs = [s for s in self.recent_signals if ev.ts - s.ts < 90
                   and s.type in (SignalType.ABSORPTION, SignalType.ICEBERG)]
        side = Side.BUY if delta > 0 else Side.SELL
        matching_absorbs = [s for s in absorbs if s.side is side]
        if not matching_absorbs:
            return
        self._last_accdist = ev.ts
        type_ = SignalType.ACCUMULATION if side is Side.BUY else SignalType.DISTRIBUTION
        verb = "accumulating" if side is Side.BUY else "distributing"
        self._emit(
            ev.ts, type_, side, ev.price,
            clamp(0.45 + 0.06 * len(matching_absorbs) + 0.3 * abs(delta) / volume),
            f"Price is rangebound while delta runs {delta:+,.0f} on {volume:,.0f} volume with "
            f"{len(matching_absorbs)} absorption/iceberg events on the {side.value} side — "
            f"institutions appear to be quietly {verb}.",
            delta=round(delta, 0), volume=round(volume, 0), range_pct=round(rng / mid * 100, 3),
        )
