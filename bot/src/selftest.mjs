// selftest.mjs — proves the honest mechanics with concrete numbers.
// Not a strategy test; a "does the engine lie?" test. Run: npm test
import { PaperExchange } from "./paperExchange.mjs";
import { kellyFraction } from "./kelly.mjs";

let failed = 0;
const approx = (a, b, eps = 1e-6) => Math.abs(a - b) < eps;
function check(name, cond, detail = "") {
  console.log(`${cond ? "✅" : "❌"} ${name}${detail ? "  — " + detail : ""}`);
  if (!cond) failed++;
}

// 1) A flat round trip at the SAME price must LOSE money (fees + slippage).
//    An honest engine can never show a free or fake profit here.
{
  const ex = new PaperExchange(10000, { takerFeeRate: 0.006, slippageRate: 0.0005, fundingRatePer8h: 0 });
  ex.openLong("X", 100, 1000, { t: 1 });
  const { trade } = ex.closeLong("X", 100, "test", { t: 2 });
  check("round trip at flat price loses money", trade.realizedPnl < 0, `pnl=${trade.realizedPnl}`);
  check("loss includes both fees", trade.entryFee > 0 && trade.exitFee > 0);
}

// 2) A real loss is reported in full, not rounded toward zero.
{
  const ex = new PaperExchange(10000, { takerFeeRate: 0, slippageRate: 0, fundingRatePer8h: 0 });
  ex.openLong("X", 100, 1000, { t: 1 });      // buy 10 units @ 100
  const { trade } = ex.closeLong("X", 90, "test", { t: 2 }); // sell @ 90 -> -100
  check("10% drop on $1000 = -$100 exactly", approx(trade.realizedPnl, -100), `pnl=${trade.realizedPnl}`);
}

// 3) Cash conservation: equity after a flat round trip == cash, and equals
//    starting minus the fees actually paid.
{
  const ex = new PaperExchange(10000, { takerFeeRate: 0.006, slippageRate: 0, fundingRatePer8h: 0 });
  ex.openLong("X", 100, 2000, { t: 1 });
  ex.closeLong("X", 100, "test", { t: 2 });
  check("equity == cash when flat", approx(ex.equity({}), ex.cash));
  check("equity dropped by exactly the fees", approx(ex.equity({}), 10000 - ex.feesPaid), `equity=${ex.equity({})} fees=${ex.feesPaid}`);
}

// 4) Funding is zero for spot (rate 0) — we never invent a charge.
{
  const ex = new PaperExchange(10000, { takerFeeRate: 0, slippageRate: 0, fundingRatePer8h: 0 });
  ex.openLong("X", 100, 1000, { t: 1 });
  const f = ex.accrueFunding("X", 100, 24);
  check("spot funding is 0", f === 0);
}

// 5) Kelly with no edge returns <= 0 (don't bet).
{
  check("Kelly <= 0 when win rate 40% / payoff 1", kellyFraction(0.4, 1) <= 0, `f=${kellyFraction(0.4,1)}`);
  check("Kelly > 0 with a real edge (60% / payoff 1.5)", kellyFraction(0.6, 1.5) > 0);
}

console.log(failed === 0 ? "\nAll honesty checks passed.\n" : `\n${failed} check(s) FAILED.\n`);
process.exit(failed === 0 ? 0 : 1);
