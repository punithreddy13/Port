"""Core event types flowing through the platform.

Every engine communicates exclusively through these events on the EventBus.
This keeps engines decoupled (event-driven design) and makes the entire
pipeline replayable: a recorded stream of MarketDataEvents deterministically
reproduces every downstream signal.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Optional


class Side(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class Topic(str, enum.Enum):
    """EventBus topics. Subscribers register per-topic."""

    BOOK_SNAPSHOT = "book.snapshot"
    BOOK_DELTA = "book.delta"
    TRADE = "trade"
    CANDLE = "candle"
    SIGNAL = "signal"
    STRUCTURE = "structure"
    ASSESSMENT = "assessment"
    NARRATION = "narration"
    RISK = "risk"
    ORDER = "order"
    FILL = "fill"
    REPLAY = "replay"
    SYSTEM = "system"


@dataclass(slots=True)
class Event:
    """Base event. ``ts`` is epoch seconds (float, exchange time)."""

    ts: float


@dataclass(slots=True)
class BookSnapshot(Event):
    """Full L2 snapshot: lists of (price, size) best-first."""

    symbol: str
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]


@dataclass(slots=True)
class BookDelta(Event):
    """Incremental L2 update. size == 0 removes the level."""

    symbol: str
    side: Side
    price: float
    size: float


@dataclass(slots=True)
class TradeEvent(Event):
    """Executed market order. ``aggressor`` is the taker side."""

    symbol: str
    price: float
    size: float
    aggressor: Side
    trade_id: int = 0


@dataclass(slots=True)
class Candle:
    ts_open: float
    ts_close: float
    open: float
    high: float
    low: float
    close: float
    volume: float
    buy_volume: float
    sell_volume: float
    # price -> [buy_vol, sell_vol]; the footprint of this candle
    footprint: dict[float, list[float]] = field(default_factory=dict)

    @property
    def delta(self) -> float:
        return self.buy_volume - self.sell_volume

    @property
    def is_bull(self) -> bool:
        return self.close >= self.open


@dataclass(slots=True)
class CandleEvent(Event):
    symbol: str
    candle: Candle
    closed: bool  # False while the candle is still forming


class SignalType(str, enum.Enum):
    # Liquidity engine
    LIQUIDITY_WALL = "liquidity_wall"
    SPOOFING = "spoofing"
    ICEBERG = "iceberg"
    ABSORPTION = "absorption"
    SWEEP = "liquidity_sweep"
    IMBALANCE = "imbalance"
    DELTA_IMBALANCE = "delta_imbalance"
    LARGE_PARTICIPANT = "large_participant"
    LIQUIDITY_MIGRATION = "liquidity_migration"
    LIQUIDITY_EXHAUSTION = "liquidity_exhaustion"
    PULLED_LIQUIDITY = "pulled_liquidity"
    ADDED_LIQUIDITY = "added_liquidity"
    HIDDEN_LIQUIDITY = "hidden_liquidity"
    AGGRESSIVE_FLOW = "aggressive_flow"
    # Market structure engine
    SWING_HIGH = "swing_high"
    SWING_LOW = "swing_low"
    BOS = "break_of_structure"
    CHOCH = "change_of_character"
    FVG = "fair_value_gap"
    ORDER_BLOCK = "order_block"
    BREAKER_BLOCK = "breaker_block"
    MITIGATION = "mitigation_block"
    LIQUIDITY_POOL = "liquidity_pool"
    EQUAL_HIGHS = "equal_highs"
    EQUAL_LOWS = "equal_lows"
    PREMIUM_ZONE = "premium_zone"
    DISCOUNT_ZONE = "discount_zone"
    # Composite (cross-engine) detections
    STOP_HUNT = "stop_hunt"
    FAKE_BREAKOUT = "fake_breakout"
    TRAPPED_BUYERS = "trapped_buyers"
    TRAPPED_SELLERS = "trapped_sellers"
    ACCUMULATION = "accumulation"
    DISTRIBUTION = "distribution"


@dataclass(slots=True)
class Signal(Event):
    """A detection with an explanation.

    The platform's contract: no signal is emitted without ``explanation``
    (why it fired) and ``confidence`` in [0, 1].
    """

    symbol: str
    type: SignalType
    side: Optional[Side]  # side the signal favours (BUY = bullish)
    price: float
    confidence: float
    explanation: str
    data: dict[str, Any] = field(default_factory=dict)
    source: str = ""  # emitting detector, for analytics/learning

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "symbol": self.symbol,
            "type": self.type.value,
            "side": self.side.value if self.side else None,
            "price": self.price,
            "confidence": round(self.confidence, 4),
            "explanation": self.explanation,
            "data": self.data,
            "source": self.source,
        }


@dataclass(slots=True)
class Assessment(Event):
    """The AI layer's rolling interpretation of the market."""

    symbol: str
    control: str  # "buyers" | "sellers" | "balanced"
    control_score: float  # -1 (sellers) .. +1 (buyers)
    institutional_bias: str  # "accumulating" | "distributing" | "neutral"
    p_continuation: float
    p_reversal: float
    confidence: float
    trade_plan: Optional[dict[str, Any]]
    features: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "symbol": self.symbol,
            "control": self.control,
            "control_score": round(self.control_score, 4),
            "institutional_bias": self.institutional_bias,
            "p_continuation": round(self.p_continuation, 4),
            "p_reversal": round(self.p_reversal, 4),
            "confidence": round(self.confidence, 4),
            "trade_plan": self.trade_plan,
            "features": {k: round(v, 4) for k, v in self.features.items()},
            "reasons": self.reasons,
        }


@dataclass(slots=True)
class Narration(Event):
    symbol: str
    text: str
    severity: str = "info"  # info | notice | alert
    related_signal: Optional[SignalType] = None
