// engine.mjs — the live loop. Pulls real prices, runs approved strategies,
// sizes with fractional Kelly, closes truthfully, and survives blow-ups via the
// reset-and-learn loop. Writes data/state.json continuously for the dashboard.
//
// Modes:
//   node src/engine.mjs            -> LIVE (polls CoinDesk every loopSeconds)
//   node src/engine.mjs --replay   -> replay recorded REAL candles (offline)
//   node src/engine.mjs --replay --max 80   -> stop after N replay cycles
import CONFIG from "./config.mjs";
import { PaperExchange } from "./paperExchange.mjs";
import { RiskManager } from "./riskManager.mjs";
import { STRATEGIES } from "./strategies/index.mjs";
import { sizePosition } from "./kelly.mjs";
import { liveSource, makeReplaySource } from "./dataSource.mjs";
import { readJson, writeJson, appendLine, DATA_DIR, usd, pct, sleep } from "./util.mjs";
import { join } from "node:path";

const STATE_PATH = join(DATA_DIR, "state.json");
const TRADES_LOG = join(DATA_DIR, "trades.log");

// Which strategies earned the right to trade each instrument (from backtest).
function loadApproved() {
  const a = readJson(join(DATA_DIR, "approved_strategies.json"), null);
  if (!a || !a.approved?.length) return {};
  const map = {};
  for (const { instrument, strategy } of a.approved) {
    (map[instrument] ||= []).push(strategy);
  }
  return map;
}

export class Engine {
  constructor({ replay = false, demo = false } = {}) {
    this.replay = replay;
    this.demo = demo;
    this.exchange = new PaperExchange(CONFIG.startingBalanceUsd);
    this.risk = new RiskManager();
    // DEMO ONLY: trade every strategy on every symbol so the full machinery is
    // visible. This is NOT an endorsement — these signals were REJECTED by the
    // backtest. Prices and P&L are still 100% real.
    this.approved = demo
      ? Object.fromEntries(CONFIG.symbols.map((s) => [s, Object.keys(STRATEGIES)]))
      : loadApproved();
    this.histories = Object.fromEntries(CONFIG.symbols.map((s) => [s, []]));
    this.cycle = 0;
    this.startedAt = new Date().toISOString();
    this.lastEvent = "started";
  }

  approvedFor(inst) {
    return this.approved[inst] || [];
  }

  tradesByStrategy(name) {
    return this.exchange.closedTrades.filter((t) => t.strategy === name);
  }

  // One honest cycle for one instrument at a known REAL price.
  step(inst, price, t, candle) {
    if (candle) this.histories[inst].push(candle);
    const history = this.histories[inst];

    // 1) Funding accrual (0 for spot) + honest stop/target exits.
    this.exchange.accrueFunding(inst, price, this.replay ? 24 : CONFIG.loopSeconds / 3600);
    const exit = this.exchange.checkExits(inst, price, t);
    if (exit?.ok) this.logTrade(exit.trade);

    // 2) Only strategies that passed the backtest may act.
    for (const name of this.approvedFor(inst)) {
      const strat = STRATEGIES[name];
      if (!strat) continue;
      const inPos = this.exchange.hasPosition(inst);
      const sig = strat.signal(history, inPos);

      if (sig === "enter" && !inPos) {
        const equity = this.exchange.equity(this.markPrices());
        const sizing = sizePosition(equity, this.tradesByStrategy(name), {
          kellyFraction: CONFIG.kellyFraction * this.risk.riskMultiplier(),
        });
        if (sizing.notionalUsd > 1) {
          const r = this.exchange.openLong(inst, price, sizing.notionalUsd, {
            t, strategy: name,
            stop: price * (1 - CONFIG.stopLossPct),
            target: price * (1 + CONFIG.takeProfitPct),
          });
          if (r.ok) {
            this.lastEvent = `OPEN ${name} ${inst} @ ${usd(r.fillPrice)} (${usd(sizing.notionalUsd)}, kelly=${sizing.kelly ?? "n/a"})`;
          }
        }
      } else if (sig === "exit" && inPos) {
        const r = this.exchange.closeLong(inst, price, "signal", { t });
        if (r.ok) { this.logTrade(r.trade); this.lastEvent = `CLOSE ${name} ${inst} @ ${usd(r.trade.exitPrice)} pnl=${usd(r.trade.realizedPnl)}`; }
      }
    }
  }

  markPrices() {
    const p = {};
    for (const inst of CONFIG.symbols) {
      const h = this.histories[inst];
      if (h.length) p[inst] = h[h.length - 1].c;
    }
    return p;
  }

  logTrade(trade) {
    appendLine(TRADES_LOG, JSON.stringify(trade));
    this.lastEvent = `CLOSE ${trade.strategy} ${trade.instrument} @ ${usd(trade.exitPrice)} pnl=${usd(trade.realizedPnl)} (${trade.reason})`;
  }

