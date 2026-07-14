from orderflow.ai.learning import OnlineLogit, PatternStats
from orderflow.core import Side, Signal, SignalType, Topic, TradeEvent

from conftest import feed_sim, make_sim


def test_assessment_produced(pipeline):
    sim = make_sim(seed=3)
    feed_sim(pipeline, sim, seconds=60)
    a = pipeline.fusion.assess(sim.ts, None)
    assert a.control in ("buyers", "sellers", "balanced")
    assert 0.0 <= a.p_continuation <= 1.0
    assert 0.0 <= a.p_reversal <= 1.0
    assert 0.0 <= a.confidence <= 1.0
    assert -1.0 <= a.control_score <= 1.0
    assert isinstance(a.reasons, list)


def test_control_follows_signals(pipeline):
    ts = 1000.0
    for i in range(6):
        pipeline.bus.publish(Topic.SIGNAL, Signal(
            ts=ts + i, symbol="TEST", type=SignalType.ABSORPTION, side=Side.BUY,
            price=100.0, confidence=0.9, explanation="bid absorbing", source="test",
        ))
    a = pipeline.fusion.assess(ts + 6, 100.0)
    assert a.control == "buyers"
    assert a.control_score > 0.15
    assert a.institutional_bias == "accumulating"
    assert a.reasons  # explanations propagate into the assessment


def _make_candles(pipeline, path, per_candle=5.0):
    ts = 0.0
    tid = 0
    for px in path:
        for off, d in ((1.0, 0.0), (2.0, 0.1), (3.0, -0.1), (4.0, 0.0)):
            tid += 1
            pipeline.bus.publish(Topic.TRADE, TradeEvent(ts + off, "TEST", px + d, 10.0, Side.BUY, tid))
        ts += per_candle
    pipeline.bus.publish(Topic.TRADE, TradeEvent(ts + 1, "TEST", path[-1], 10.0, Side.BUY, tid + 1))
    return ts + 1


def test_trade_plan_geometry(pipeline):
    # double top above, swing low below → pools on both sides of price
    path = [100, 99, 98, 99, 100, 101, 102, 101, 100, 101, 102, 101, 100.5]
    ts = _make_candles(pipeline, path)
    for i in range(8):
        pipeline.bus.publish(Topic.SIGNAL, Signal(
            ts=ts + i * 0.1, symbol="TEST", type=SignalType.ABSORPTION, side=Side.BUY,
            price=100.5, confidence=0.9, explanation="bid absorbing", source="test",
        ))
    a = pipeline.fusion.assess(ts + 1, 100.5)
    assert a.trade_plan is not None, "expected a trade plan with strong buy control and pools both sides"
    tp = a.trade_plan
    assert tp["direction"] == "buy"
    assert tp["stop"] < tp["entry"] < tp["targets"][0]
    assert tp["risk_reward"] > 0
    assert "stop" in tp["rationale"]


def test_narrator_speaks(pipeline):
    heard = []
    pipeline.bus.subscribe(Topic.NARRATION, heard.append)
    pipeline.bus.publish(Topic.SIGNAL, Signal(
        ts=1.0, symbol="TEST", type=SignalType.STOP_HUNT, side=Side.BUY,
        price=100.0, confidence=0.8, explanation="Sweep below the lows was reclaimed", source="test",
    ))
    assert heard, "narrator should speak on a high-confidence stop hunt"
    assert "confidence 80%" in heard[0].text
    assert heard[0].severity == "alert"


def test_answer_interface(pipeline):
    sim = make_sim(seed=5)
    feed_sim(pipeline, sim, seconds=30)
    ans = pipeline.fusion.answer("who is in control?", sim.ts, None)
    assert "answer" in ans and "confidence" in ans
    ans2 = pipeline.fusion.answer("what is the probability of continuation?", sim.ts, None)
    assert "%" in str(ans2["answer"])


def test_pattern_stats_learning():
    ps = PatternStats(horizon_s=10.0)
    # signals whose side always predicts the move
    for i in range(30):
        sig = Signal(ts=float(i * 20), symbol="T", type=SignalType.ABSORPTION, side=Side.BUY,
                     price=100.0, confidence=0.8, explanation="x", source="t")
        ps.observe_signal(sig, regime="up")
        ps.observe_price(sig.ts + 11, 101.0)  # resolves: price went up → hit
    report = ps.report()
    assert report[0]["pattern"] == "absorption"
    assert report[0]["hit_rate"] == 1.0
    weights = ps.weight_updates({SignalType.ABSORPTION: 1.0}, min_samples=10)
    assert weights[SignalType.ABSORPTION] > 1.0  # winning pattern boosted


def test_online_logit_learns():
    m = OnlineLogit(lr=0.1)
    # simple learnable rule: label = 1 iff feature x > 0
    import random

    rng = random.Random(1)
    for _ in range(400):
        x = rng.uniform(-1, 1)
        m.update({"x": x}, 1 if x > 0 else 0)
    assert m.predict({"x": 0.8}) > 0.7
    assert m.predict({"x": -0.8}) < 0.3


def test_composites_stop_hunt(pipeline):
    """Sweep through a swing low then reclaim → stop hunt + trapped sellers."""
    seen = []
    pipeline.bus.subscribe(Topic.SIGNAL, seen.append)
    # V-shape leaves an unswept swing low near 98.9
    path = [102, 101, 100, 99, 100, 101, 102]
    ts = _make_candles(pipeline, path)
    pool = pipeline.structure.state.liquidity_pools()
    lows = [p for p in pool if p["side"] == "below"]
    assert lows, "structure should expose a sell-side pool below"
    pool_price = lows[0]["price"]
    # a sweep prints right at the pool...
    pipeline.bus.publish(Topic.SIGNAL, Signal(
        ts=ts + 1, symbol="TEST", type=SignalType.SWEEP, side=Side.SELL,
        price=pool_price, confidence=0.7, explanation="sweep", source="sweep",
    ))
    # ...then price reclaims above it
    pipeline.bus.publish(Topic.TRADE, TradeEvent(ts + 5, "TEST", pool_price + 1.0, 10.0, Side.BUY, 999))
    types = {s.type for s in seen}
    assert SignalType.STOP_HUNT in types
    assert SignalType.TRAPPED_SELLERS in types
    hunt = next(s for s in seen if s.type is SignalType.STOP_HUNT)
    assert hunt.side is Side.BUY  # failed sweep down flips bias up
    assert "reclaimed" in hunt.explanation


def test_composites_fake_breakout(pipeline):
    seen = []
    pipeline.bus.subscribe(Topic.SIGNAL, seen.append)
    pipeline.bus.publish(Topic.SIGNAL, Signal(
        ts=100.0, symbol="TEST", type=SignalType.BOS, side=Side.BUY,
        price=105.0, confidence=0.7, explanation="bos", source="structure",
    ))
    # price falls back through the broken level → failed breakout
    pipeline.bus.publish(Topic.TRADE, TradeEvent(110.0, "TEST", 104.0, 10.0, Side.SELL, 1))
    types = {s.type for s in seen}
    assert SignalType.FAKE_BREAKOUT in types
    assert SignalType.TRAPPED_BUYERS in types
