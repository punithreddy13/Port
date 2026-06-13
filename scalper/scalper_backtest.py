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
MODE = "breakout"      # "breakout" (momentum) or "reversion" (mean-reversion)
LOOKBACK = 20          # bars used to define the breakout range
TP_PCT = 0.0025        # take-profit  = 0.25%
SL_PCT = 0.0015        # stop-loss    = 0.15%  (reward:risk = 1.67 : 1)
MAX_BARS = 30          # time-stop (bars) if neither TP nor SL is hit
CLOSE_FRAC = 0.34      # candle must close within this fraction of its extreme
EMA_LEN = 20           # (reversion) EMA anchor length
DEV_PCT = 0.0015       # (reversion) min % stretch from EMA
BRACKET = "pct"        # "pct" (fixed %) or "atr" (volatility-scaled)
ATR_LEN = 14           # ATR lookback for the atr bracket
TP_ATR = 1.0           # take-profit = TP_ATR * ATR (atr bracket)
SL_ATR = 1.0           # stop-loss   = SL_ATR * ATR (atr bracket)
TRADE_LONG = True      # allow long entries
TRADE_SHORT = True     # allow short entries (needs margin/futures on spot) to fade
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


def atr_series(bars: list[Bar], length: int) -> list[float]:
    """Average True Range (simple MA of true range), aligned to bars."""
    tr: list[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            tr.append(b.h - b.l)
        else:
            pc = bars[i - 1].c
            tr.append(max(b.h - b.l, abs(b.h - pc), abs(b.l - pc)))
    out: list[float] = []
    run_sum = 0.0
    for i, t in enumerate(tr):
        run_sum += t
        if i >= length:
            run_sum -= tr[i - length]
        out.append(run_sum / min(i + 1, length))
    return out


def ema_series(bars: list[Bar], length: int) -> list[float]:
    """Exponential moving average of close, aligned to bars."""
    k = 2.0 / (length + 1)
    out: list[float] = []
    prev = bars[0].c
    for b in bars:
        prev = b.c * k + prev * (1 - k)
        out.append(prev)
    return out


def signal(bars: list[Bar], i: int, ema: list[float] | None = None) -> str | None:
    """Return 'long'/'short'/None for a signal evaluated on bar i (closed)."""
    b = bars[i]
    if MODE == "reversion":
        # Fade a stretch away from the EMA anchor, with a reversal candle.
        if i < EMA_LEN or ema is None:
            return None
        anchor = ema[i]
        dev = (b.c - anchor) / anchor
        if dev <= -DEV_PCT and b.c > b.o:      # stretched below + bullish bar
            return "long"
        if dev >= DEV_PCT and b.c < b.o:        # stretched above + bearish bar
            return "short"
        return None
    # ---- breakout (momentum) ----
    if i < LOOKBACK:
        return None
    window = bars[i - LOOKBACK : i]
    hh = max(bb.h for bb in window)
    ll = min(bb.l for bb in window)
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
    n = len(bars)
    ema = ema_series(bars, EMA_LEN) if MODE == "reversion" else None
    atr = atr_series(bars, ATR_LEN) if BRACKET == "atr" else None
    i = EMA_LEN if MODE == "reversion" else LOOKBACK
    while i < n - 1:
        sig = signal(bars, i, ema)
        if sig is None or (sig == "long" and not TRADE_LONG) \
                or (sig == "short" and not TRADE_SHORT):
            i += 1
            continue

        entry_bar = bars[i + 1]
        slip = SLIPPAGE_PCT if sig == "long" else -SLIPPAGE_PCT
        entry = entry_bar.o * (1 + slip)

        if BRACKET == "atr":
            tp_dist = TP_ATR * atr[i]
            sl_dist = SL_ATR * atr[i]
        else:
            tp_dist = entry * TP_PCT
            sl_dist = entry * SL_PCT
        if sig == "long":
            tp, sl = entry + tp_dist, entry - sl_dist
        else:
            tp, sl = entry - tp_dist, entry + sl_dist

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


def optimize(bars: list[Bar]) -> None:
    """Walk-forward style check: tune on the first half, judge on the second.

    Picks the config with the best TRAIN expectancy among those that take a
    reasonable number of trades, then prints its out-of-sample TEST result.
    A config is only trustworthy if TEST win rate / return hold up vs TRAIN.
    """
    global TP_PCT, SL_PCT, LOOKBACK, MAX_BARS, MODE, EMA_LEN, DEV_PCT
    mid = len(bars) // 2
    train, test = bars[:mid], bars[mid:]
    min_trades = max(5, mid // 40)   # require enough train trades to mean anything

    # (mode, tp%, sl%, p1, p2)  p1/p2 = lookback/maxbars (breakout) or ema/dev% (reversion)
    grid = []
    for tp, sl in [(0.2, 0.15), (0.25, 0.2), (0.3, 0.25), (0.4, 0.3), (0.5, 0.4)]:
        for lb in (15, 20, 30):
            for mb in (20, 40, 60):
                grid.append(("breakout", tp, sl, lb, mb))
    for tp, sl in [(0.15, 0.3), (0.2, 0.4), (0.2, 0.5), (0.25, 0.5), (0.3, 0.6)]:
        for em in (15, 20, 30):
            for dv in (0.1, 0.15, 0.2):
                grid.append(("reversion", tp, sl, em, dv))

    def set_params(combo):
        global TP_PCT, SL_PCT, LOOKBACK, MAX_BARS, MODE, EMA_LEN, DEV_PCT
        m, tp, sl, p1, p2 = combo
        MODE, TP_PCT, SL_PCT = m, tp / 100.0, sl / 100.0
        if m == "breakout":
            LOOKBACK, MAX_BARS = int(p1), int(p2)
        else:
            EMA_LEN, DEV_PCT, MAX_BARS = int(p1), p2 / 100.0, 20

    best = None
    for combo in grid:
        set_params(combo)
        _, s = run(train)
        if s["trades"] < min_trades:
            continue
        score = s["avg_trade_pct"]            # expectancy per trade (net of costs)
        if best is None or score > best[1]:
            best = (combo, score, s)

    if best is None:
        print("No config produced enough trades on the train half.")
        return

    combo, _, s_tr = best
    set_params(combo)
    _, s_te = run(test)

    m, tp, sl, p1, p2 = combo
    p = f"lookback={p1} maxbars={p2}" if m == "breakout" else f"ema={p1} dev={p2}%"
    print("=" * 66)
    print("  OUT-OF-SAMPLE OPTIMIZATION  (train = first half, test = second)")
    print("=" * 66)
    print(f"  Bars             : {len(bars)} (train {len(train)} / test {len(test)})")
    print(f"  Best TRAIN config: mode={m} TP={tp}% SL={sl}% {p}")
    print("-" * 66)
    print(f"  TRAIN  : {s_tr['trades']:>3} trades  "
          f"win {s_tr['win_rate']:>5.1f}%  avg {s_tr['avg_trade_pct']:+.3f}%  "
          f"PF {s_tr['profit_factor']:>4.2f}  ret {s_tr['total_return_pct']:+.2f}%")
    print(f"  TEST   : {s_te['trades']:>3} trades  "
          f"win {s_te['win_rate']:>5.1f}%  avg {s_te['avg_trade_pct']:+.3f}%  "
          f"PF {s_te['profit_factor']:>4.2f}  ret {s_te['total_return_pct']:+.2f}%")
    print("=" * 66)
    verdict = ("HOLDS UP" if s_te["win_rate"] >= 50 and s_te["total_return_pct"] > 0
               else "DOES NOT generalize (likely overfit to train)")
    print(f"  Out-of-sample verdict: {verdict}")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Price-action scalper backtest")
    ap.add_argument("csv", nargs="?", default="scalper/data/btcusdt_1m_sample.csv")
    ap.add_argument("--tp", type=float, help="take-profit %% (e.g. 0.6)")
    ap.add_argument("--sl", type=float, help="stop-loss %% (e.g. 0.4)")
    ap.add_argument("--lookback", type=int, help="breakout lookback (bars)")
    ap.add_argument("--maxbars", type=int, help="time-stop (bars)")
    ap.add_argument("--mode", choices=["breakout", "reversion"], help="strategy mode")
    ap.add_argument("--ema", type=int, help="(reversion) EMA anchor length")
    ap.add_argument("--dev", type=float, help="(reversion) %% stretch from EMA")
    ap.add_argument("--bracket", choices=["pct", "atr"], help="stop/target sizing")
    ap.add_argument("--atr", type=int, help="ATR lookback (atr bracket)")
    ap.add_argument("--tp-atr", type=float, dest="tp_atr", help="TP = N*ATR")
    ap.add_argument("--sl-atr", type=float, dest="sl_atr", help="SL = N*ATR")
    ap.add_argument("--long-only", action="store_true", dest="long_only",
                    help="take long entries only (spot-friendly)")
    ap.add_argument("--short-only", action="store_true", dest="short_only",
                    help="take short entries only")
    ap.add_argument("--optimize", action="store_true",
                    help="grid-search on first half (train), report second half (test)")
    a = ap.parse_args()

    global TP_PCT, SL_PCT, LOOKBACK, MAX_BARS, MODE, EMA_LEN, DEV_PCT
    global BRACKET, ATR_LEN, TP_ATR, SL_ATR, TRADE_LONG, TRADE_SHORT
    if a.long_only:
        TRADE_SHORT = False
    if a.short_only:
        TRADE_LONG = False
    if a.mode is not None:
        MODE = a.mode
    if a.bracket is not None:
        BRACKET = a.bracket
    if a.atr is not None:
        ATR_LEN = a.atr
    if a.tp_atr is not None:
        TP_ATR = a.tp_atr
    if a.sl_atr is not None:
        SL_ATR = a.sl_atr
    if a.tp is not None:
        TP_PCT = a.tp / 100.0
    if a.sl is not None:
        SL_PCT = a.sl / 100.0
    if a.lookback is not None:
        LOOKBACK = a.lookback
    if a.maxbars is not None:
        MAX_BARS = a.maxbars
    if a.ema is not None:
        EMA_LEN = a.ema
    if a.dev is not None:
        DEV_PCT = a.dev / 100.0

    path = a.csv
    bars = load_csv(path)

    if a.optimize:
        optimize(bars)
        return
    trades, s = run(bars)

    print("=" * 64)
    print("  BTCUSD 1m Price-Action Scalper - Backtest Result")
    print("=" * 64)
    print(f"  Data file        : {path}")
    print(f"  Bars             : {s['bars']}  (~{s['span_minutes']:.0f} min "
          f"= {s['span_minutes']/60:.1f} h)")
    if BRACKET == "atr":
        print(f"  Params           : mode={MODE}  bracket=ATR(len={ATR_LEN})  "
              f"TP={TP_ATR}x  SL={SL_ATR}x  maxBars={MAX_BARS}")
    else:
        print(f"  Params           : mode={MODE}  lookback={LOOKBACK}  "
              f"TP={TP_PCT*100:.2f}%  SL={SL_PCT*100:.2f}%  maxBars={MAX_BARS}")
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