  // After each cycle: detect blow-up, run the reset-and-learn loop if needed,
  // and persist state for the dashboard.
  settle(prices) {
    const equity = this.exchange.equity(prices);
    if (this.risk.isBlownUp(equity)) {
      // Close everything truthfully at real prices.
      for (const inst of Object.keys(this.exchange.positions)) {
        const r = this.exchange.closeLong(inst, prices[inst] ?? this.exchange.positions[inst].entryPrice, "blowup-reset", {});
        if (r.ok) this.logTrade(r.trade);
      }
      const lesson = this.risk.recordBlowup(this.exchange, prices);
      this.lastEvent = `💥 BLOW-UP gen ${lesson.generation - 1}: ${lesson.note} Resetting to ${usd(this.exchange.startingBalance)}.`;
      this.exchange.reset(CONFIG.startingBalanceUsd);
    }
    this.persist(prices);
  }

  persist(prices) {
    const s = this.exchange.stats();
    writeJson(STATE_PATH, {
      updatedAt: new Date().toISOString(),
      mode: (this.replay ? "replay (recorded real prices)" : "live") + (this.demo ? " · DEMO (rejected strategies, for show only)" : ""),
      startedAt: this.startedAt,
      cycle: this.cycle,
      generation: this.risk.generation,
      cautionFactor: this.risk.riskMultiplier(),
      startingBalance: this.exchange.startingBalance,
      cash: this.exchange.cash,
      equity: this.exchange.equity(prices),
      unrealizedPnl: this.exchange.unrealizedPnl(prices),
      prices,
      approved: this.approved,
      positions: Object.values(this.exchange.positions).map((p) => ({
        ...p, mark: prices[p.instrument] ?? p.entryPrice,
        uPnl: ((prices[p.instrument] ?? p.entryPrice) - p.entryPrice) * p.qty,
      })),
      stats: s,
      lessons: this.risk.lessons,
      recentTrades: this.exchange.closedTrades.slice(-15).reverse(),
      lastEvent: this.lastEvent,
    });
  }

  printStatus(prices) {
    const eq = this.exchange.equity(prices);
    const ret = (eq - this.exchange.startingBalance) / this.exchange.startingBalance;
    console.log(
      `cycle ${String(this.cycle).padStart(4)} | gen ${this.risk.generation} | equity ${usd(eq).padStart(12)} ` +
      `(${pct(ret).padStart(8)}) | trades ${this.exchange.stats().trades} | ${this.lastEvent}`
    );
  }
}

// ---- Replay runner (offline, recorded real candles) -----------------------
async function runReplay(maxCycles, demo = false) {
  const engine = new Engine({ replay: true, demo });
  const sources = Object.fromEntries(
    CONFIG.symbols.map((s) => [s, makeReplaySource(s, "days")])
  );
  const len = Math.min(...Object.values(sources).map((s) => s.total));
  const limit = maxCycles ? Math.min(maxCycles, len) : len;

  console.log(`\n=== REPLAY: ${limit} daily cycles on recorded real prices ===`);
  if (Object.values(engine.approved).flat().length === 0) {
    console.log("No approved strategies (run `npm run backtest` first) — the bot will correctly trade nothing.\n");
  }
  for (let i = 0; i < limit; i++) {
    engine.cycle = i + 1;
    const prices = {};
    for (const inst of CONFIG.symbols) {
      const bar = sources[inst].next();
      prices[inst] = bar.c;
      engine.step(inst, bar.c, bar.t, bar);
    }
    engine.settle(prices);
    if (i % 10 === 0 || i === limit - 1) engine.printStatus(prices);
  }
  console.log(`\nDone. Final state written to data/state.json. Run "npm run serve" to watch it.\n`);
}

// ---- Live runner ----------------------------------------------------------
async function runLive() {
  const engine = new Engine({ replay: false });
  console.log(`\n=== LIVE paper trading (real prices, fake money) ===`);
  console.log(`Polling ${CONFIG.market} every ${CONFIG.loopSeconds}s. Ctrl-C to stop.\n`);
  if (Object.values(engine.approved).flat().length === 0) {
    console.log("No approved strategies yet — run `npm run backtest`. The bot stays flat until something passes.\n");
  }
  // Warm up histories with real recent candles so indicators have context.
  for (const inst of CONFIG.symbols) {
    try { engine.histories[inst] = await liveSource.candles(inst, "days", 60); }
    catch (e) { console.log(`! Could not warm up ${inst}: ${e.message}`); }
  }
  // eslint-disable-next-line no-constant-condition
  while (true) {
    engine.cycle += 1;
    const prices = {};
    for (const inst of CONFIG.symbols) {
      try {
        const tick = await liveSource.tick(inst);
        prices[inst] = tick.price;
        // Update the latest candle's close with the live price (same bar).
        const h = engine.histories[inst];
        if (h.length) h[h.length - 1] = { ...h[h.length - 1], c: tick.price };
        engine.step(inst, tick.price, tick.t, null);
      } catch (e) {
        engine.lastEvent = `! price error ${inst}: ${e.message} (skipping; never faking a price)`;
      }
    }
    engine.settle(prices);
    engine.printStatus(prices);
    await sleep(CONFIG.loopSeconds * 1000);
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const args = process.argv.slice(2);
  const replay = args.includes("--replay");
  const demo = args.includes("--demo");
  const maxIdx = args.indexOf("--max");
  const maxCycles = maxIdx >= 0 ? Number(args[maxIdx + 1]) : null;
  (replay ? runReplay(maxCycles, demo) : runLive()).catch((e) => {
    console.error("Engine stopped:", e.message);
    process.exit(1);
  });
}

export default Engine;
