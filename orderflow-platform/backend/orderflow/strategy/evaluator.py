"""Strategy evaluation against the live pipeline state."""
from __future__ import annotations

import datetime as dt
from collections import deque
from dataclasses import dataclass

from ..core import Side, Signal, SignalType, Topic
from ..pipeline import Pipeline


@dataclass
class FeatureSnapshot:
    ts: float
    metrics: dict[str, float]
    signals: list[Signal]  # recent, newest last


class FeatureCollector:
    """Maintains the metric dict + rolling signal window for one pipeline."""

    def __init__(self, pipeline: Pipeline) -> None:
        self.p = pipeline
        self.signals: deque[Signal] = deque(maxlen=400)
        self.trades: deque[tuple[float, float, float]] = deque()  # ts, signed size, size
        self.session_open_ts: float | None = None
        pipeline.bus.subscribe(Topic.SIGNAL, self.signals.append)
        pipeline.bus.subscribe(Topic.TRADE, self._on_trade)

    def _on_trade(self, ev) -> None:
        if self.session_open_ts is None:
            self.session_open_ts = ev.ts
        signed = ev.size if ev.aggressor is Side.BUY else -ev.size
        self.trades.append((ev.ts, signed, ev.size))
        while self.trades and ev.ts - self.trades[0][0] > 30.0:
            self.trades.popleft()

    def snapshot(self, ts: float) -> FeatureSnapshot:
        p = self.p
        book = p.book_engine.book
        lf = p.liquidity.features(ts)
        price = p.book_engine.last_trade_price or book.mid or 0.0
        sf = p.structure.features(price)
        a = p.fusion.last_assessment
        metrics = {
            "price": price,
            "mid": book.mid or price,
            "spread_ticks": lf["spread_ticks"],
            "book_imbalance": lf["book_imbalance"],
            "bid_depth": lf["bid_depth"],
            "ask_depth": lf["ask_depth"],
            "delta_30s": sum(s for _, s, _ in self.trades),
            "volume_30s": sum(q for _, _, q in self.trades),
            "cum_delta": lf["cum_delta"],
            "vwap_distance": lf["vwap_distance"],
            "range_position": sf["range_position"],
            "trend": sf["trend_up"] - sf["trend_down"],
            "control_score": a.control_score if a else 0.0,
            "p_continuation": a.p_continuation if a else 0.5,
            "p_reversal": a.p_reversal if a else 0.5,
            "minutes_since_open": (ts - self.session_open_ts) / 60.0 if self.session_open_ts else 0.0,
        }
        recent = [s for s in self.signals if ts - s.ts <= 120.0]
        return FeatureSnapshot(ts=ts, metrics=metrics, signals=recent)


OPS = {
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "abs>": lambda a, b: abs(a) > b,
    "abs<": lambda a, b: abs(a) < b,
}


class StrategyEvaluator:
    def __init__(self, strategy: dict) -> None:
        self.s = strategy
        self.last_entry_ts = 0.0

    # -- condition primitives ------------------------------------------------
    def _cond(self, cond: dict, snap: FeatureSnapshot, direction: Side | None) -> bool:
        if "metric" in cond:
            val = snap.metrics.get(cond["metric"], 0.0)
            return OPS[cond["op"]](val, cond["value"])
        want = SignalType(cond["signal"])
        within = cond.get("within_s", 60.0)
        min_conf = cond.get("min_confidence", 0.5)
        side_req = cond.get("side")
        for sig in reversed(snap.signals):
            if snap.ts - sig.ts > within:
                break
            if sig.type is not want or sig.confidence < min_conf:
                continue
            if side_req in (None,):
                return True
            if side_req in ("buy", "sell"):
                if sig.side is not None and sig.side.value == side_req:
                    return True
            elif direction is not None and sig.side is not None:
                match = sig.side is direction if side_req == "same" else sig.side is not direction
                if match:
                    return True
        return False

    def _group(self, group: dict, snap: FeatureSnapshot, direction: Side | None) -> bool:
        def eval_node(node: dict) -> bool:
            if set(node) & {"all", "any"}:
                return self._group(node, snap, direction)
            return self._cond(node, snap, direction)

        if "all" in group and not all(eval_node(c) for c in group["all"]):
            return False
        if "any" in group and not any(eval_node(c) for c in group["any"]):
            return False
        return True

    # -- filters ------------------------------------------------------------
    def _filters_pass(self, snap: FeatureSnapshot) -> bool:
        f = self.s.get("filters", {})
        if snap.ts - self.last_entry_ts < f.get("cooldown_s", 60):
            return False
        wins = f.get("time_windows")
        if wins:
            t = dt.datetime.fromtimestamp(snap.ts, tz=dt.timezone.utc).time()
            ok = False
            for start, end in wins:
                t0 = dt.time.fromisoformat(start)
                t1 = dt.time.fromisoformat(end)
                if t0 <= t <= t1:
                    ok = True
                    break
            if not ok:
                return False
        if len([s for s in snap.signals if snap.ts - s.ts < 60]) < f.get("min_active_signals", 0):
            return False
        return True

    # -- public API -----------------------------------------------------------
    def entry_signal(self, snap: FeatureSnapshot) -> Side | None:
        if not self._filters_pass(snap):
            return None
        directions = {"buy": [Side.BUY], "sell": [Side.SELL], "both": [Side.BUY, Side.SELL]}[
            self.s.get("direction", "both")
        ]
        for d in directions:
            if self._group(self.s["entry"], snap, d):
                # directional sanity: a 'both' strategy trades with control
                if self.s.get("direction") == "both":
                    ctrl = snap.metrics.get("control_score", 0.0)
                    if (d is Side.BUY and ctrl < 0) or (d is Side.SELL and ctrl > 0):
                        continue
                self.last_entry_ts = snap.ts
                return d
        return None

    def exit_signal(self, snap: FeatureSnapshot, position_side: Side) -> bool:
        exit_block = self.s.get("exit")
        if not exit_block:
            return False
        return self._group(exit_block, snap, position_side)
