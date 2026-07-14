"""Detector tests: drive hand-crafted event sequences (plus the simulator's
labelled injections) and assert the right signals fire."""
from orderflow.core import BookDelta, BookSnapshot, Side, SignalType, Topic, TradeEvent

from conftest import feed_sim, make_sim


def collect_signals(pipeline):
    seen = []
    pipeline.bus.subscribe(Topic.SIGNAL, seen.append)
    return seen


def base_book(ts=1.0, mid=100.0, size=10.0, levels=30):
    bids = [(mid - 0.25 * i, size) for i in range(1, levels + 1)]
    asks = [(mid + 0.25 * i, size) for i in range(1, levels + 1)]
    return BookSnapshot(ts, "TEST", bids, asks)


def test_wall_detection(pipeline):
    seen = collect_signals(pipeline)
    pipeline.bus.publish(Topic.BOOK_SNAPSHOT, base_book())
    ts = 2.0
    # build up baseline level stats
    for i in range(120):
        ts += 0.1
        pipeline.bus.publish(Topic.BOOK_DELTA, BookDelta(ts, "TEST", Side.BUY, 99.0 - (i % 5) * 0.25, 10.0 + (i % 3)))
    pipeline.bus.publish(Topic.BOOK_DELTA, BookDelta(ts + 1, "TEST", Side.BUY, 99.5, 500.0))
    walls = [s for s in seen if s.type is SignalType.LIQUIDITY_WALL]
    assert walls, "expected a liquidity wall signal"
    assert walls[0].side is Side.BUY
    assert walls[0].price == 99.5
    assert "×" in walls[0].explanation or "x" in walls[0].explanation.lower()


def test_spoof_detection(pipeline):
    seen = collect_signals(pipeline)
    pipeline.bus.publish(Topic.BOOK_SNAPSHOT, base_book())
    ts = 2.0
    for i in range(120):
        ts += 0.1
        pipeline.bus.publish(Topic.BOOK_DELTA, BookDelta(ts, "TEST", Side.SELL, 101.0 + (i % 5) * 0.25, 10.0))
    # large ask appears 4 ticks off the touch...
    pipeline.bus.publish(Topic.BOOK_DELTA, BookDelta(ts + 0.5, "TEST", Side.SELL, 101.25, 800.0))
    # ...and is pulled 3 seconds later with no fills
    pipeline.bus.publish(Topic.BOOK_DELTA, BookDelta(ts + 3.5, "TEST", Side.SELL, 101.25, 10.0))
    spoofs = [s for s in seen if s.type is SignalType.SPOOFING]
    assert spoofs, "expected spoofing signal"
    assert spoofs[0].side is Side.BUY  # fake sell wall → real intent is up


def test_iceberg_detection(pipeline):
    seen = collect_signals(pipeline)
    pipeline.bus.publish(Topic.BOOK_SNAPSHOT, base_book())
    ts = 2.0
    # 20 visible on the bid at 99.75, but 300 executes there
    pipeline.bus.publish(Topic.BOOK_DELTA, BookDelta(ts, "TEST", Side.BUY, 99.75, 20.0))
    for i in range(15):
        ts += 0.3
        pipeline.bus.publish(Topic.TRADE, TradeEvent(ts, "TEST", 99.75, 20.0, Side.SELL, i))
        pipeline.bus.publish(Topic.BOOK_DELTA, BookDelta(ts, "TEST", Side.BUY, 99.75, 20.0))  # refills
    icebergs = [s for s in seen if s.type is SignalType.ICEBERG]
    assert icebergs, "expected iceberg signal"
    assert icebergs[0].side is Side.BUY
    assert icebergs[0].data["executed"] >= 3 * icebergs[0].data["max_visible"]


def test_sweep_detection(pipeline):
    seen = collect_signals(pipeline)
    pipeline.bus.publish(Topic.BOOK_SNAPSHOT, base_book())
    ts = 5.0
    for i in range(4):  # 4 levels consumed in <1.5s
        pipeline.bus.publish(Topic.TRADE, TradeEvent(ts + i * 0.2, "TEST", 100.25 + i * 0.25, 30.0, Side.BUY, i))
    sweeps = [s for s in seen if s.type is SignalType.SWEEP]
    assert sweeps and sweeps[0].side is Side.BUY


def test_imbalance_detection(pipeline):
    seen = collect_signals(pipeline)
    bids = [(100.0 - 0.25 * i, 50.0) for i in range(1, 15)]
    asks = [(100.0 + 0.25 * i, 5.0) for i in range(1, 15)]
    pipeline.bus.publish(Topic.BOOK_SNAPSHOT, BookSnapshot(1.0, "TEST", bids, asks))
    pipeline.liquidity.tick(10.0)
    imbalances = [s for s in seen if s.type is SignalType.IMBALANCE]
    assert imbalances and imbalances[0].side is Side.BUY
    assert imbalances[0].data["ratio"] > 0.65


def test_simulator_injections_are_detected():
    """End-to-end: run the simulator with injected behaviours; the engines
    should detect at least walls/sweeps/icebergs among the injected set."""
    from orderflow.core import Config
    from orderflow.pipeline import Pipeline

    cfg = Config(symbol="TEST", tick_size=0.25, candle_seconds=5.0, data_dir="/tmp/orderflow-test-data")
    p = Pipeline(cfg)
    seen = collect_signals(p)
    sim = make_sim(seed=11)
    feed_sim(p, sim, seconds=120)
    injected_kinds = {e.kind for e in sim.injected_log}
    detected_types = {s.type for s in seen}
    assert injected_kinds, "simulator should inject behaviours in 120s"
    liquidity_types = {
        SignalType.LIQUIDITY_WALL, SignalType.ICEBERG, SignalType.ABSORPTION,
        SignalType.SWEEP, SignalType.SPOOFING,
    }
    assert detected_types & liquidity_types, (
        f"expected some of {liquidity_types} given injections {injected_kinds}, got {detected_types}"
    )
    # every emitted signal must carry an explanation and confidence
    for s in seen:
        assert s.explanation and 0.0 <= s.confidence <= 1.0


def test_session_profile_and_vwap(pipeline):
    pipeline.bus.publish(Topic.BOOK_SNAPSHOT, base_book())
    ts = 1.0
    for i, (price, size, side) in enumerate([(100.0, 10, Side.BUY), (100.25, 20, Side.BUY), (100.0, 5, Side.SELL)]):
        pipeline.bus.publish(Topic.TRADE, TradeEvent(ts + i, "TEST", price, size, side, i))
    session = pipeline.liquidity.session
    assert session.profile.total == 35
    assert session.profile.poc == 100.25  # heaviest bin
    va = session.profile.value_area()
    assert va is not None and va[0] <= 100.25 <= va[1]
    expected_vwap = (100.0 * 10 + 100.25 * 20 + 100.0 * 5) / 35
    assert abs(session.vwap.value - expected_vwap) < 1e-9
    assert session.cum_delta == 25  # +10 +20 -5
