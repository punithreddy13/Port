from orderflow.core import Side, SignalType, Topic, TradeEvent


def trade(pipeline, ts, price, size=10.0, side=Side.BUY, tid=0):
    pipeline.bus.publish(Topic.TRADE, TradeEvent(ts, "TEST", price, size, side, tid))


def make_candles(pipeline, path, start_ts=0.0, per_candle=5.0):
    """path: list of candle close prices. Each candle opens at the previous
    close so candles have real bodies (needed for order-block detection)."""
    ts = start_ts
    prev = path[0]
    tid = 0
    for px in path:
        hi = max(prev, px) + 0.1
        lo = min(prev, px) - 0.1
        for off, p in ((1.0, prev), (2.0, hi), (3.0, lo), (4.0, px)):
            tid += 1
            trade(pipeline, ts + off, p, tid=tid)
        prev = px
        ts += per_candle
    # one extra trade to close the final candle
    trade(pipeline, ts + 1.0, path[-1], tid=tid + 1)


def collect(pipeline):
    seen = []
    pipeline.bus.subscribe(Topic.SIGNAL, seen.append)
    return seen


def test_swings_and_bos(pipeline):
    seen = collect(pipeline)
    # up-leg, pullback (swing high forms), then break above → BOS
    path = [100, 101, 102, 103, 102.5, 102, 101.5, 102, 102.5, 103.5, 104, 104.5, 105]
    make_candles(pipeline, path)
    types = [s.type for s in seen]
    assert SignalType.SWING_HIGH in types
    assert SignalType.BOS in types
    bos = next(s for s in seen if s.type is SignalType.BOS)
    assert bos.side is Side.BUY
    assert pipeline.structure.state.trend == "up"


def test_choch(pipeline):
    seen = collect(pipeline)
    # uptrend established, then a break below the swing low → CHOCH
    path = [100, 101, 102, 103, 102.5, 102, 102.5, 103.5, 104.5,  # up + BOS
            103.5, 102.5, 101.5, 100.5, 99.5, 98.5, 97.5]          # collapse
    make_candles(pipeline, path)
    chochs = [s for s in seen if s.type is SignalType.CHOCH]
    assert chochs, "expected change of character"
    assert chochs[0].side is Side.SELL
    assert pipeline.structure.state.trend == "down"


def test_fvg(pipeline):
    seen = collect(pipeline)
    ts = 0.0
    # candle 1 around 100, candle 2 gaps, candle 3 leaves low above candle-1 high
    trade(pipeline, ts + 1, 100.0, tid=1)
    trade(pipeline, ts + 2, 100.2, tid=2)  # high of candle 1 = 100.2
    ts = 5.0
    trade(pipeline, ts + 1, 101.5, tid=3)
    ts = 10.0
    trade(pipeline, ts + 1, 102.5, tid=4)  # low 102.5 > 100.2 → bullish FVG
    trade(pipeline, ts + 2, 102.6, tid=5)
    ts = 15.0
    trade(pipeline, ts + 1, 102.7, tid=6)  # closes candle 3
    fvgs = [s for s in seen if s.type is SignalType.FVG]
    assert fvgs and fvgs[0].side is Side.BUY
    assert fvgs[0].data["bottom"] == 100.2


def test_order_block_and_zones(pipeline):
    seen = collect(pipeline)
    # down candle at the start of the up-leg becomes the order block after BOS
    path = [100, 99.5, 100.5, 101.5, 102.5, 101.8, 101.2, 101.8, 102.8, 103.8, 104.8]
    make_candles(pipeline, path)
    obs = [s for s in seen if s.type is SignalType.ORDER_BLOCK]
    assert obs, "expected an order block after BOS"
    assert obs[0].side is Side.BUY
    zones = pipeline.structure.state.zones
    assert any(z.kind == "order_block" for z in zones)


def test_premium_discount(pipeline):
    path = [100, 101, 102, 103, 102.5, 102, 101.5, 102, 103.5, 104.5, 103.5, 103, 102.5, 103]
    make_candles(pipeline, path)
    kind_hi, x_hi = pipeline.structure.premium_discount(104.4)
    kind_lo, x_lo = pipeline.structure.premium_discount(100.2)
    assert x_hi > x_lo
    assert kind_hi == "premium"
    assert kind_lo == "discount"


def test_liquidity_pools(pipeline):
    path = [100, 101, 102, 103, 102, 101, 102, 103, 102, 101, 100.5, 101]  # double top ~103
    make_candles(pipeline, path)
    pools = pipeline.structure.state.liquidity_pools()
    assert pools, "expected liquidity pools"
    assert any(p["side"] == "above" for p in pools)
