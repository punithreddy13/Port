"""Machine learning layer.

Two complementary parts, both designed for continuous learning from
recorded history — no external ML framework needed for the core loop:

* PatternStats — per signal-type outcome tracking: after each signal, did
  price move with the signal's side over a horizon? Produces hit rates,
  average forward returns and risk/reward per pattern and per market
  regime, and turns them into fusion weight updates (evidence-weighted,
  shrunk toward the prior with low sample counts).

* OnlineLogit — an online logistic regression over assessment features →
  outcome (did the market continue?), trained by SGD as labels arrive.
  Gives calibrated continuation probabilities that improve with data.

State serialises to JSON so learning accumulates across sessions.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from ..core import Side, Signal, SignalType
from ..core.models import clamp, sigmoid


@dataclass
class PatternOutcome:
    n: int = 0
    hits: int = 0
    sum_fwd_return: float = 0.0
    sum_favorable: float = 0.0  # max favourable excursion (reward proxy)
    sum_adverse: float = 0.0  # max adverse excursion (risk proxy)

    @property
    def hit_rate(self) -> float:
        return self.hits / self.n if self.n else 0.0

    @property
    def avg_rr(self) -> float:
        if self.n == 0 or self.sum_adverse <= 0:
            return 0.0
        return self.sum_favorable / self.sum_adverse


class PatternStats:
    """Tracks which signal patterns actually work."""

    def __init__(self, horizon_s: float = 60.0) -> None:
        self.horizon = horizon_s
        self.stats: dict[str, PatternOutcome] = {}
        self._pending: list[dict] = []

    def observe_signal(self, sig: Signal, regime: str = "any") -> None:
        if sig.side is None:
            return
        self._pending.append({
            "key": f"{sig.type.value}|{regime}",
            "side": sig.side,
            "price": sig.price,
            "ts": sig.ts,
            "max_up": sig.price,
            "min_dn": sig.price,
        })

    def observe_price(self, ts: float, price: float) -> None:
        """Feed every trade/mid price; resolves pending signals at horizon."""
        for p in self._pending:
            p["max_up"] = max(p["max_up"], price)
            p["min_dn"] = min(p["min_dn"], price)
        due = [p for p in self._pending if ts - p["ts"] >= self.horizon]
        if not due:
            return
        self._pending = [p for p in self._pending if ts - p["ts"] < self.horizon]
        for p in due:
            o = self.stats.setdefault(p["key"], PatternOutcome())
            direction = 1.0 if p["side"] is Side.BUY else -1.0
            fwd = (price - p["price"]) * direction
            favorable = (p["max_up"] - p["price"]) if direction > 0 else (p["price"] - p["min_dn"])
            adverse = (p["price"] - p["min_dn"]) if direction > 0 else (p["max_up"] - p["price"])
            o.n += 1
            o.hits += 1 if fwd > 0 else 0
            o.sum_fwd_return += fwd
            o.sum_favorable += max(favorable, 0.0)
            o.sum_adverse += max(adverse, 1e-9)

    def weight_updates(self, base_weights: dict[SignalType, float],
                       min_samples: int = 20) -> dict[SignalType, float]:
        """Shrunk multiplicative update: hit-rate 50% → keep prior; 70% with
        many samples → boost; consistent failure → damp. This is how the
        platform 'learns which setups consistently work'."""
        out: dict[SignalType, float] = {}
        by_type: dict[str, PatternOutcome] = {}
        for key, o in self.stats.items():
            t = key.split("|")[0]
            agg = by_type.setdefault(t, PatternOutcome())
            agg.n += o.n
            agg.hits += o.hits
            agg.sum_fwd_return += o.sum_fwd_return
            agg.sum_favorable += o.sum_favorable
            agg.sum_adverse += o.sum_adverse
        for t, agg in by_type.items():
            try:
                st = SignalType(t)
            except ValueError:
                continue
            if agg.n < min_samples:
                continue
            shrink = agg.n / (agg.n + 50.0)  # confidence in the estimate
            edge = (agg.hit_rate - 0.5) * 2.0  # -1..1
            mult = 1.0 + shrink * edge
            out[st] = max(0.05, base_weights.get(st, 0.3) * mult)
        return out

    def report(self) -> list[dict]:
        rows = []
        for key, o in sorted(self.stats.items(), key=lambda kv: -kv[1].n):
            t, regime = key.split("|")
            rows.append({
                "pattern": t, "regime": regime, "n": o.n,
                "hit_rate": round(o.hit_rate, 3),
                "avg_fwd_return": round(o.sum_fwd_return / o.n, 4) if o.n else 0.0,
                "avg_rr": round(o.avg_rr, 2),
            })
        return rows

    # -- persistence --------------------------------------------------------
    def save(self, path: str | Path) -> None:
        data = {k: vars(v) for k, v in self.stats.items()}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps({"horizon": self.horizon, "stats": data}))

    @classmethod
    def load(cls, path: str | Path) -> "PatternStats":
        p = Path(path)
        ps = cls()
        if p.exists():
            raw = json.loads(p.read_text())
            ps.horizon = raw.get("horizon", 60.0)
            for k, v in raw.get("stats", {}).items():
                ps.stats[k] = PatternOutcome(**v)
        return ps


@dataclass
class OnlineLogit:
    """Online logistic regression (SGD + L2) for continuation prediction."""

    feature_names: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    bias: float = 0.0
    lr: float = 0.02
    l2: float = 1e-4
    n_updates: int = 0

    def predict(self, features: dict[str, float]) -> float:
        z = self.bias + sum(self.weights.get(k, 0.0) * self._norm(v) for k, v in features.items())
        return sigmoid(z)

    @staticmethod
    def _norm(v: float) -> float:
        return math.tanh(v) if abs(v) > 3 else v

    def update(self, features: dict[str, float], label: int) -> float:
        """label: 1 = continued, 0 = reversed. Returns pre-update prediction."""
        p = self.predict(features)
        err = label - p
        self.bias += self.lr * err
        for k, v in features.items():
            if k not in self.weights:
                self.weights[k] = 0.0
                self.feature_names.append(k)
            g = err * self._norm(v) - self.l2 * self.weights[k]
            self.weights[k] += self.lr * g
        self.n_updates += 1
        return p

    @property
    def brier_ready(self) -> bool:
        return self.n_updates >= 50

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps({
            "weights": self.weights, "bias": self.bias, "n_updates": self.n_updates,
        }))

    @classmethod
    def load(cls, path: str | Path) -> "OnlineLogit":
        p = Path(path)
        m = cls()
        if p.exists():
            raw = json.loads(p.read_text())
            m.weights = raw.get("weights", {})
            m.feature_names = list(m.weights)
            m.bias = raw.get("bias", 0.0)
            m.n_updates = raw.get("n_updates", 0)
        return m


class LearningCoordinator:
    """Wires PatternStats + OnlineLogit into the live pipeline: watches
    signals & assessments, resolves outcomes, and periodically produces
    updated fusion weights."""

    def __init__(self, data_dir: str | Path = "data", horizon_s: float = 60.0) -> None:
        self.dir = Path(data_dir)
        self.patterns = PatternStats.load(self.dir / "patterns.json")
        self.patterns.horizon = horizon_s
        self.model = OnlineLogit.load(self.dir / "logit.json")
        self._pending_assessments: list[dict] = []
        self.predictions: list[dict] = []  # for AI-accuracy analytics

    def on_signal(self, sig: Signal, regime: str) -> None:
        self.patterns.observe_signal(sig, regime)

    def on_assessment(self, ts: float, price: float, features: dict[str, float],
                      control_score: float, p_continuation: float) -> None:
        if abs(control_score) < 0.15:
            return  # no direction, nothing to score
        self._pending_assessments.append({
            "ts": ts, "price": price, "features": dict(features),
            "dir": 1.0 if control_score > 0 else -1.0, "p": p_continuation,
        })

    def on_price(self, ts: float, price: float) -> None:
        self.patterns.observe_price(ts, price)
        due = [a for a in self._pending_assessments if ts - a["ts"] >= self.patterns.horizon]
        if not due:
            return
        self._pending_assessments = [a for a in self._pending_assessments
                                     if ts - a["ts"] < self.patterns.horizon]
        for a in due:
            moved = (price - a["price"]) * a["dir"]
            label = 1 if moved > 0 else 0
            pred = self.model.update(a["features"], label)
            self.predictions.append({"ts": a["ts"], "p": a["p"], "model_p": pred, "label": label})
            if len(self.predictions) > 2000:
                del self.predictions[:500]

    def fusion_weights(self, base: dict[SignalType, float]) -> dict[SignalType, float]:
        return self.patterns.weight_updates(base)

    def ai_accuracy(self) -> dict:
        if not self.predictions:
            return {"n": 0, "accuracy": None, "brier": None}
        n = len(self.predictions)
        correct = sum(1 for p in self.predictions if (p["p"] >= 0.5) == (p["label"] == 1))
        brier = sum((p["p"] - p["label"]) ** 2 for p in self.predictions) / n
        return {"n": n, "accuracy": round(correct / n, 3), "brier": round(brier, 4)}

    def save(self) -> None:
        self.patterns.save(self.dir / "patterns.json")
        self.model.save(self.dir / "logit.json")
