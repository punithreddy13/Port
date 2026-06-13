#!/usr/bin/env python3
"""
BTCUSD 1-minute price-action scalper - backtest engine.

Strategy: "Range-Break Pullback Scalper" (pure price action, no indicators)
-----------------------------------------------------------------------------
On the 1-minute chart we read raw market structure - the rolling high/low of the
last `lookback` bars - and trade momentum breakouts that are confirmed by the
breakout candle itself:

  LONG  when close > highest_high(prev `lookback` bars)  AND  candle is bullish
        (close > open) and closes in the upper third of its own range.
  SHORT when close < lowest_low(prev `lookback` bars)    AND  candle is bearish
        (close < open) and closes in the lower third of its own range.

Risk is a fixed bracket scaled in % of entry price (typical scalper):
  - take-profit  = entry * (1 +/- TP_PCT)
  - stop-loss    = entry * (1 -/+ SL_PCT)
  - time-stop    = exit at close after MAX_BARS if neither level is hit.

Fills are realistic: a signal on bar i is entered at the OPEN of bar i+1
(no look-ahead). Inside a trade we check the STOP before the TARGET within the
same bar (conservative). Taker fees + slippage are charged on entry and exit.

The Pine Script port (pine/scalper_btc_priceaction_1m.pine) uses the identical
rules so the TradingView Strategy Tester reproduces this logic on full history.

Usage:
    python3 scalper_backtest.py [path/to/ohlcv.csv]

CSV columns: timestamp,open,high,low,close,volume
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass

# ----------------------------- Parameters -----------------------------------
LOOKBACK = 20          # bars used to define the breakout range
TP_PCT = 0.0025        # take-profit  = 0.25%
SL_PCT = 0.0015        # stop-loss    = 0.15%  (reward:risk = 1.67 : 1)
MAX_BARS = 30          # time-stop (bars) if neither TP nor SL is hit
CLOSE_FRAC = 0.34      # candle must close within this fraction of its extreme
FEE_PCT = 0.0004       # 0.04% taker fee per side (Binance spot taker)
SLIPPAGE_PCT = 0.0001  # 0.01% slippage per side
START_EQUITY = 10_000.0
RISK_PER_TRADE = 1.0   # fraction of equity deployed per trade (1.0 = all-in notional)


@dataclass
class Bar:
    ts: int
    o: float
    h: float
    l: float
    c: float
    v: float


@dataclass
class Trade:
    side: str
    entry_ts: int
    entry: float
    exit_ts: int
    exit: float
    reason: str
    ret_pct: float   # net return on notional after costs


def load_csv(path: str) -> list[Bar]:
    bars: list[Bar] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            bars.append(
                Bar(
                    int(float(row["timestamp"])),
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row.get("volume", 0) or 0),
                )
            )
    bars.sort(key=lambda b: b.ts)
    return bars


def signal(bars: list[Bar], i: int) -> str | None:
    """Return 'long'/'short'/None for a signal evaluated on bar i (closed)."""
    if i < LOOKBACK:
        return None
    window = bars[i - LOOKBACK : i]
    hh = max(b.h for b in window)
    ll = min(b.l for b in window)
    b = bars[i]
    rng = b.h - b.l
    if rng <= 0:
        return None
    close_pos = (b.c - b.l) / rng  # 0 = closed on low, 1 = closed on high
    bullish = b.c > b.o and close_pos >= (1 - CLOSE_FRAC)
    bearish = b.c < b.o and close_pos <= CLOSE_FRAC
    if b.c > hh and bullish:
        return "long"
    if b.c < ll and bearish:
        return "short"
    return None


def run(bars: list[Bar]) -> tuple[list[Trade], dict]:
    trades: list[Trade] = []
    i = LOOKBACK
    n = len(bars)
    while i < n - 1:
        sig = signal(bars, i)
        if sig is None:
            i += 1
            continue

        entry_bar = bars[i + 1]
        slip = SLIPPAGE_PCT if sig == "long" else -SLIPPAGE_PCT
        entry = entry_bar.o * (1 + slip)

        if sig == "long":
            tp = entry * (1 + TP_PCT)
            sl = entry * (1 - SL_PCT)
        else:
            tp = entry * (1 - TP_PCT)
            sl = entry * (1 + SL_PCT)

        exit_price = None
        exit_ts = entry_bar.ts
        reason = "time"
        # Walk forward from the entry bar through the time-stop window.
        for j in range(i + 1, min(i + 1 + MAX_BARS, n)):
            bar = bars[j]
            exit_ts = bar.ts
            if sig == "long":
                if bar.l <= sl:            # stop checked first (conservative)
                    exit_price, reason = sl, "stop"
                    break
                if bar.h >= tp:
                    exit_price, reason = tp, "target"
                    break
            else:
                if bar.h >= sl:
                    exit_price, reason = sl, "stop"
                    break
                if bar.l <= tp:
                    exit_price, reason = tp, "target"
                    break
        else:
            j = min(i + MAX_BARS, n - 1)
        if exit_price is None:                 # time-stop: exit at that bar close
            exit_price = bars[j].c
            exit_ts = bars[j].ts

        # gross directional return, then subtract round-trip fees
        if sig == "long":
            gross = (exit_price - entry) / entry
        else:
            gross = (entry - exit_price) / entry
        net = gross - 2 * FEE_PCT

        trades.append(
            Trade(sig, entry_bar.ts, entry, exit_ts, exit_price, reason, net)
        )
        i = j + 1   # no overlapping positions; resume after the exit bar

    stats = compute_stats(trades, bars)
    return trades, stats


def compute_stats(trades: list[Trade], bars: list[Bar]) -> dict:
    equity = START_EQUITY
    peak = equity
    max_dd = 0.0
    wins = losses = 0
    gross_win = gross_loss = 0.0
    curve = [equity]
    for t in trades:
        pnl = equity * RISK_PER_TRADE * t.ret_pct
        equity += pnl
        curve.append(equity)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        if t.ret_pct > 0:
            wins += 1
            gross_win += t.ret_pct
        else:
            losses += 1
            gross_loss += -t.ret_pct
    n = len(trades)
    win_rate = (wins / n * 100) if n else 0.0
    avg = (sum(t.ret_pct for t in trades) / n * 100) if n else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    span_min = (bars[-1].ts - bars[0].ts) / 60 if len(bars) > 1 else 0
    return {
        "bars": len(bars),
        "span_minutes": span_min,
        "trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "avg_trade_pct": avg,
        "profit_factor": pf,
        "max_drawdown_pct": max_dd * 100,
        "start_equity": START_EQUITY,
        "end_equity": equity,
        "total_return_pct": (equity / START_EQUITY - 1) * 100,
    }


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "scalper/data/btcusdt_1m_sample.csv"
    bars = load_csv(path)
    trades, s = run(bars)

    print("=" * 64)
    print("  BTCUSD 1m Price-Action Scalper - Backtest Result")
    print("=" * 64)
    print(f"  Data file        : {path}")
    print(f"  Bars             : {s['bars']}  (~{s['span_minutes']:.0f} min "
          f"= {s['span_minutes']/60:.1f} h)")
    print(f"  Params           : lookback={LOOKBACK}  TP={TP_PCT*100:.2f}%  "
          f"SL={SL_PCT*100:.2f}%  maxBars={MAX_BARS}")
    print(f"  Costs            : fee={FEE_PCT*100:.2f}%/side  "
          f"slippage={SLIPPAGE_PCT*100:.2f}%/side")
    print("-" * 64)
    print(f"  Trades           : {s['trades']}  "
          f"({s['wins']}W / {s['losses']}L)")
    print(f"  Win rate         : {s['win_rate']:.1f}%")
    print(f"  Avg trade        : {s['avg_trade_pct']:+.3f}%  (net of costs)")
    print(f"  Profit factor    : {s['profit_factor']:.2f}")
    print(f"  Max drawdown     : {s['max_drawdown_pct']:.2f}%")
    print(f"  Start equity     : ${s['start_equity']:,.2f}")
    print(f"  End equity       : ${s['end_equity']:,.2f}")
    print(f"  Total return     : {s['total_return_pct']:+.2f}%")
    print("=" * 64)

    if trades:
        print("\n  Trade log:")
        print(f"  {'#':>2} {'side':<5} {'entry':>10} {'exit':>10} "
              f"{'reason':<7} {'net%':>8}")
        for k, t in enumerate(trades, 1):
            print(f"  {k:>2} {t.side:<5} {t.entry:>10.2f} {t.exit:>10.2f} "
                  f"{t.reason:<7} {t.ret_pct*100:>+8.3f}")


if __name__ == "__main__":
    main()
