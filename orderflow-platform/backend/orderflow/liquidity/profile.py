"""Volume distribution analytics: volume profile, session profile, POC,
value area, developing POC, VWAP and anchored VWAP."""
from __future__ import annotations

from collections import defaultdict

from ..core import Side


class VolumeProfile:
    def __init__(self, tick_size: float, bin_ticks: int = 1) -> None:
        self.bin = tick_size * bin_ticks
        self.volumes: dict[float, float] = defaultdict(float)
        self.buy_volumes: dict[float, float] = defaultdict(float)
        self.sell_volumes: dict[float, float] = defaultdict(float)
        self.total = 0.0
        self.poc_history: list[tuple[float, float]] = []  # (ts, poc) — developing POC

    def _bin(self, price: float) -> float:
        return round(round(price / self.bin) * self.bin, 10)

    def add_trade(self, ts: float, price: float, size: float, aggressor: Side) -> None:
        b = self._bin(price)
        self.volumes[b] += size
        if aggressor is Side.BUY:
            self.buy_volumes[b] += size
        else:
            self.sell_volumes[b] += size
        self.total += size
        poc = self.poc
        if poc is not None and (not self.poc_history or self.poc_history[-1][1] != poc):
            self.poc_history.append((ts, poc))

    @property
    def poc(self) -> float | None:
        if not self.volumes:
            return None
        return max(self.volumes.items(), key=lambda kv: kv[1])[0]

    def value_area(self, pct: float = 0.70) -> tuple[float, float] | None:
        """Classic VA expansion outward from POC until pct of volume covered."""
        if not self.volumes:
            return None
        rows = sorted(self.volumes.items())
        prices = [p for p, _ in rows]
        vols = [v for _, v in rows]
        poc_i = max(range(len(vols)), key=lambda i: vols[i])
        target = self.total * pct
        acc = vols[poc_i]
        lo = hi = poc_i
        while acc < target and (lo > 0 or hi < len(vols) - 1):
            up = vols[hi + 1] if hi < len(vols) - 1 else -1.0
            down = vols[lo - 1] if lo > 0 else -1.0
            if up >= down:
                hi += 1
                acc += up
            else:
                lo -= 1
                acc += down
        return prices[lo], prices[hi]

    def as_dict(self, max_bins: int = 120) -> dict:
        rows = sorted(self.volumes.items())
        if len(rows) > max_bins:
            # keep the densest region around POC
            poc = self.poc
            rows.sort(key=lambda kv: abs(kv[0] - (poc or 0)))
            rows = sorted(rows[:max_bins])
        va = self.value_area()
        return {
            "bins": [[p, round(v, 1), round(self.buy_volumes.get(p, 0.0), 1), round(self.sell_volumes.get(p, 0.0), 1)] for p, v in rows],
            "poc": self.poc,
            "value_area": list(va) if va else None,
            "total": round(self.total, 1),
        }


class Vwap:
    """Standard VWAP plus arbitrary anchored VWAPs."""

    def __init__(self) -> None:
        self._pv = 0.0
        self._v = 0.0
        self.anchors: dict[str, list[float]] = {}  # name -> [pv, v, anchor_ts]

    def add_trade(self, ts: float, price: float, size: float) -> None:
        self._pv += price * size
        self._v += size
        for acc in self.anchors.values():
            acc[0] += price * size
            acc[1] += size

    def anchor(self, name: str, ts: float) -> None:
        self.anchors[name] = [0.0, 0.0, ts]

    @property
    def value(self) -> float | None:
        return self._pv / self._v if self._v > 0 else None

    def anchored(self, name: str) -> float | None:
        acc = self.anchors.get(name)
        if not acc or acc[1] <= 0:
            return None
        return acc[0] / acc[1]

    def reset(self) -> None:
        self._pv = self._v = 0.0


class SessionTracker:
    """Owns the session-scoped profile & VWAP; rolls over on session change."""

    def __init__(self, tick_size: float, session_seconds: float = 24 * 3600.0) -> None:
        self.tick_size = tick_size
        self.session_seconds = session_seconds
        self.session_start: float | None = None
        self.profile = VolumeProfile(tick_size)
        self.session_profile = VolumeProfile(tick_size)
        self.vwap = Vwap()
        self.cum_delta = 0.0
        self.cum_delta_series: list[tuple[float, float]] = []

    def add_trade(self, ts: float, price: float, size: float, aggressor: Side) -> None:
        if self.session_start is None:
            self.session_start = ts
        elif ts - self.session_start >= self.session_seconds:
            self.session_start = ts
            self.session_profile = VolumeProfile(self.tick_size)
            self.vwap.reset()
        self.profile.add_trade(ts, price, size, aggressor)
        self.session_profile.add_trade(ts, price, size, aggressor)
        self.vwap.add_trade(ts, price, size)
        self.cum_delta += size if aggressor is Side.BUY else -size
        self.cum_delta_series.append((ts, self.cum_delta))
        if len(self.cum_delta_series) > 5000:
            del self.cum_delta_series[:1000]
