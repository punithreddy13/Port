# HTF Reversal + 1m Divergence

Pine Script v5 indicator for TradingView.

## What it does

- Detects a higher-timeframe (default `15m`) **reversal candle pattern** on the
  most recently closed HTF bar: Hammer, Inverted Hammer, Shooting Star,
  Hanging Man, Bullish/Bearish Engulfing, or Doji.
- On the 1-minute chart, watches for **regular RSI divergence** (bullish: price
  LL / RSI HL — bearish: price HH / RSI LH).
- Plots a `Bull Div` / `Bear Div` marker only when the divergence's pivot bar
  falls **inside the time window of the most recent HTF reversal candle** and
  the divergence direction agrees with the pattern direction.
- Renders a mini-replica of that HTF candle to the **right of price (RPL)**
  with horizontal guide lines for **High**, **Low**, and **Open**.

## Install

1. Open TradingView, switch the chart to the **1-minute** timeframe.
2. Pine Editor → New indicator → paste `htf_reversal_1m_divergence.pine` →
   Save → **Add to chart**.
3. Adjust the *Higher Timeframe* input (default `15`) and pattern toggles in
   the indicator settings.

## Inputs of note

| Input | Default | Notes |
| --- | --- | --- |
| Higher Timeframe | `15` | The HTF used for pattern detection. |
| Wick / Body ratio | `2.0` | Hammer / Star wick must be ≥ this × body. |
| Doji body / range | `0.1` | Body ≤ 10% of range counts as a Doji. |
| Pivot left / right | `5 / 5` | RSI pivot lookback for divergence. |
| Min / Max bars between pivots | `5 / 60` | Filters near and far divergences. |
| HTF candle: bars to the right | `25` | How far right of the latest bar to draw. |
| Guide line look-back | `60` | Length of the H/L/O guide lines. |

## Alerts

Two alert conditions are pre-wired:

- `1m Bull Div in HTF Reversal`
- `1m Bear Div in HTF Reversal`
