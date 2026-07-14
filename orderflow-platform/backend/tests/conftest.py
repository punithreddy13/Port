import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from orderflow.core import Config, Topic
from orderflow.feeds.simulated import MarketSimulator
from orderflow.pipeline import Pipeline


@pytest.fixture
def config() -> Config:
    return Config(symbol="TEST", tick_size=0.25, candle_seconds=5.0, data_dir="/tmp/orderflow-test-data")


@pytest.fixture
def pipeline(config: Config) -> Pipeline:
    return Pipeline(config)


def make_sim(seed: int = 7, inject: bool = True) -> MarketSimulator:
    return MarketSimulator("TEST", tick=0.25, seed=seed, inject=inject)


def feed_sim(pipeline: Pipeline, sim: MarketSimulator, seconds: float, dt: float = 0.1) -> None:
    pipeline.bus.publish(Topic.BOOK_SNAPSHOT, sim.snapshot())
    steps = int(seconds / dt)
    for _ in range(steps):
        for topic, ev in sim.step(dt):
            pipeline.bus.publish(topic, ev)
