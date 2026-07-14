"""Risk engine: position sizing, trade risk maths, and account-level limits
(daily loss limit, exposure caps, drawdown tracking). The broker consults
``allow_order`` before accepting anything — risk is enforced, not advisory."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RiskConfig:
    account_equity: float = 100_000.0
    risk_per_trade_pct: float = 1.0
    daily_loss_limit_pct: float = 3.0
    max_position: float = 25.0
    max_portfolio_risk_pct: float = 5.0
    point_value: float = 1.0  # $ per point per unit (e.g. 50 for ES)


@dataclass
class TradeRisk:
    size: float
    risk_amount: float
    stop: float
    take_profit: float | None
    risk_reward: float | None


@dataclass
class RiskState:
    realized_today: float = 0.0
    peak_equity: float = 0.0
    equity: float = 0.0
    max_drawdown: float = 0.0
    open_risk: float = 0.0
    day_stamp: str = ""
    halted: bool = False
    halt_reason: str = ""
    equity_curve: list[tuple[float, float]] = field(default_factory=list)


class RiskEngine:
    def __init__(self, config: RiskConfig | None = None) -> None:
        self.cfg = config or RiskConfig()
        self.state = RiskState(equity=self.cfg.account_equity, peak_equity=self.cfg.account_equity)

    # -- trade-level maths ---------------------------------------------------
    def position_size(self, entry: float, stop: float, rr_target: float | None = 2.0) -> TradeRisk:
        """Size so that a stop-out loses exactly risk_per_trade_pct of equity."""
        risk_amount = self.state.equity * self.cfg.risk_per_trade_pct / 100.0
        per_unit = abs(entry - stop) * self.cfg.point_value
        size = 0.0 if per_unit <= 0 else risk_amount / per_unit
        size = min(size, self.cfg.max_position)
        direction = 1.0 if entry > stop else -1.0
        tp = entry + direction * abs(entry - stop) * rr_target if rr_target else None
        rr = None
        if tp is not None and abs(entry - stop) > 0:
            rr = abs(tp - entry) / abs(entry - stop)
        return TradeRisk(size=round(size, 2), risk_amount=round(risk_amount, 2),
                         stop=stop, take_profit=tp, risk_reward=rr)

    # -- account-level gates ---------------------------------------------------
    def allow_order(self, size: float, current_position: float, entry: float, stop: float | None) -> tuple[bool, str]:
        if self.state.halted:
            return False, self.state.halt_reason
        if abs(current_position + size) > self.cfg.max_position:
            return False, f"position limit {self.cfg.max_position} would be exceeded"
        limit = self.cfg.account_equity * self.cfg.daily_loss_limit_pct / 100.0
        if self.state.realized_today <= -limit:
            self._halt(f"daily loss limit ({self.cfg.daily_loss_limit_pct}%) reached")
            return False, self.state.halt_reason
        if stop is not None:
            new_risk = abs(entry - stop) * self.cfg.point_value * abs(size)
            max_risk = self.cfg.account_equity * self.cfg.max_portfolio_risk_pct / 100.0
            if self.state.open_risk + new_risk > max_risk:
                return False, f"portfolio risk cap {self.cfg.max_portfolio_risk_pct}% would be exceeded"
        return True, "ok"

    def _halt(self, reason: str) -> None:
        self.state.halted = True
        self.state.halt_reason = reason

    def resume(self) -> None:
        self.state.halted = False
        self.state.halt_reason = ""

    # -- accounting -------------------------------------------------------------
    def on_realized_pnl(self, ts: float, pnl: float, day: str = "") -> None:
        if day and day != self.state.day_stamp:
            self.state.day_stamp = day
            self.state.realized_today = 0.0
            if self.state.halted and "daily" in self.state.halt_reason:
                self.resume()
        self.state.realized_today += pnl
        self.state.equity += pnl
        self.state.peak_equity = max(self.state.peak_equity, self.state.equity)
        dd = self.state.peak_equity - self.state.equity
        self.state.max_drawdown = max(self.state.max_drawdown, dd)
        self.state.equity_curve.append((ts, self.state.equity))
        limit = self.cfg.account_equity * self.cfg.daily_loss_limit_pct / 100.0
        if self.state.realized_today <= -limit and not self.state.halted:
            self._halt(f"daily loss limit ({self.cfg.daily_loss_limit_pct}%) reached")

    def summary(self) -> dict:
        s = self.state
        return {
            "equity": round(s.equity, 2),
            "realized_today": round(s.realized_today, 2),
            "max_drawdown": round(s.max_drawdown, 2),
            "open_risk": round(s.open_risk, 2),
            "halted": s.halted,
            "halt_reason": s.halt_reason,
        }
