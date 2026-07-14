"""Market structure engine — smart-money-concepts detection on closed candles.

Detects: swing highs/lows, BOS, CHOCH, fair value gaps, order blocks,
breaker & mitigation blocks, equal highs/lows, liquidity pools,
premium/discount zones, internal vs external liquidity.

Everything is derived from the candle series so it works identically live,
in replay and in backtests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..core import Candle, CandleEvent, EventBus, Side, Signal, SignalType, Topic
from ..core.models import clamp


@dataclass(slots=True)
class Swing:
    ts: float
    price: float
    kind: str  # "high" | "low"
    swept: bool = False


@dataclass(slots=True)
class Zone:
    """A price zone with a lifecycle (order block / FVG / breaker...)."""

    kind: str
    side: Side  # side expected to defend / react at this zone
    top: float
    bottom: float
    ts: float
    mitigated: bool = False
    broken: bool = False

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def as_dict(self) -> dict:
        return {"kind": self.kind, "side": self.side.value, "top": self.top,
                "bottom": self.bottom, "ts": self.ts, "mitigated": self.mitigated, "broken": self.broken}


@dataclass
class StructureState:
    trend: str = "none"  # "up" | "down" | "none"
    swings: list[Swing] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    last_bos_ts: float = 0.0
    equal_highs: list[float] = field(default_factory=list)
    equal_lows: list[float] = field(default_factory=list)

    def recent_swing(self, kind: str) -> Optional[Swing]:
        for s in reversed(self.swings):
            if s.kind == kind:
                return s
        return None

    def liquidity_pools(self) -> list[dict]:
        """Where resting stops cluster: above equal/swing highs, below lows."""
        pools = []
        for p in self.equal_highs:
            pools.append({"price": p, "side": "above", "kind": "equal_highs"})
        for p in self.equal_lows:
            pools.append({"price": p, "side": "below", "kind": "equal_lows"})
        hi = self.recent_swing("high")
        lo = self.recent_swing("low")
        if hi and not hi.swept:
            pools.append({"price": hi.price, "side": "above", "kind": "swing_high"})
        if lo and not lo.swept:
            pools.append({"price": lo.price, "side": "below", "kind": "swing_low"})
        return pools


class MarketStructureEngine:
    def __init__(self, bus: EventBus, symbol: str, tick_size: float, swing_strength: int = 3,
                 eq_tolerance_ticks: float = 2.0) -> None:
        self.bus = bus
        self.symbol = symbol
        self.tick = tick_size
        self.k = swing_strength
        self.eq_tol = eq_tolerance_ticks * tick_size
        self.candles: list[Candle] = []
        self.state = StructureState()
        bus.subscribe(Topic.CANDLE, self._on_candle)

    def _emit(self, ts: float, type_: SignalType, side: Side | None, price: float,
              confidence: float, explanation: str, **data: object) -> None:
        self.bus.publish(Topic.SIGNAL, Signal(
            ts=ts, symbol=self.symbol, type=type_, side=side, price=price,
            confidence=clamp(confidence), explanation=explanation, data=dict(data), source="structure",
        ))

    def _on_candle(self, ev: CandleEvent) -> None:  # type: ignore[override]
        if not ev.closed:
            return
        self.candles.append(ev.candle)
        if len(self.candles) > 600:
            del self.candles[:100]
        self._detect_swings(ev.ts)
        self._detect_bos_choch(ev.candle)
        self._detect_fvg(ev.candle)
        self._update_zones(ev.candle)
        self._detect_equal_levels(ev.ts)

    # -- swings ------------------------------------------------------------
    def _detect_swings(self, ts: float) -> None:
        k = self.k
        if len(self.candles) < 2 * k + 1:
            return
        i = len(self.candles) - 1 - k  # candidate pivot (confirmed k bars later)
        c = self.candles[i]
        left = self.candles[i - k : i]
        right = self.candles[i + 1 :]
        if all(c.high >= x.high for x in left) and all(c.high >= x.high for x in right):
            if not any(abs(s.price - c.high) < 1e-9 and s.kind == "high" for s in self.state.swings[-5:]):
                self.state.swings.append(Swing(ts=c.ts_open, price=c.high, kind="high"))
                self._emit(ts, SignalType.SWING_HIGH, None, c.high, 0.6,
                           f"Swing high confirmed at {c.high:g} (fractal {k}/{k}).")
        if all(c.low <= x.low for x in left) and all(c.low <= x.low for x in right):
            if not any(abs(s.price - c.low) < 1e-9 and s.kind == "low" for s in self.state.swings[-5:]):
                self.state.swings.append(Swing(ts=c.ts_open, price=c.low, kind="low"))
                self._emit(ts, SignalType.SWING_LOW, None, c.low, 0.6,
                           f"Swing low confirmed at {c.low:g} (fractal {k}/{k}).")
        if len(self.state.swings) > 100:
            del self.state.swings[:20]

    # -- BOS / CHOCH ---------------------------------------------------------
    def _detect_bos_choch(self, c: Candle) -> None:
        st = self.state
        hi = st.recent_swing("high")
        lo = st.recent_swing("low")
        ts = c.ts_close
        if hi and not hi.swept and c.close > hi.price:
            hi.swept = True
            if st.trend in ("up", "none"):
                st.trend = "up"
                self._emit(ts, SignalType.BOS, Side.BUY, hi.price, 0.65,
                           f"Break of structure: close {c.close:g} above swing high {hi.price:g} — uptrend continues.",
                           trend="up")
            else:
                st.trend = "up"
                self._emit(ts, SignalType.CHOCH, Side.BUY, hi.price, 0.7,
                           f"Change of character: close {c.close:g} reclaimed swing high {hi.price:g} "
                           "against the downtrend — bullish shift.", trend="up")
            st.last_bos_ts = ts
            self._mark_order_block(Side.BUY, ts)
        if lo and not lo.swept and c.close < lo.price:
            lo.swept = True
            if st.trend in ("down", "none"):
                st.trend = "down"
                self._emit(ts, SignalType.BOS, Side.SELL, lo.price, 0.65,
                           f"Break of structure: close {c.close:g} below swing low {lo.price:g} — downtrend continues.",
                           trend="down")
            else:
                st.trend = "down"
                self._emit(ts, SignalType.CHOCH, Side.SELL, lo.price, 0.7,
                           f"Change of character: close {c.close:g} lost swing low {lo.price:g} "
                           "against the uptrend — bearish shift.", trend="down")
            st.last_bos_ts = ts
            self._mark_order_block(Side.SELL, ts)

    def _mark_order_block(self, break_side: Side, ts: float) -> None:
        """Order block = last opposite-coloured candle before the break."""
        for c in reversed(self.candles[-10:]):
            opposite = (not c.is_bull) if break_side is Side.BUY else c.is_bull
            if opposite:
                zone = Zone(kind="order_block", side=break_side, top=max(c.open, c.close),
                            bottom=min(c.open, c.close), ts=ts)
                self.state.zones.append(zone)
                self._emit(ts, SignalType.ORDER_BLOCK, break_side, (zone.top + zone.bottom) / 2, 0.55,
                           f"{break_side.value.capitalize()}-side order block {zone.bottom:g}–{zone.top:g}: "
                           "last opposing candle before the structural break; expect a reaction on revisit.",
                           top=zone.top, bottom=zone.bottom)
                break

    # -- FVG -----------------------------------------------------------------
    def _detect_fvg(self, c: Candle) -> None:
        if len(self.candles) < 3:
            return
        a, b, cc = self.candles[-3], self.candles[-2], self.candles[-1]
        ts = cc.ts_close
        if cc.low > a.high:  # bullish gap
            zone = Zone(kind="fvg", side=Side.BUY, top=cc.low, bottom=a.high, ts=ts)
            self.state.zones.append(zone)
            self._emit(ts, SignalType.FVG, Side.BUY, (zone.top + zone.bottom) / 2, 0.5,
                       f"Bullish fair value gap {a.high:g}–{cc.low:g}: price moved too fast to fill — "
                       "an inefficiency likely to be revisited.", top=zone.top, bottom=zone.bottom)
        if cc.high < a.low:  # bearish gap
            zone = Zone(kind="fvg", side=Side.SELL, top=a.low, bottom=cc.high, ts=ts)
            self.state.zones.append(zone)
            self._emit(ts, SignalType.FVG, Side.SELL, (zone.top + zone.bottom) / 2, 0.5,
                       f"Bearish fair value gap {cc.high:g}–{a.low:g}: unfilled inefficiency overhead.",
                       top=zone.top, bottom=zone.bottom)

    # -- zone lifecycle --------------------------------------------------------
    def _update_zones(self, c: Candle) -> None:
        ts = c.ts_close
        for z in self.state.zones:
            if z.broken:
                continue
            if not z.mitigated and (z.contains(c.low) or z.contains(c.high) or (c.low < z.bottom and c.high > z.top)):
                z.mitigated = True
                if z.kind == "order_block":
                    self._emit(ts, SignalType.MITIGATION, z.side, (z.top + z.bottom) / 2, 0.5,
                               f"Price returned into the {z.side.value} order block {z.bottom:g}–{z.top:g} "
                               "(mitigation) — watch for the defended reaction.",
                               top=z.top, bottom=z.bottom)
            # broken zone flips into a breaker
            if z.side is Side.BUY and c.close < z.bottom:
                z.broken = True
            elif z.side is Side.SELL and c.close > z.top:
                z.broken = True
            if z.broken and z.kind == "order_block":
                flipped = Zone(kind="breaker", side=z.side.opposite, top=z.top, bottom=z.bottom, ts=ts)
                self.state.zones.append(flipped)
                self._emit(ts, SignalType.BREAKER_BLOCK, flipped.side, (z.top + z.bottom) / 2, 0.5,
                           f"Order block {z.bottom:g}–{z.top:g} failed and flipped — now a "
                           f"{flipped.side.value}-side breaker block.", top=z.top, bottom=z.bottom)
        if len(self.state.zones) > 80:
            self.state.zones = [z for z in self.state.zones if not z.broken][-60:]

    # -- equal highs/lows, pools, premium/discount ------------------------------
    def _detect_equal_levels(self, ts: float) -> None:
        st = self.state
        highs = [s for s in st.swings if s.kind == "high"][-6:]
        lows = [s for s in st.swings if s.kind == "low"][-6:]
        st.equal_highs = self._find_equal([s.price for s in highs])
        st.equal_lows = self._find_equal([s.price for s in lows])
        for p in st.equal_highs:
            self._emit(ts, SignalType.EQUAL_HIGHS, Side.SELL, p, 0.45,
                       f"Equal highs at {p:g} — buy-side stop liquidity resting above.", price_level=p)
        for p in st.equal_lows:
            self._emit(ts, SignalType.EQUAL_LOWS, Side.BUY, p, 0.45,
                       f"Equal lows at {p:g} — sell-side stop liquidity resting below.", price_level=p)

    def _find_equal(self, prices: list[float]) -> list[float]:
        out = []
        for i, p in enumerate(prices):
            for q in prices[i + 1 :]:
                if abs(p - q) <= self.eq_tol:
                    lvl = (p + q) / 2
                    if not any(abs(lvl - x) <= self.eq_tol for x in out):
                        out.append(lvl)
        return out

    # -- queries -----------------------------------------------------------------
    def premium_discount(self, price: float) -> tuple[str, float]:
        """Position of price in the current dealing range: >0.5 premium."""
        hi = self.state.recent_swing("high")
        lo = self.state.recent_swing("low")
        if not hi or not lo or hi.price <= lo.price:
            return "unknown", 0.5
        x = (price - lo.price) / (hi.price - lo.price)
        if x > 0.62:
            return "premium", x
        if x < 0.38:
            return "discount", x
        return "equilibrium", x

    def features(self, price: float) -> dict[str, float]:
        zone_kind, x = self.premium_discount(price)
        st = self.state
        pools = st.liquidity_pools()
        above = min((p["price"] for p in pools if p["side"] == "above" and p["price"] > price), default=None)
        below = max((p["price"] for p in pools if p["side"] == "below" and p["price"] < price), default=None)
        return {
            "trend_up": 1.0 if st.trend == "up" else 0.0,
            "trend_down": 1.0 if st.trend == "down" else 0.0,
            "range_position": x,
            "in_premium": 1.0 if zone_kind == "premium" else 0.0,
            "in_discount": 1.0 if zone_kind == "discount" else 0.0,
            "pool_above_dist": (above - price) if above is not None else 0.0,
            "pool_below_dist": (price - below) if below is not None else 0.0,
        }

    def as_ui(self) -> dict:
        st = self.state
        return {
            "trend": st.trend,
            "swings": [{"ts": s.ts, "price": s.price, "kind": s.kind, "swept": s.swept} for s in st.swings[-20:]],
            "zones": [z.as_dict() for z in st.zones[-25:] if not z.broken],
            "equal_highs": st.equal_highs,
            "equal_lows": st.equal_lows,
            "pools": st.liquidity_pools(),
        }
