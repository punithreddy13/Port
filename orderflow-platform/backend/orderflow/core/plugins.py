"""Minimal plugin system.

Detectors, feeds and strategy metrics register themselves in named
registries. Third-party packages can extend the platform by registering
entries at import time (e.g. from an entry-point loader) without touching
core code.
"""
from __future__ import annotations

from typing import Any, Callable, TypeVar

T = TypeVar("T")


class Registry:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._items: dict[str, Any] = {}

    def register(self, name: str) -> Callable[[T], T]:
        def deco(obj: T) -> T:
            if name in self._items:
                raise ValueError(f"{self.kind} '{name}' already registered")
            self._items[name] = obj
            return obj

        return deco

    def get(self, name: str) -> Any:
        try:
            return self._items[name]
        except KeyError:
            raise KeyError(f"unknown {self.kind}: '{name}' (have: {sorted(self._items)})") from None

    def names(self) -> list[str]:
        return sorted(self._items)

    def items(self):
        return self._items.items()


DETECTORS = Registry("detector")
FEEDS = Registry("feed")
STRATEGY_METRICS = Registry("strategy-metric")
