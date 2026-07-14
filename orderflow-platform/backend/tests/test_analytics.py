from orderflow.analytics.performance import ClosedTrade, analyze, compare


def t(pnl, setup="s1", trader="manual", dur=60.0):
    return ClosedTrade(ts_open=0.0, ts_close=dur, direction="buy",
                       entry=100.0, exit=100.0 + pnl, size=1.0, pnl=pnl,
                       setup=setup, trader=trader)


def test_empty():
    r = analyze([])
    assert r.n_trades == 0


def test_metrics():
    trades = [t(10), t(-5), t(20, setup="s2"), t(-5), t(10)]
    r = analyze(trades)
    assert r.n_trades == 5
    assert abs(r.win_rate - 0.6) < 1e-9
    assert abs(r.profit_factor - 4.0) < 1e-9  # 40 win / 10 loss
    assert r.total_pnl == 30
    assert r.avg_trade == 6.0
    assert r.avg_win == 40 / 3
    assert r.avg_loss == -5.0
    assert r.sharpe != 0.0
    assert r.sortino != 0.0
    assert r.best_setup in ("s1", "s2")
    assert r.by_setup["s2"]["n"] == 1


def test_drawdown():
    r = analyze([t(10), t(-15), t(-5), t(30)])
    assert r.max_drawdown == 20.0


def test_compare_groups():
    out = compare({
        "ai": [t(10, trader="ai"), t(5, trader="ai")],
        "trader": [t(-5), t(15)],
    })
    assert out["ai"]["n_trades"] == 2
    assert out["trader"]["total_pnl"] == 10
