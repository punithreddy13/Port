"""Signal fusion: the AI analyst.

Aggregates the decayed signal stream + engine features into a single
Assessment: who is in control, institutional bias, continuation/reversal
probabilities, and a concrete trade plan (entry / stop / targets) with a
confidence score. Weights are adjustable by the learning module, so the
analyst improves as outcomes accumulate.
"""
from __future__ import annotations

import math
from typing import Optional

from ..core import Assessment, EventBus, Side, Signal, SignalType, Topic
from ..core.models import clamp, sigmoid
from ..liquidity.engine import LiquidityEngine
from ..structure.engine import MarketStructureEngine

# Baseline directional weight of each signal type in the control score.
# Positive → supports its Signal.side; magnitude = how "institutional" it is.
DEFAULT_WEIGHTS: dict[SignalType, float] = {
    SignalType.LIQUIDITY_WALL: 0.5,
    SignalType.SPOOFING: 0.7,
    SignalType.ICEBERG: 0.9,
    SignalType.ABSORPTION: 1.0,
    SignalType.SWEEP: 0.45,
    SignalType.IMBALANCE: 0.4,
    SignalType.DELTA_IMBALANCE: 0.7,
    SignalType.AGGRESSIVE_FLOW: 0.55,
    SignalType.LARGE_PARTICIPANT: 0.6,
    SignalType.LIQUIDITY_MIGRATION: 0.5,
    SignalType.LIQUIDITY_EXHAUSTION: 0.2,
    SignalType.PULLED_LIQUIDITY: 0.5,
    SignalType.ADDED_LIQUIDITY: 0.35,
    SignalType.HIDDEN_LIQUIDITY: 0.5,
    SignalType.BOS: 0.6,
    SignalType.CHOCH: 0.75,
    SignalType.FVG: 0.25,
    SignalType.ORDER_BLOCK: 0.35,
    SignalType.BREAKER_BLOCK: 0.35,
    SignalType.MITIGATION: 0.3,
    SignalType.EQUAL_HIGHS: 0.15,
    SignalType.EQUAL_LOWS: 0.15,
    SignalType.STOP_HUNT: 0.95,
    SignalType.FAKE_BREAKOUT: 0.9,
    SignalType.TRAPPED_BUYERS: 0.6,
    SignalType.TRAPPED_SELLERS: 0.6,
    SignalType.ACCUMULATION: 1.0,
    SignalType.DISTRIBUTION: 1.0,
}

INSTITUTIONAL_TYPES = {
    SignalType.ICEBERG, SignalType.ABSORPTION, SignalType.ACCUMULATION,
    SignalType.DISTRIBUTION, SignalType.LARGE_PARTICIPANT, SignalType.HIDDEN_LIQUIDITY,
    SignalType.STOP_HUNT,
}


