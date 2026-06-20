// backtester.mjs — score strategies on REAL price history, keep only winners.
//
// It replays recorded real candles bar by bar (no look-ahead), runs each
// strategy through the SAME PaperExchange used live (same fees, slippage,
// stops), and reports honest metrics. A strategy is approved only if it clears
// every gate in CONFIG.backtest. If nothing passes, it says so plainly — that
// is the honest, expected outcome for naive ideas after real costs.
import CONFIG from "./config.mjs";
import { PaperExchange } from "./paperExchange.mjs";
import { STRATEGIES } from "./strategies/index.mjs";
import { loadRecorded } from "./dataSource.mjs";
import { stdev } from "./indicators.mjs";
import { writeJson, usd, pct, DATA_DIR } from "./util.mjs";
import { join } from "node:path";

function maxDrawdown(equityCurve) {
  let peak = -Infinity, maxDd = 0;
  for (const e of equityCurve) {
    if (e > peak) peak = e;
    const dd = (peak - e) / peak;
    if (dd > maxDd) maxDd = dd;
  }
  return maxDd;
}

// Sharpe from per-trade returns (not annualized; a relative quality signal).
function sharpe(returns) {
  if (returns.length < 2) return 0;
  const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
  const sd = stdev(returns);
  return sd === 0 ? 0 : mean / sd;
}

export function backtestStrategy(strategy, candles) {
  const ex = new PaperExchange(CONFIG.startingBalanceUsd);
  const inst = "BACKTEST";
  const equityCurve = [ex.startingBalance];

  for (let i = 0; i < candles.length; i++) {
    const bar = candles[i];
    const history = candles.slice(0, i + 1);
    const price = bar.c;

    // Honest exits first (stop / target on the real close).
    ex.checkExits(inst, price, bar.t);

    const inPos = ex.hasPosition(inst);
    const sig = strategy.signal(history, inPos);

    if (sig === "enter" && !inPos) {
      // Fixed, simple sizing for evaluation: a flat slice of equity. (Live uses
      // Kelly; here we want to judge the SIGNAL, not the sizing.)
      const notional = ex.equity({ [inst]: price }) * 0.5;
      ex.openLong(inst, price, notional, {
        t: bar.t,
        strategy: strategy.name,
        stop: price * (1 - CONFIG.stopLossPct),
        target: price * (1 + CONFIG.takeProfitPct),
      });
    } else if (sig === "exit" && inPos) {
      ex.closeLong(inst, price, "signal", { t: bar.t });
    }
    equityCurve.push(ex.equity({ [inst]: price }));
  }
  // Close anything still open at the last real price — truthfully.
  const last = candles[candles.length - 1];
  if (ex.hasPosition(inst)) ex.closeLong(inst, last.c, "end-of-data", { t: last.t });

  const s = ex.stats();
  const finalEquity = ex.equity({});
  const returns = ex.closedTrades.map((t) => t.returnPct);
  return {
    ...s,
    finalEquity,
    totalReturn: (finalEquity - ex.startingBalance) / ex.startingBalance,
    maxDrawdown: maxDrawdown(equityCurve),
    sharpe: sharpe(returns),
    expectancyUsd: s.trades ? s.realizedPnl / s.trades : 0,
  };
}

function passes(r) {
  const g = CONFIG.backtest;
  return (
    r.trades >= g.minTrades &&
    r.profitFactor >= g.minProfitFactor &&
    r.expectancyUsd > g.minExpectancyUsd &&
    r.maxDrawdown <= g.maxDrawdownFraction
  );
}

export function runBacktests() {
  const approved = [];
  const report = [];
  console.log("\n=== Backtest on REAL recorded prices ===");
  console.log(`Costs applied: taker ${pct(CONFIG.takerFeeRate)}/side, slippage ${pct(CONFIG.slippageRate)}/fill.\n`);

  for (const inst of CONFIG.symbols) {
    const candles = loadRecorded(inst, "days");
    if (!candles) {
      console.log(`! No recorded data for ${inst}. Run "npm run record" (or it was shipped under data/recorded/).`);
      continue;
    }
    console.log(`--- ${inst}  (${candles.length} daily candles) ---`);
    for (const strat of Object.values(STRATEGIES)) {
      const r = backtestStrategy(strat, candles);
      const ok = passes(r);
      report.push({ instrument: inst, strategy: strat.name, ...r, approved: ok });
      console.log(
        `  ${strat.name.padEnd(18)} trades=${String(r.trades).padStart(3)} ` +
        `win=${pct(r.winRate).padStart(7)} PF=${(r.profitFactor === Infinity ? "inf" : r.profitFactor.toFixed(2)).padStart(5)} ` +
        `ret=${pct(r.totalReturn).padStart(8)} maxDD=${pct(r.maxDrawdown).padStart(7)} ` +
        `exp/trade=${usd(r.expectancyUsd).padStart(10)}  ${ok ? "✅ PASS" : "❌ reject"}`
      );
      if (ok) approved.push({ instrument: inst, strategy: strat.name });
    }
    console.log("");
  }

  writeJson(join(DATA_DIR, "approved_strategies.json"), {
    generatedAt: new Date().toISOString(),
    gates: CONFIG.backtest,
    approved,
    report,
  });

  if (approved.length === 0) {
    console.log("Honest result: NO strategy passed after real costs.");
    console.log("That is the lesson, not a bug — simple signals rarely beat fees + slippage.");
    console.log("The live engine will trade nothing until something earns its place.\n");
  } else {
    console.log(`Approved ${approved.length} strategy/instrument pair(s):`);
    approved.forEach((a) => console.log(`  • ${a.strategy} on ${a.instrument}`));
    console.log("");
  }
  return { approved, report };
}

// Run directly: `npm run backtest`
if (import.meta.url === `file://${process.argv[1]}`) {
  runBacktests();
}
