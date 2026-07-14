from orderflow.risk.engine import RiskConfig, RiskEngine


def test_position_sizing():
    r = RiskEngine(RiskConfig(account_equity=100_000, risk_per_trade_pct=1.0, point_value=50.0))
    tr = r.position_size(entry=5000.0, stop=4990.0, rr_target=2.0)
    # $1000 risk / (10 pts * $50) = 2 contracts
    assert abs(tr.size - 2.0) < 1e-9
    assert tr.risk_amount == 1000.0
    assert tr.take_profit == 5020.0
    assert abs(tr.risk_reward - 2.0) < 1e-9


def test_position_size_capped():
    r = RiskEngine(RiskConfig(account_equity=1_000_000, risk_per_trade_pct=5.0, max_position=10.0))
    tr = r.position_size(entry=100.0, stop=99.9)
    assert tr.size == 10.0


def test_daily_loss_limit_halts():
    r = RiskEngine(RiskConfig(account_equity=10_000, daily_loss_limit_pct=3.0))
    ok, _ = r.allow_order(1.0, 0.0, 100.0, 99.0)
    assert ok
    r.on_realized_pnl(1.0, -400.0)  # over the $300 limit
    assert r.state.halted
    ok, reason = r.allow_order(1.0, 0.0, 100.0, 99.0)
    assert not ok and "daily loss" in reason


def test_daily_limit_resets_next_day():
    r = RiskEngine(RiskConfig(account_equity=10_000, daily_loss_limit_pct=3.0))
    r.on_realized_pnl(1.0, -400.0, day="2026-01-01")
    assert r.state.halted
    r.on_realized_pnl(2.0, 10.0, day="2026-01-02")
    assert not r.state.halted
    assert r.state.realized_today == 10.0


def test_position_limit():
    r = RiskEngine(RiskConfig(max_position=5.0))
    ok, reason = r.allow_order(6.0, 0.0, 100.0, None)
    assert not ok and "position limit" in reason
    ok, _ = r.allow_order(3.0, 2.0, 100.0, None)
    assert ok


def test_drawdown_tracking():
    r = RiskEngine(RiskConfig(account_equity=10_000, daily_loss_limit_pct=50.0))
    r.on_realized_pnl(1.0, 500.0)
    r.on_realized_pnl(2.0, -800.0)
    r.on_realized_pnl(3.0, 200.0)
    assert r.state.max_drawdown == 800.0
    assert r.state.equity == 9_900.0
