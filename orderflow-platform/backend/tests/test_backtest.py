from orderflow.backtest.broker import PaperBroker
from orderflow.backtest.replay import ReplayEngine
from orderflow.core import Config, Side, Topic, TradeEvent
from orderflow.feeds.recorder import DataRecorder, load_events
from orderflow.core import EventBus

from conftest import make_sim


def trade_ev(ts, price, size=10.0, side=Side.BUY, tid=0):
    return TradeEvent(ts, "TEST", price, size, side, tid)


def make_broker():
    from orderflow.risk.engine import RiskConfig, RiskEngine

    return PaperBroker(RiskEngine(RiskConfig(account_equity=100_000, daily_loss_limit_pct=90.0,
                                             max_position=100.0)))


def test_broker_market_roundtrip():
    b = make_broker()
    b.on_trade(trade_ev(1.0, 100.0))
    b.submit(Side.BUY, 2.0)
    assert b.position.size == 2.0
    assert b.position.avg_price == 100.0
    b.on_trade(trade_ev(2.0, 101.0))
    assert abs(b.unrealized_pnl() - 2.0) < 1e-9
    b.submit(Side.SELL, 2.0)
    assert b.position.size == 0.0
    assert abs(b.state.realized - 2.0) < 1e-9
    assert len(b.closed_trades) == 1
    assert b.closed_trades[0].pnl == 2.0


def test_broker_stop_loss():
    b = make_broker()
    b.on_trade(trade_ev(1.0, 100.0))
    b.submit(Side.BUY, 1.0, stop=99.0, take_profit=103.0)
    b.on_trade(trade_ev(2.0, 98.5))  # through the stop
    assert b.position.size == 0.0
    assert b.closed_trades[0].exit == 99.0
    assert b.closed_trades[0].pnl == -1.0


def test_broker_take_profit():
    b = make_broker()
    b.on_trade(trade_ev(1.0, 100.0))
    b.submit(Side.SELL, 1.0, stop=102.0, take_profit=97.0)
    b.on_trade(trade_ev(2.0, 96.5))
    assert b.position.size == 0.0
    assert b.closed_trades[0].pnl == 3.0


def test_broker_limit_fill():
    b = make_broker()
    b.on_trade(trade_ev(1.0, 100.0))
    b.submit(Side.BUY, 1.0, type="limit", limit_price=99.0)
    assert b.position.size == 0.0
    b.on_trade(trade_ev(2.0, 98.9))
    assert b.position.size == 1.0
    assert b.position.avg_price == 99.0


def test_recorder_replay_roundtrip(tmp_path):
    bus = EventBus()
    rec = DataRecorder(bus, tmp_path / "session.jsonl")
    sim = make_sim(seed=9)
    bus.publish(Topic.BOOK_SNAPSHOT, sim.snapshot())
    for _ in range(200):
        for topic, ev in sim.step(0.1):
            bus.publish(topic, ev)
    rec.close()
    events = load_events(tmp_path / "session.jsonl")
    assert events, "recording should not be empty"
    assert events[0][0] is Topic.BOOK_SNAPSHOT
    # deterministic replay: same events → same downstream state twice
    r1 = ReplayEngine(events=events, config=Config(symbol="TEST", tick_size=0.25,
                                                   data_dir=str(tmp_path / "d1")))
    r1.run_to_end()
    r2 = ReplayEngine(events=events, config=Config(symbol="TEST", tick_size=0.25,
                                                   data_dir=str(tmp_path / "d2")))
    r2.run_to_end()
    assert r1.pipeline.liquidity.session.cum_delta == r2.pipeline.liquidity.session.cum_delta
    assert r1.pipeline.book_engine.book.mid == r2.pipeline.book_engine.book.mid


def test_replay_transport_controls(tmp_path):
    sim = make_sim(seed=13)
    events = [(Topic.BOOK_SNAPSHOT, sim.snapshot())] + sim.run_for(30)
    r = ReplayEngine(events=events, config=Config(symbol="TEST", tick_size=0.25,
                                                  data_dir=str(tmp_path)))
    assert r.status.total == len(events)
    r.step(10)
    assert r.status.cursor == 10
    mid_ts = r.status.start_ts + 15
    r.jump_to(mid_ts)
    assert r.status.ts >= mid_ts - 1
    # backward jump rebuilds deterministically
    r.jump_to(r.status.start_ts + 5)
    assert r.status.ts <= mid_ts
    r.set_speed(10)
    assert r.status.speed == 10
    r.step_seconds(100)  # run past the end
    assert r.status.finished


def test_replay_trading_and_comparison(tmp_path):
    sim = make_sim(seed=17)
    events = [(Topic.BOOK_SNAPSHOT, sim.snapshot())] + sim.run_for(60)
    r = ReplayEngine(events=events, config=Config(symbol="TEST", tick_size=0.25,
                                                  data_dir=str(tmp_path)))
    r.step_seconds(10)
    res = r.user_trade("buy", 1.0)
    assert res["status"] in ("filled", "rejected")
    r.step_seconds(20)
    r.pipeline.broker.flatten(r.status.ts)
    r.run_to_end()
    perf = r.performance()
    assert "comparison" in perf and "broker" in perf
    assert r.ai_log.records, "user trades must be logged with the AI's view"
    assert r.ai_log.records[0]["kind"] == "user_trade"
