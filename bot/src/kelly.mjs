// kelly.mjs — fractional Kelly position sizing.
//
// Kelly fraction for a bet with win prob W and payoff ratio R (avg win / avg
// loss): f* = W - (1 - W) / R.
// Reference: J. L. Kelly Jr., "A New Interpretation of Information Rate" (1956).
//
// Full Kelly is famously violent — one bad streak and you're wiped. So we scale
// it down (half-Kelly by default) and cap it. With too few trades to estimate
// the edge, we size tiny instead of guessing big.
import CONFIG from "./config.mjs";

export function kellyFraction(winRate, payoffRatio) {
  if (payoffRatio <= 0) return 0;
  const f = winRate - (1 - winRate) / payoffRatio;
  return f; // may be negative -> no edge -> don't bet
}

// Decide how much (fake) USD to deploy on the next trade for a strategy, given
// its own track record so far.
export function sizePosition(equity, strategyTrades, opts = {}) {
  const frac = opts.kellyFraction ?? CONFIG.kellyFraction;
  const cap = opts.maxPositionFraction ?? CONFIG.maxPositionFraction;

  const wins = strategyTrades.filter((t) => t.win);
  const losses = strategyTrades.filter((t) => !t.win);

  // Not enough evidence yet: bet small and honest (a quarter of the cap).
  if (wins.length < 3 || losses.length < 3) {
    return { notionalUsd: equity * cap * 0.25, kelly: null, reason: "insufficient history" };
  }

  const winRate = wins.length / strategyTrades.length;
  const avgWin = wins.reduce((a, t) => a + t.realizedPnl, 0) / wins.length;
  const avgLoss = Math.abs(losses.reduce((a, t) => a + t.realizedPnl, 0) / losses.length);
  const payoff = avgLoss > 0 ? avgWin / avgLoss : 0;

  const kelly = kellyFraction(winRate, payoff);
  if (kelly <= 0) {
    return { notionalUsd: 0, kelly, reason: "no edge (Kelly <= 0)" };
  }
  const sized = Math.min(kelly * frac, cap);
  return { notionalUsd: equity * sized, kelly, fractionUsed: sized, reason: "kelly" };
}

export default sizePosition;
