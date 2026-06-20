// donchianBreakout.mjs — breakout trend following (Turtle-style).
// Enter long when price closes above the highest high of the last `entry` bars;
// exit when it closes below the lowest low of the last `exit` bars.
import { rollingExtremes } from "../indicators.mjs";

export const donchianBreakout = {
  name: "donchianBreakout",
  entry: 20,
  exit: 10,
  signal(candles, inPosition) {
    const need = Math.max(this.entry, this.exit) + 2;
    if (candles.length < need) return "hold";
    const highs = candles.map((c) => c.h);
    const lows = candles.map((c) => c.l);
    const close = candles[candles.length - 1].c;
    const i = candles.length - 1;
    const { hi } = rollingExtremes(highs, lows, this.entry);
    const { lo } = rollingExtremes(highs, lows, this.exit);
    if (!inPosition && hi[i] != null && close > hi[i]) return "enter";
    if (inPosition && lo[i] != null && close < lo[i]) return "exit";
    return "hold";
  },
};