class SignalFusionEngine:
    def __init__(self, bus: EventBus, symbol: str, liquidity: LiquidityEngine,
                 structure: MarketStructureEngine, tick_size: float,
                 halflife_s: float = 30.0, weights: dict[SignalType, float] | None = None) -> None:
        self.bus = bus
        self.symbol = symbol
        self.liquidity = liquidity
        self.structure = structure
        self.tick = tick_size
        self.halflife = halflife_s
        self.weights = dict(DEFAULT_WEIGHTS)
        if weights:
            self.weights.update(weights)
        self.active_signals: list[Signal] = []
        self.last_assessment: Optional[Assessment] = None
        bus.subscribe(Topic.SIGNAL, self._on_signal)

    def _on_signal(self, sig: Signal) -> None:
        self.active_signals.append(sig)
        if len(self.active_signals) > 500:
            del self.active_signals[:100]

    def _decay(self, age: float) -> float:
        return math.pow(0.5, age / self.halflife)

    # ------------------------------------------------------------------
    def assess(self, ts: float, price: float | None) -> Assessment:
        price = price or self.liquidity.book.mid or 0.0
        self.active_signals = [s for s in self.active_signals if ts - s.ts < 6 * self.halflife]

        control_score = 0.0
        inst_score = 0.0
        evidence = 0.0
        reasons: list[tuple[float, str]] = []
        for s in self.active_signals:
            w = self.weights.get(s.type, 0.3)
            decay = self._decay(ts - s.ts)
            direction = 0.0
            if s.side is Side.BUY:
                direction = 1.0
            elif s.side is Side.SELL:
                direction = -1.0
            contrib = w * s.confidence * decay * direction
            control_score += contrib
            evidence += w * s.confidence * decay
            if s.type in INSTITUTIONAL_TYPES:
                inst_score += contrib
            if abs(contrib) > 0.15:
                reasons.append((abs(contrib), s.explanation))

        feats = self.liquidity.features(ts)
        sfeats = self.structure.features(price)
        # book & flow context nudges control
        control_score += 0.8 * feats["book_imbalance"]
        cum_delta_norm = math.tanh(feats["cum_delta"] / 2000.0)
        control_score += 0.6 * cum_delta_norm
        trend_dir = sfeats["trend_up"] - sfeats["trend_down"]

        norm = math.tanh(control_score / 2.5)
        control = "buyers" if norm > 0.15 else "sellers" if norm < -0.15 else "balanced"

        inst_norm = math.tanh(inst_score / 1.5)
        institutional_bias = ("accumulating" if inst_norm > 0.2
                              else "distributing" if inst_norm < -0.2 else "neutral")

        # continuation = flow agrees with structure trend; reversal = flow fights it
        alignment = norm * trend_dir if trend_dir != 0 else abs(norm) * 0.3
        p_cont = sigmoid(2.2 * alignment + 0.8 * abs(norm) - 0.3)
        reversal_pressure = 0.0
        for s in self.active_signals:
            if s.type in (SignalType.STOP_HUNT, SignalType.FAKE_BREAKOUT, SignalType.CHOCH,
                          SignalType.TRAPPED_BUYERS, SignalType.TRAPPED_SELLERS):
                d = self._decay(ts - s.ts)
                against_trend = (s.side is Side.BUY and trend_dir < 0) or (s.side is Side.SELL and trend_dir > 0)
                reversal_pressure += s.confidence * d * (1.4 if against_trend else 0.7)
        p_rev = sigmoid(1.6 * reversal_pressure - 1.2)

        confidence = clamp(math.tanh(evidence / 2.0) * 0.6 + abs(norm) * 0.4)
        reasons.sort(reverse=True)
        top_reasons = [r for _, r in reasons[:5]]

        plan = self._trade_plan(ts, price, norm, confidence, p_cont, p_rev)
        assessment = Assessment(
            ts=ts, symbol=self.symbol, control=control, control_score=norm,
            institutional_bias=institutional_bias, p_continuation=p_cont, p_reversal=p_rev,
            confidence=confidence, trade_plan=plan,
            features={**feats, **sfeats, "evidence": evidence, "cum_delta_norm": cum_delta_norm},
            reasons=top_reasons,
        )
        self.last_assessment = assessment
        self.bus.publish(Topic.ASSESSMENT, assessment)
        return assessment

    # ------------------------------------------------------------------
    def _trade_plan(self, ts: float, price: float, control: float, confidence: float,
                    p_cont: float, p_rev: float) -> Optional[dict]:
        """Entry/stop/targets from structure + liquidity pools. Only produced
        when evidence is decent — the assistant should say 'no trade' freely."""
        if abs(control) < 0.25 or confidence < 0.35 or price <= 0:
            return None
        side = Side.BUY if control > 0 else Side.SELL
        st = self.structure.state
        pools = st.liquidity_pools()
        lo = st.recent_swing("low")
        hi = st.recent_swing("high")

        if side is Side.BUY:
            stop_ref = lo.price if lo else price - 20 * self.tick
            stop = stop_ref - 2 * self.tick
            targets = sorted([p["price"] for p in pools if p["side"] == "above" and p["price"] > price])
            if not targets and hi:
                targets = [hi.price]
        else:
            stop_ref = hi.price if hi else price + 20 * self.tick
            stop = stop_ref + 2 * self.tick
            targets = sorted([p["price"] for p in pools if p["side"] == "below" and p["price"] < price], reverse=True)
            if not targets and lo:
                targets = [lo.price]
        risk = abs(price - stop)
        if risk <= 0:
            return None
        if not targets:
            # every pool on the target side already swept → project a 2R
            # measured move instead of refusing to plan
            targets = [price + 2 * risk if side is Side.BUY else price - 2 * risk]
        reward = abs(targets[0] - price)
        rr = reward / risk
        if rr < 0.8:
            return None
        return {
            "direction": side.value,
            "entry": round(price, 6),
            "stop": round(stop, 6),
            "targets": [round(t, 6) for t in targets[:3]],
            "risk_reward": round(rr, 2),
            "confidence": round(confidence, 3),
            "p_continuation": round(p_cont, 3),
            "p_reversal": round(p_rev, 3),
            "rationale": f"{side.value} while {'buyers' if side is Side.BUY else 'sellers'} hold control "
                         f"({control:+.2f}); stop beyond {'swing low' if side is Side.BUY else 'swing high'} "
                         f"{stop_ref:g}, first target at the nearest liquidity pool {targets[0]:g}.",
        }

    # -- Q&A interface used by the API/assistant -----------------------------
    def answer(self, question: str, ts: float, price: float | None) -> dict:
        """Structured answers to the canonical questions the AI layer supports."""
        a = self.last_assessment or self.assess(ts, price)
        q = question.lower()
        if "control" in q:
            return {"answer": f"{a.control} (score {a.control_score:+.2f})", "confidence": a.confidence}
        if "institution" in q and ("buy" in q or "accum" in q):
            yes = a.institutional_bias == "accumulating"
            return {"answer": "yes" if yes else "no",
                    "detail": f"institutional bias: {a.institutional_bias}", "confidence": a.confidence}
        if "institution" in q and ("sell" in q or "distrib" in q):
            yes = a.institutional_bias == "distributing"
            return {"answer": "yes" if yes else "no",
                    "detail": f"institutional bias: {a.institutional_bias}", "confidence": a.confidence}
        if "breakout" in q:
            return {"answer": f"probability breakout fails: {a.p_reversal:.0%}", "confidence": a.confidence}
        if "continuation" in q:
            return {"answer": f"{a.p_continuation:.0%}", "confidence": a.confidence}
        if "reversal" in q:
            return {"answer": f"{a.p_reversal:.0%}", "confidence": a.confidence}
        if "entry" in q or "stop" in q or "target" in q:
            return {"answer": a.trade_plan or "no high-probability plan right now", "confidence": a.confidence}
        return {"answer": a.as_dict(), "confidence": a.confidence}
