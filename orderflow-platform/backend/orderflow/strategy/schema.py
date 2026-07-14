"""No-code strategy definition.

A strategy is pure data (JSON-safe dict) combining liquidity, delta,
volume, imbalance, VWAP, order flow signals, market structure, time
filters and risk management. The UI's strategy builder emits exactly this
schema; ``validate`` is the single source of truth for what is allowed.

Example::

    {
      "name": "Absorption reversal",
      "direction": "both",
      "entry": {"all": [
        {"metric": "book_imbalance", "op": ">", "value": 0.2},
        {"signal": "absorption", "min_confidence": 0.55, "within_s": 45}
      ]},
      "filters": {"time_windows": [["09:30", "16:00"]], "min_active_signals": 1},
      "risk": {"risk_per_trade_pct": 1.0, "stop_ticks": 12, "rr": 2.0},
      "exit": {"any": [{"metric": "cum_delta", "op": "<", "value": 0}]}
    }
"""
from __future__ import annotations

from typing import Any

from ..core import SignalType

VALID_OPS = {">", "<", ">=", "<=", "==", "abs>", "abs<"}

# Metrics come from FeatureSnapshot (see evaluator). Registered here so the
# builder UI can enumerate them with descriptions.
METRICS: dict[str, str] = {
    "price": "last traded price",
    "mid": "book midpoint",
    "spread_ticks": "bid/ask spread in ticks",
    "book_imbalance": "(bid depth − ask depth) / total, top 10 levels [-1..1]",
    "bid_depth": "resting size, top 10 bid levels",
    "ask_depth": "resting size, top 10 ask levels",
    "delta_30s": "buy − sell aggressive volume, last 30s",
    "cum_delta": "session cumulative delta",
    "volume_30s": "total traded volume, last 30s",
    "vwap_distance": "price − session VWAP",
    "range_position": "position in dealing range: 0 low … 1 high",
    "trend": "structure trend: 1 up, -1 down, 0 none",
    "control_score": "AI control score [-1..1]",
    "p_continuation": "AI continuation probability",
    "p_reversal": "AI reversal probability",
    "minutes_since_open": "minutes since session start",
}


class StrategyError(ValueError):
    pass


def _check_condition(cond: dict, where: str) -> None:
    if "metric" in cond:
        if cond["metric"] not in METRICS:
            raise StrategyError(f"{where}: unknown metric '{cond['metric']}'")
        if cond.get("op") not in VALID_OPS:
            raise StrategyError(f"{where}: invalid op '{cond.get('op')}'")
        if not isinstance(cond.get("value"), (int, float)):
            raise StrategyError(f"{where}: 'value' must be numeric")
    elif "signal" in cond:
        try:
            SignalType(cond["signal"])
        except ValueError:
            raise StrategyError(f"{where}: unknown signal '{cond['signal']}'") from None
        mc = cond.get("min_confidence", 0.5)
        if not (0.0 <= float(mc) <= 1.0):
            raise StrategyError(f"{where}: min_confidence out of range")
        if cond.get("side") not in (None, "buy", "sell", "same", "opposite"):
            raise StrategyError(f"{where}: invalid side '{cond.get('side')}'")
    else:
        raise StrategyError(f"{where}: condition needs 'metric' or 'signal'")


def _check_group(group: dict, where: str) -> None:
    if not isinstance(group, dict) or not (set(group) & {"all", "any"}):
        raise StrategyError(f"{where}: expected {{'all': [...]}} or {{'any': [...]}}")
    for key in ("all", "any"):
        for i, cond in enumerate(group.get(key, [])):
            if isinstance(cond, dict) and (set(cond) & {"all", "any"}):
                _check_group(cond, f"{where}.{key}[{i}]")
            else:
                _check_condition(cond, f"{where}.{key}[{i}]")


def validate(strategy: dict[str, Any]) -> dict[str, Any]:
    if not strategy.get("name"):
        raise StrategyError("strategy needs a name")
    if strategy.get("direction", "both") not in ("buy", "sell", "both"):
        raise StrategyError("direction must be buy / sell / both")
    if "entry" not in strategy:
        raise StrategyError("strategy needs an 'entry' block")
    _check_group(strategy["entry"], "entry")
    if "exit" in strategy:
        _check_group(strategy["exit"], "exit")
    risk = strategy.setdefault("risk", {})
    risk.setdefault("risk_per_trade_pct", 1.0)
    risk.setdefault("stop_ticks", 12)
    risk.setdefault("rr", 2.0)
    if risk["stop_ticks"] <= 0 or risk["rr"] <= 0:
        raise StrategyError("risk.stop_ticks and risk.rr must be positive")
    filters = strategy.setdefault("filters", {})
    for win in filters.get("time_windows", []):
        if len(win) != 2:
            raise StrategyError("time window must be [start, end]")
    filters.setdefault("cooldown_s", 60)
    return strategy
