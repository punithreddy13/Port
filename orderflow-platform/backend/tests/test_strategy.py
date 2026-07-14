import pytest

from orderflow.core import Config, Topic
from orderflow.strategy.backtester import StrategyBacktester
from orderflow.strategy.schema import StrategyError, validate

from conftest import make_sim


GOOD = {
    "name": "Delta momentum",
    "direction": "both",
    "entry": {"all": [
        {"metric": "delta_30s", "op": "abs>", "value": 50},
        {"metric": "book_imbalance", "op": "abs>", "value": 0.05},
    ]},
    "risk": {"risk_per_trade_pct": 1.0, "stop_ticks": 10, "rr": 1.5},
    "filters": {"cooldown_s": 20},
}


def test_validate_ok():
    s = validate(dict(GOOD))
    assert s["risk"]["rr"] == 1.5
    assert s["filters"]["cooldown_s"] == 20


@pytest.mark.parametrize("mutate,msg", [
    (lambda s: s.pop("name"), "name"),
    (lambda s: s.pop("entry"), "entry"),
    (lambda s: s["entry"]["all"][0].update({"metric": "nope"}), "unknown metric"),
    (lambda s: s["entry"]["all"][0].update({"op": "!!"}), "invalid op"),
    (lambda s: s.update({"direction": "sideways"}), "direction"),
    (lambda s: s["risk"].update({"stop_ticks": -1}), "positive"),
])
def test_validate_rejects(mutate, msg):
    import copy

    s = copy.deepcopy(GOOD)
    mutate(s)
    with pytest.raises(StrategyError, match=msg):
        validate(s)


def test_signal_condition_validation():
    s = dict(GOOD, entry={"all": [{"signal": "absorption", "min_confidence": 0.6}]})
    validate(s)
    bad = dict(GOOD, entry={"all": [{"signal": "not_a_thing"}]})
    with pytest.raises(StrategyError, match="unknown signal"):
        validate(bad)


def test_backtester_runs_and_reports(tmp_path):
    sim = make_sim(seed=23)
    events = [(Topic.BOOK_SNAPSHOT, sim.snapshot())] + sim.run_for(180)
    cfg = Config(symbol="TEST", tick_size=0.25, candle_seconds=5.0, data_dir=str(tmp_path))
    result = StrategyBacktester(GOOD, cfg).run(events)
    assert result["strategy"] == "Delta momentum"
    assert result["evaluations"] > 50
    report = result["report"]
    assert set(report) >= {"n_trades", "win_rate", "profit_factor", "sharpe",
                           "sortino", "max_drawdown", "total_pnl"}
    # trades should have fired on a 3-minute active tape with loose thresholds
    assert report["n_trades"] >= 1, "expected the strategy to trade at least once"
    for t in result["trades"]:
        assert t["setup"] == "Delta momentum"


def test_signal_based_strategy_backtest(tmp_path):
    strategy = {
        "name": "Absorption fade",
        "direction": "both",
        "entry": {"all": [{"signal": "absorption", "min_confidence": 0.4, "within_s": 60, "side": "same"}]},
        "risk": {"risk_per_trade_pct": 0.5, "stop_ticks": 8, "rr": 2.0},
        "filters": {"cooldown_s": 30},
    }
    sim = make_sim(seed=29)
    events = [(Topic.BOOK_SNAPSHOT, sim.snapshot())] + sim.run_for(180)
    cfg = Config(symbol="TEST", tick_size=0.25, candle_seconds=5.0, data_dir=str(tmp_path))
    result = StrategyBacktester(strategy, cfg).run(events)
    assert result["report"]["n_trades"] >= 0  # runs cleanly end-to-end
