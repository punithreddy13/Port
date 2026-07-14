"""Synchronous, deterministic event bus.

Determinism matters more than raw concurrency here: replaying the same
recorded event stream must always produce the same signals, so engines are
invoked in subscription order on the publisher's thread. The async server
bridges bus events into asyncio queues (see server/app.py); heavy fan-out to
UI clients happens there, off the hot path.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable

from .events import Event, Topic

log = logging.getLogger(__name__)

Handler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[Topic, list[Handler]] = defaultdict(list)
        self._any_subs: list[Callable[[Topic, Event], None]] = []
        self.events_published = 0

    def subscribe(self, topic: Topic, handler: Handler) -> None:
        self._subs[topic].append(handler)

    def subscribe_all(self, handler: Callable[[Topic, Event], None]) -> None:
        """Receive every event with its topic (used by recorder & server)."""
        self._any_subs.append(handler)

    def unsubscribe(self, topic: Topic, handler: Handler) -> None:
        if handler in self._subs.get(topic, []):
            self._subs[topic].remove(handler)

    def publish(self, topic: Topic, event: Event) -> None:
        self.events_published += 1
        for handler in self._subs.get(topic, ()):
            try:
                handler(event)
            except Exception:  # noqa: BLE001 - one bad handler must not kill the pipeline
                log.exception("handler %s failed for topic %s", handler, topic)
        for handler in self._any_subs:
            try:
                handler(topic, event)
            except Exception:  # noqa: BLE001
                log.exception("wildcard handler %s failed", handler)
