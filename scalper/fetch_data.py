#!/usr/bin/env python3
"""
Fetch BTCUSD 1-minute OHLCV and write a CSV the backtester can read.

The committed sample (data/btcusdt_1m_sample.csv) is real Binance BTC-USDT 1m
data pulled from CryptoCompare's Data API. Use this script to regenerate a
larger / fresher dataset whenever you have outbound network access, then run:

    python3 scalper/fetch_data.py --limit 2000 --out scalper/data/btcusdt_1m.csv
    python3 scalper/scalper_backtest.py scalper/data/btcusdt_1m.csv

The endpoint returns at most 2000 bars per call and is paginated backwards with
`to_ts`, so larger spans are stitched together automatically.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.request

API = "https://data-api.cryptocompare.com/spot/v1/historical/minutes"


def fetch_chunk(market: str, instrument: str, limit: int, to_ts: int | None):
    params = f"?market={market}&instrument={instrument}&limit={limit}&aggregate=1"
    if to_ts is not None:
        params += f"&to_ts={to_ts}"
    with urllib.request.urlopen(API + params, timeout=20) as r:
        return json.loads(r.read())["Data"]


def fetch(market: str, instrument: str, total: int) -> list[dict]:
    rows: list[dict] = []
    to_ts: int | None = None
    while len(rows) < total:
        n = min(2000, total - len(rows))
        chunk = fetch_chunk(market, instrument, n, to_ts)
        if not chunk:
            break
        rows = chunk + rows
        to_ts = chunk[0]["TIMESTAMP"] - 60
        time.sleep(0.25)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="binance")
    ap.add_argument("--instrument", default="BTC-USDT")
    ap.add_argument("--limit", type=int, default=2000, help="number of 1m bars")
    ap.add_argument("--out", default="scalper/data/btcusdt_1m.csv")
    a = ap.parse_args()

    rows = fetch(a.market, a.instrument, a.limit)
    rows.sort(key=lambda r: r["TIMESTAMP"])
    with open(a.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for r in rows:
            w.writerow([r["TIMESTAMP"], r["OPEN"], r["HIGH"],
                        r["LOW"], r["CLOSE"], r["VOLUME"]])
    print(f"Wrote {len(rows)} bars to {a.out}")


if __name__ == "__main__":
    main()
