"""Automatic strategy backtesting: run a no-code strategy over recorded
events through the full pipeline and produce a performance report."""
from __future__ import annotations

from ..analytics.performance import analyze
from ..core import Config, Event, Side, Topic
from ..pipeline import Pipeline
from .evaluator import FeatureCollector, StrategyEvaluator
from .schema import validate


class StrategyBacktester:
    def __init__(self, strategy: dict, config: Config | None = None) -> None:
        self.strategy = validate(dict(strategy))
        self.config = config or Config()

    def run(self, events: list[tuple[Topic, Event]], eval_interval_s: float = 1.0) -> dict:
        pipeline = Pipeline(self.config)
        collector = FeatureCollector(pipeline)
        evaluator = StrategyEvaluator(self.strategy)
        broker = pipeline.broker
        tick = pipeline.config.tick_size
        risk_cfg = self.strategy["risk"]
        pipeline.risk.cfg.risk_per_trade_pct = risk_cfg["risk_per_trade_pct"]

        last_eval = 0.0
        evaluations = 0
        for topic, ev in events:
            pipeline.bus.publish(topic, ev)
            if ev.ts - last_eval < eval_interval_s:
                continue
            last_eval = ev.ts
            evaluations += 1
            snap = collector.snapshot(ev.ts)
            pos = broker.position
            if pos.size != 0:
                side = Side.BUY if pos.size > 0 else Side.SELL
                if evaluator.exit_signal(snap, side):
                    broker.flatten(ev.ts)
                continue
            entry_side = evaluator.entry_signal(snap)
            if entry_side is None:
                continue
            price = snap.metrics["price"]
            if price <= 0:
                continue
            stop_off = risk_cfg["stop_ticks"] * tick
            stop = price - stop_off if entry_side is Side.BUY else price + stop_off
            tp = price + stop_off * risk_cfg["rr"] if entry_side is Side.BUY else price - stop_off * risk_cfg["rr"]
            sizing = pipeline.risk.position_size(price, stop, risk_cfg["rr"])
            if sizing.size <= 0:
                continue
            broker.submit(entry_side, sizing.size, stop=stop, take_profit=tp,
                          setup=self.strategy["name"], trader=self.strategy["name"])

        if broker.position.size != 0 and events:
            broker.flatten(events[-1][1].ts)
        report = analyze(broker.closed_trades)
        return {
            "strategy": self.strategy["name"],
            "evaluations": evaluations,
            "report": report.as_dict(),
            "trades": [vars(t) for t in broker.closed_trades[-100:]],
            "risk": pipeline.risk.summary(),
        }
