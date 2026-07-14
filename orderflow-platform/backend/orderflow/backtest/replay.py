"""Tick-by-tick replay engine with full transport controls.

Drives recorded events through a fresh Pipeline. Supports pause/resume,
speed control (fast-forward & slow motion), jump-to-time, trading during
replay through the paper broker, and records AI decisions alongside user
trades so AI-vs-trader performance can be compared afterwards.

``step()`` is synchronous and pull-based, so the engine works both headless
(backtests: call ``run_to_end``) and interactively (server pumps steps on a
timer honouring ``speed``).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..analytics.performance import compare
from ..core import Config, Event, Side, Topic
from ..feeds.recorder import load_events
from ..pipeline import Pipeline


@dataclass
class ReplayStatus:
    playing: bool = False
    speed: float = 1.0
    cursor: int = 0
    total: int = 0
    ts: float = 0.0
    start_ts: float = 0.0
    end_ts: float = 0.0
    finished: bool = False

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in
                ("playing", "speed", "cursor", "total", "ts", "start_ts", "end_ts", "finished")}


@dataclass
class AIDecisionLog:
    """Snapshot of what the AI believed at a moment a decision mattered."""

    records: list[dict] = field(default_factory=list)

    def record(self, ts: float, kind: str, detail: dict) -> None:
        self.records.append({"ts": ts, "kind": kind, **detail})


class ReplayEngine:
    def __init__(self, events: list[tuple[Topic, Event]] | None = None,
                 path: str | None = None, config: Config | None = None) -> None:
        if events is None:
            if path is None:
                raise ValueError("provide events or path")
            events = load_events(path)
        self.events = events
        self.config = config or Config()
        self.pipeline = Pipeline(self.config)
        self.status = ReplayStatus(total=len(events))
        if events:
            self.status.start_ts = events[0][1].ts
            self.status.end_ts = events[-1][1].ts
            self.status.ts = self.status.start_ts
        self.ai_log = AIDecisionLog()
        self._auto_trade_ai = False
        self._last_ai_plan_ts = 0.0

    # -- transport --------------------------------------------------------
    def play(self) -> None:
        self.status.playing = True

    def pause(self) -> None:
        self.status.playing = False

    def set_speed(self, speed: float) -> None:
        """>1 fast-forward, <1 slow motion."""
        self.status.speed = max(0.05, min(500.0, speed))

    def jump_to(self, ts: float) -> None:
        """Jump to a timestamp. Backward jumps rebuild the pipeline from the
        start of the recording (state must be re-derived, never interpolated)."""
        if ts < self.status.ts:
            self.pipeline = Pipeline(self.config)
            self.status.cursor = 0
            self.status.finished = False
        while self.status.cursor < len(self.events) and self.events[self.status.cursor][1].ts < ts:
            self._publish_next()

    def enable_ai_trading(self, enabled: bool = True) -> None:
        """Let the AI take its own trade plans during replay (tagged 'ai')."""
        self._auto_trade_ai = enabled

    # -- stepping ------------------------------------------------------------
    def step(self, n: int = 1) -> int:
        """Publish up to n events; returns how many were published."""
        done = 0
        while done < n and self.status.cursor < len(self.events):
            self._publish_next()
            done += 1
        if self.status.cursor >= len(self.events):
            self.status.finished = True
            self.status.playing = False
        return done

    def step_seconds(self, seconds: float) -> int:
        """Advance replay clock by ``seconds`` of market time."""
        target = self.status.ts + seconds
        done = 0
        while self.status.cursor < len(self.events) and self.events[self.status.cursor][1].ts <= target:
            self._publish_next()
            done += 1
        if self.status.cursor >= len(self.events):
            self.status.finished = True
            self.status.playing = False
        return done

    def run_to_end(self) -> None:
        while self.status.cursor < len(self.events):
            self._publish_next()
        self.status.finished = True
        self.status.playing = False

    def _publish_next(self) -> None:
        topic, ev = self.events[self.status.cursor]
        self.status.cursor += 1
        self.status.ts = ev.ts
        self.pipeline.bus.publish(topic, ev)
        if self._auto_trade_ai:
            self._maybe_ai_trade(ev.ts)

    # -- trading during replay ---------------------------------------------------
    def user_trade(self, side: str, size: float, stop: float | None = None,
                   take_profit: float | None = None) -> dict:
        order = self.pipeline.broker.submit(
            Side(side), size, stop=stop, take_profit=take_profit, setup="replay-manual", trader="trader",
        )
        a = self.pipeline.fusion.last_assessment
        self.ai_log.record(self.status.ts, "user_trade", {
            "side": side, "size": size,
            "ai_view": a.as_dict() if a else None,  # what the AI thought when the human pulled the trigger
        })
        return {"order_id": order.id, "status": order.status}

    def _maybe_ai_trade(self, ts: float) -> None:
        a = self.pipeline.fusion.last_assessment
        if not a or not a.trade_plan or self.pipeline.broker.position.size != 0:
            return
        if ts - self._last_ai_plan_ts < 60:  # one AI decision per minute max
            return
        plan = a.trade_plan
        if plan["confidence"] < 0.5:
            return
        self._last_ai_plan_ts = ts
        sizing = self.pipeline.risk.position_size(plan["entry"], plan["stop"])
        if sizing.size <= 0:
            return
        order = self.pipeline.broker.submit(
            Side(plan["direction"]), sizing.size, stop=plan["stop"],
            take_profit=plan["targets"][0], setup="ai-plan", trader="ai",
        )
        self.ai_log.record(ts, "ai_trade", {"plan": plan, "order_status": order.status})

    # -- results ---------------------------------------------------------------
    def performance(self) -> dict:
        trades = self.pipeline.broker.closed_trades
        groups = {
            "trader": [t for t in trades if t.trader == "trader"],
            "ai": [t for t in trades if t.trader == "ai"],
        }
        return {
            "comparison": compare({k: v for k, v in groups.items() if v}),
            "broker": self.pipeline.broker.summary(),
            "risk": self.pipeline.risk.summary(),
            "ai_decisions": self.ai_log.records[-200:],
        }
