from orderflow.core import BookDelta, BookSnapshot, Side, Topic
from orderflow.core.models import OrderBook, RollingStats


def test_orderbook_basics():
    ob = OrderBook("X", tick_size=0.25)
    ob.apply_snapshot([(100.0, 5.0), (99.75, 3.0)], [(100.25, 4.0), (100.5, 6.0)], ts=1.0)
    assert ob.best_bid == 100.0
    assert ob.best_ask == 100.25
    assert ob.mid == 100.125
    assert ob.spread == 0.25
    assert ob.depth(Side.BUY, 10) == 8.0

    ob.apply_delta(Side.BUY, 100.0, 0.0, ts=2.0)
    assert ob.best_bid == 99.75
    prev = ob.apply_delta(Side.SELL, 100.25, 9.0, ts=3.0)
    assert prev == 4.0
    assert ob.asks[100.25] == 9.0


def test_orderbook_tick_rounding():
    ob = OrderBook("X", tick_size=0.25)
    ob.apply_delta(Side.BUY, 99.9999999, 2.0, ts=1.0)
    assert 100.0 in ob.bids


def test_book_engine_via_bus(pipeline):
    bus = pipeline.bus
    bus.publish(Topic.BOOK_SNAPSHOT, BookSnapshot(1.0, "TEST", [(100.0, 5.0)], [(100.5, 5.0)]))
    bus.publish(Topic.BOOK_DELTA, BookDelta(2.0, "TEST", Side.BUY, 100.25, 7.0))
    book = pipeline.book_engine.book
    assert book.best_bid == 100.25
    assert book.best_ask == 100.5


def test_book_history_capture(pipeline):
    bus = pipeline.bus
    bus.publish(Topic.BOOK_SNAPSHOT, BookSnapshot(1.0, "TEST", [(100.0, 5.0)], [(100.5, 5.0)]))
    pipeline.capture_frame(2.0)
    pipeline.capture_frame(3.0)
    frames = pipeline.book_engine.history.frames
    assert len(frames) == 2
    assert frames[0].levels[100.0] == 5.0  # bids positive
    assert frames[0].levels[100.5] == -5.0  # asks negative


def test_rolling_stats():
    rs = RollingStats(window=3)
    for x in (1.0, 2.0, 3.0, 4.0):
        rs.add(x)
    assert len(rs) == 3
    assert abs(rs.mean - 3.0) < 1e-9
    assert rs.zscore(rs.mean) == 0.0
