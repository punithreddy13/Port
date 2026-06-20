// rsiReversion.mjs — mean reversion. Buy fear, sell strength.
// Enter long when RSI dips below `oversold`; exit when it recovers above `exit`.
// Reverts nicely in ranges; gets run over in strong downtrends (the honest risk).
import { rsi } from "../indicators.mjs";

export const rsiReversion = {
  name: "rsiReversion",
  period: 14,
  oversold: 30,
  exitLevel: 55,
  signal(candles, inPosition) {
    if (candles.length < this.period + 2) return "hold";
    const closes = candles.map((c) => c.c);
    const r = rsi(closes, this.period);
    const i = closes.length - 1;
    if (r[i] == null) return "hold";
    if (!inPosition && r[i] < this.oversold) return "enter";
    if (inPosition && r[i] > this.exitLevel) return "exit";
    return "hold";
  },
};
