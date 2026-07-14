"""Platform configuration with environment overrides.

Everything tunable lives here so a deployment can be reshaped without code
changes (ORDERFLOW_* env vars or a JSON file passed to ``load``).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any


@dataclass
class Config:
    symbol: str = "SIM-FUT"
    tick_size: float = 0.25
    feed: str = "simulated"  # simulated | binance | replay
    feed_options: dict[str, Any] = field(default_factory=dict)

    # engines
    candle_seconds: float = 5.0  # demo-friendly; 60 for production intraday
    book_depth_levels: int = 40
    heatmap_history: int = 600  # frames kept for the liquidity heatmap
    swing_strength: int = 3  # bars each side for a fractal swing

    # AI layer
    assessment_interval_s: float = 1.0
    signal_halflife_s: float = 30.0  # decay of signal influence in fusion

    # risk defaults
    account_equity: float = 100_000.0
    risk_per_trade_pct: float = 1.0
    daily_loss_limit_pct: float = 3.0
    max_position: float = 25.0

    # server
    host: str = "0.0.0.0"
    port: int = 8720
    ui_fps: float = 8.0  # heatmap frame rate pushed to clients

    data_dir: str = "data"

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        cfg = cls()
        if path and Path(path).exists():
            raw = json.loads(Path(path).read_text())
            for f in fields(cls):
                if f.name in raw:
                    setattr(cfg, f.name, raw[f.name])
        for f in fields(cls):
            env = os.environ.get(f"ORDERFLOW_{f.name.upper()}")
            if env is not None:
                current = getattr(cfg, f.name)
                if isinstance(current, bool):
                    setattr(cfg, f.name, env.lower() in ("1", "true", "yes"))
                elif isinstance(current, int):
                    setattr(cfg, f.name, int(env))
                elif isinstance(current, float):
                    setattr(cfg, f.name, float(env))
                elif isinstance(current, dict):
                    setattr(cfg, f.name, json.loads(env))
                else:
                    setattr(cfg, f.name, env)
        return cfg
