// riskManager.mjs — the reset-and-learn loop.
//
// When a run blows up (equity falls to CONFIG.blowupFloorFraction of the
// starting balance), we do NOT pretend it didn't happen. We close everything at
// real prices, write down exactly what the generation did and how it died, then
// reset to the starting balance and start a new, more cautious generation.
import CONFIG from "./config.mjs";
import { readJson, writeJson, DATA_DIR } from "./util.mjs";
import { join } from "node:path";

const LESSONS_PATH = join(DATA_DIR, "lessons.json");

export class RiskManager {
  constructor() {
    const saved = readJson(LESSONS_PATH, null);
    this.generation = saved?.generation ?? 1;
    this.lessons = saved?.lessons ?? [];
    // Each generation shrinks risk slightly: survival over hero trades.
    this.cautionFactor = saved?.cautionFactor ?? 1.0;
  }

  floor() {
    return CONFIG.startingBalanceUsd * CONFIG.blowupFloorFraction;
  }

  isBlownUp(equity) {
    return equity <= this.floor();
  }

  // Returns the Kelly multiplier to apply this generation (gets more cautious
  // after each blow-up).
  riskMultiplier() {
    return this.cautionFactor;
  }

  recordBlowup(exchange, prices) {
    const stats = exchange.stats();
    const lesson = {
      generation: this.generation,
      diedAt: new Date().toISOString(),
      endEquity: exchange.equity(prices),
      startingBalance: exchange.startingBalance,
      trades: stats.trades,
      winRate: stats.winRate,
      realizedPnl: stats.realizedPnl,
      feesPaid: stats.feesPaid,
      note:
        stats.feesPaid > Math.abs(stats.realizedPnl) * 0.5
          ? "Fees were a large share of losses — overtrading. Trade less."
          : "Strategy edge did not survive real prices. Tighten gates.",
    };
    this.lessons.push(lesson);
    this.generation += 1;
    this.cautionFactor = Math.max(0.25, this.cautionFactor * 0.75);
    writeJson(LESSONS_PATH, {
      generation: this.generation,
      cautionFactor: this.cautionFactor,
      lessons: this.lessons,
    });
    return lesson;
  }
}

export default RiskManager;
