// smaCross.mjs — classic trend-following moving-average crossover.
// Enter long when the fast SMA crosses ABOVE the slow SMA; exit when it crosses
// back below. Trend ideas win in trends and bleed fees in chop.
import { sma } from "../indicators.mjs";

export const smaCross = {
  name: "smaCross",
  fast: 10,
  slow: 30,
  // candles: array of {t,o,h,l,c,v} up to and INCLUDING the current bar.
  signal(candles, inPosition) {
    if (candles.length < this.slow + 2) return "hold";
    const closes = candles.map((c) => c.c);
    const f = sma(closes, this.fast);
    const s = sma(closes, this.slow);
    const i = closes.length - 1;
    if (f[i] == null || s[i] == null || f[i - 1] == null || s[i - 1] == null) return "hold";
    const crossedUp = f[i - 1] <= s[i - 1] && f[i] > s[i];
    const crossedDown = f[i - 1] >= s[i - 1] && f[i] < s[i];
    if (!inPosition && crossedUp) return "enter";
    if (inPosition && crossedDown) return "exit";
    return "hold";
  },
};
