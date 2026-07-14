"""Performance analytics over completed trades and AI predictions."""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class ClosedTrade:
    ts_open: float
    ts_close: float
    direction: str  # "buy" | "sell"
    entry: float
    exit: float
    size: float
    pnl: float
    setup: str = ""  # tag: which strategy/signal produced it
    trader: str = "manual"  # "manual" | "ai" | strategy name

    @property
    def duration(self) -> float:
        return self.ts_close - self.ts_open


@dataclass
class PerformanceReport:
    n_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown: float = 0.0
    total_pnl: float = 0.0
    avg_trade: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_duration_s: float = 0.0
    best_setup: str = ""
    worst_setup: str = ""
    by_setup: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in vars(self).items()}


def analyze(trades: list[ClosedTrade]) -> PerformanceReport:
    r = PerformanceReport()
    if not trades:
        return r
    trades = sorted(trades, key=lambda t: t.ts_close)
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    r.n_trades = len(trades)
    r.win_rate = len(wins) / len(trades)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    r.profit_factor = gross_win / gross_loss if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0)
    if r.profit_factor == math.inf:
        r.profit_factor = 999.0
    r.total_pnl = sum(pnls)
    r.avg_trade = r.total_pnl / len(trades)
    r.avg_win = gross_win / len(wins) if wins else 0.0
    r.avg_loss = -gross_loss / len(losses) if losses else 0.0
    r.avg_duration_s = sum(t.duration for t in trades) / len(trades)

    mean = r.avg_trade
    if len(pnls) > 1:
        var = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
        std = math.sqrt(var)
        r.sharpe = mean / std * math.sqrt(252) if std > 0 else 0.0
        downside = [min(0.0, p - mean) for p in pnls]
        dvar = sum(d**2 for d in downside) / (len(pnls) - 1)
        dstd = math.sqrt(dvar)
        r.sortino = mean / dstd * math.sqrt(252) if dstd > 0 else 0.0

    equity = 0.0
    peak = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        r.max_drawdown = max(r.max_drawdown, peak - equity)

    by_setup: dict[str, list[float]] = {}
    for t in trades:
        by_setup.setdefault(t.setup or "untagged", []).append(t.pnl)
    r.by_setup = {
        k: {"n": len(v), "pnl": round(sum(v), 2), "win_rate": round(sum(1 for x in v if x > 0) / len(v), 3)}
        for k, v in by_setup.items()
    }
    if r.by_setup:
        ranked = sorted(r.by_setup.items(), key=lambda kv: kv[1]["pnl"])
        r.worst_setup = ranked[0][0]
        r.best_setup = ranked[-1][0]
    return r


def compare(groups: dict[str, list[ClosedTrade]]) -> dict:
    """Side-by-side reports — e.g. {'ai': [...], 'trader': [...]} or one key
    per strategy for strategy comparison."""
    return {name: analyze(ts).as_dict() for name, ts in groups.items()}
