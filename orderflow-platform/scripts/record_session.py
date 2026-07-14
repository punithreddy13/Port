#!/usr/bin/env python3
"""Record a market-data session to JSONL for replay/backtesting.

Examples:
    # 10 minutes of simulated data
    python scripts/record_session.py --feed simulated --seconds 600 --out data/sim.jsonl

    # live Binance BTCUSDT depth+trades (needs network)
    python scripts/record_session.py --feed binance --symbol BTCUSDT --seconds 300 --out data/btc.jsonl
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from orderflow.core import EventBus, Topic  # noqa: E402
from orderflow.core.plugins import FEEDS  # noqa: E402
from orderflow.feeds.recorder import DataRecorder  # noqa: E402
import orderflow.feeds  # noqa: E402,F401  (registers feeds)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="simulated", choices=["simulated", "binance"])
    ap.add_argument("--symbol", default="SIM-FUT")
    ap.add_argument("--seconds", type=float, default=600)
    ap.add_argument("--out", default="data/session.jsonl")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    bus = EventBus()
    recorder = DataRecorder(bus, args.out)
    kwargs = {"seed": args.seed} if args.feed == "simulated" and args.seed is not None else {}
    feed = FEEDS.get(args.feed)(bus, args.symbol, **kwargs)
    feed.start()
    deadline = time.time() + args.seconds
    try:
        while time.time() < deadline:
            time.sleep(1)
            print(f"\r{recorder.rows_written:,} events recorded", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        feed.stop()
        recorder.close()
        print(f"\nwrote {recorder.rows_written:,} events to {args.out}")


if __name__ == "__main__":
    main()
