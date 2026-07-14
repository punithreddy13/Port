from .broker import Order, PaperBroker, Position

__all__ = ["AIDecisionLog", "Order", "PaperBroker", "Position", "ReplayEngine", "ReplayStatus"]


def __getattr__(name: str):
    # ReplayEngine builds a Pipeline, which itself uses the broker above —
    # lazy export breaks the pipeline ↔ backtest import cycle.
    if name in ("AIDecisionLog", "ReplayEngine", "ReplayStatus"):
        from . import replay

        return getattr(replay, name)
    raise AttributeError(name)
