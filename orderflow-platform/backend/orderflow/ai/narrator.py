"""The trading assistant's voice: converts signals and assessments into a
running plain-language narration of the market."""
from __future__ import annotations

from ..core import Assessment, EventBus, Narration, Signal, SignalType, Topic

# Which signal types are worth speaking about, and at what confidence.
SPEAK_THRESHOLDS: dict[SignalType, float] = {
    SignalType.ABSORPTION: 0.5,
    SignalType.ICEBERG: 0.5,
    SignalType.SPOOFING: 0.55,
    SignalType.STOP_HUNT: 0.5,
    SignalType.FAKE_BREAKOUT: 0.5,
    SignalType.ACCUMULATION: 0.5,
    SignalType.DISTRIBUTION: 0.5,
    SignalType.LIQUIDITY_WALL: 0.6,
    SignalType.SWEEP: 0.6,
    SignalType.CHOCH: 0.6,
    SignalType.BOS: 0.65,
    SignalType.TRAPPED_BUYERS: 0.55,
    SignalType.TRAPPED_SELLERS: 0.55,
    SignalType.LIQUIDITY_EXHAUSTION: 0.55,
    SignalType.DELTA_IMBALANCE: 0.6,
    SignalType.LARGE_PARTICIPANT: 0.6,
    SignalType.HIDDEN_LIQUIDITY: 0.55,
}

ALERT_TYPES = {SignalType.STOP_HUNT, SignalType.FAKE_BREAKOUT, SignalType.SPOOFING,
               SignalType.CHOCH, SignalType.LIQUIDITY_EXHAUSTION}


class Narrator:
    def __init__(self, bus: EventBus, symbol: str, min_gap_s: float = 3.0) -> None:
        self.bus = bus
        self.symbol = symbol
        self.min_gap = min_gap_s
        self.history: list[Narration] = []
        self._last_by_type: dict[SignalType, float] = {}
        self._last_assessment_text = ""
        self._last_assessment_ts: float | None = None
        self._last_control = ""
        bus.subscribe(Topic.SIGNAL, self._on_signal)
        bus.subscribe(Topic.ASSESSMENT, self._on_assessment)

    def _say(self, ts: float, text: str, severity: str = "info",
             related: SignalType | None = None) -> None:
        n = Narration(ts=ts, symbol=self.symbol, text=text, severity=severity, related_signal=related)
        self.history.append(n)
        if len(self.history) > 300:
            del self.history[:100]
        self.bus.publish(Topic.NARRATION, n)

    def _on_signal(self, sig: Signal) -> None:
        thresh = SPEAK_THRESHOLDS.get(sig.type)
        if thresh is None or sig.confidence < thresh:
            return
        last = self._last_by_type.get(sig.type)
        if last is not None and sig.ts - last < self.min_gap:
            return
        self._last_by_type[sig.type] = sig.ts
        severity = "alert" if sig.type in ALERT_TYPES else "notice"
        self._say(sig.ts, f"{sig.explanation} (confidence {sig.confidence:.0%})", severity, sig.type)

    def _on_assessment(self, a: Assessment) -> None:
        parts = [f"Control: {a.control} ({a.control_score:+.2f})."]
        if a.institutional_bias != "neutral":
            parts.append(f"Institutions look to be {a.institutional_bias}.")
        parts.append(f"Continuation {a.p_continuation:.0%} / reversal {a.p_reversal:.0%}.")
        if a.trade_plan:
            tp = a.trade_plan
            parts.append(
                f"Best plan: {tp['direction']} near {tp['entry']:g}, stop {tp['stop']:g}, "
                f"target {tp['targets'][0]:g} (R:R {tp['risk_reward']:.1f})."
            )
        text = " ".join(parts)
        # only speak when the story changes — silence is information too:
        # a control/bias flip speaks immediately, otherwise summarise at
        # most every 20s (probabilities wiggle constantly; that's not news)
        story = f"{a.control}|{a.institutional_bias}|{bool(a.trade_plan)}"
        changed = story != self._last_control
        stale = self._last_assessment_ts is None or a.ts - self._last_assessment_ts >= 20.0
        if (changed or stale) and text != self._last_assessment_text:
            self._last_control = story
            self._last_assessment_ts = a.ts
            self._last_assessment_text = text
            self._say(a.ts, text, "info")

    def as_ui(self, last_n: int = 40) -> list[dict]:
        return [
            {"ts": n.ts, "text": n.text, "severity": n.severity,
             "signal": n.related_signal.value if n.related_signal else None}
            for n in self.history[-last_n:]
        ]
