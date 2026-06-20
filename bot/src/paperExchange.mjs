// paperExchange.mjs — the honest engine.
//
// Fake money, REAL prices, and the real costs of trading applied every time:
//   - taker fee on entry AND exit notional
//   - slippage moved against you on every fill
//   - funding accrual while a position is held (0 for spot, by honest default)
// Every close uses the real price passed in. Losses are never rounded away.
//
// Spot is long-only: you cannot truthfully short coins you do not own, so we do
// not pretend to. (Point it at perps and add shorts only with a real funding
// model.)
import CONFIG from "./config.mjs";
import { round } from "./util.mjs";

export class PaperExchange {
  constructor(startingBalanceUsd = CONFIG.startingBalanceUsd, opts = {}) {
    this.takerFeeRate = opts.takerFeeRate ?? CONFIG.takerFeeRate;
    this.slippageRate = opts.slippageRate ?? CONFIG.slippageRate;
    this.fundingRatePer8h = opts.fundingRatePer8h ?? CONFIG.fundingRatePer8h;
    this.reset(startingBalanceUsd);
  }

  reset(startingBalanceUsd) {
    this.startingBalance = startingBalanceUsd;
    this.cash = startingBalanceUsd; // free USD
    this.positions = {};            // instrument -> position
    this.closedTrades = [];         // full truthful history
    this.feesPaid = 0;
    this.fundingPaid = 0;
  }

  // Apply adverse slippage: buys fill higher, sells fill lower.
  _fill(price, side) {
    const s = this.slippageRate;
    return side === "buy" ? price * (1 + s) : price * (1 - s);
  }

  // Mark-to-market equity = cash + value of open positions at current prices.
  equity(prices) {
    let v = this.cash;
    for (const [inst, p] of Object.entries(this.positions)) {
      const mark = prices[inst] ?? p.entryPrice;
      v += p.qty * mark;
    }
    return round(v, 2);
  }

  unrealizedPnl(prices) {
    let pnl = 0;
    for (const [inst, p] of Object.entries(this.positions)) {
      const mark = prices[inst] ?? p.entryPrice;
      pnl += (mark - p.entryPrice) * p.qty;
    }
    return round(pnl, 2);
  }

  hasPosition(instrument) {
    return Boolean(this.positions[instrument]);
  }

  // Open a long. notionalUsd is how much (fake) USD to deploy before fees.
  openLong(instrument, price, notionalUsd, meta = {}) {
    if (this.positions[instrument]) return { ok: false, reason: "already in position" };
    if (notionalUsd <= 0) return { ok: false, reason: "non-positive size" };
    const fillPrice = this._fill(price, "buy");
    const fee = notionalUsd * this.takerFeeRate;
    const total = notionalUsd + fee;
    if (total > this.cash) return { ok: false, reason: "insufficient cash" };
    const qty = notionalUsd / fillPrice;
    this.cash = round(this.cash - total, 2);
    this.feesPaid = round(this.feesPaid + fee, 2);
    this.positions[instrument] = {
      instrument,
      qty,
      entryPrice: fillPrice,
      notionalAtEntry: notionalUsd,
      entryFee: fee,
      fundingAccrued: 0,
      openedAt: meta.t ?? Math.floor(Date.now() / 1000),
      strategy: meta.strategy ?? "manual",
      stop: meta.stop ?? null,
      target: meta.target ?? null,
    };
    return { ok: true, fillPrice, qty, fee };
  }

  // Accrue funding for a held position over `hours` (0 for spot).
  accrueFunding(instrument, price, hours) {
    const p = this.positions[instrument];
    if (!p || this.fundingRatePer8h === 0 || hours <= 0) return 0;
    const notional = p.qty * price;
    const funding = notional * this.fundingRatePer8h * (hours / 8);
    p.fundingAccrued = round(p.fundingAccrued + funding, 6);
    this.cash = round(this.cash - funding, 2); // long pays funding when rate > 0
    this.fundingPaid = round(this.fundingPaid + funding, 2);
    return funding;
  }

  // Close a long at the REAL price. The loss/gain is whatever it truly is.
  closeLong(instrument, price, reason = "signal", meta = {}) {
    const p = this.positions[instrument];
    if (!p) return { ok: false, reason: "no position" };
    const fillPrice = this._fill(price, "sell");
    const proceeds = p.qty * fillPrice;
    const exitFee = proceeds * this.takerFeeRate;
    const net = proceeds - exitFee;
    this.cash = round(this.cash + net, 2);
    this.feesPaid = round(this.feesPaid + exitFee, 2);

    // Truthful realized PnL: what we got back minus what we put in, minus ALL
    // costs (entry fee, exit fee, funding). No rounding a loss toward zero.
    const grossPnl = (fillPrice - p.entryPrice) * p.qty;
    const realizedPnl = round(grossPnl - p.entryFee - exitFee - p.fundingAccrued, 2);

    const trade = {
      instrument,
      strategy: p.strategy,
      qty: p.qty,
      entryPrice: p.entryPrice,
      exitPrice: fillPrice,
      entryFee: p.entryFee,
      exitFee: round(exitFee, 2),
      funding: p.fundingAccrued,
      realizedPnl,
      returnPct: round((fillPrice - p.entryPrice) / p.entryPrice, 6),
      reason,
      openedAt: p.openedAt,
      closedAt: meta.t ?? Math.floor(Date.now() / 1000),
      win: realizedPnl > 0,
    };
    this.closedTrades.push(trade);
    delete this.positions[instrument];
    return { ok: true, trade };
  }

  // Check stop-loss / take-profit against a real price; close truthfully if hit.
  checkExits(instrument, price, t) {
    const p = this.positions[instrument];
    if (!p) return null;
    if (p.stop != null && price <= p.stop) return this.closeLong(instrument, price, "stop", { t });
    if (p.target != null && price >= p.target) return this.closeLong(instrument, price, "target", { t });
    return null;
  }

  stats() {
    const trades = this.closedTrades;
    const wins = trades.filter((t) => t.win);
    const losses = trades.filter((t) => !t.win);
    const grossWin = wins.reduce((a, t) => a + t.realizedPnl, 0);
    const grossLoss = Math.abs(losses.reduce((a, t) => a + t.realizedPnl, 0));
    return {
      trades: trades.length,
      wins: wins.length,
      losses: losses.length,
      winRate: trades.length ? wins.length / trades.length : 0,
      grossWin: round(grossWin, 2),
      grossLoss: round(grossLoss, 2),
      profitFactor: grossLoss > 0 ? round(grossWin / grossLoss, 3) : (grossWin > 0 ? Infinity : 0),
      realizedPnl: round(trades.reduce((a, t) => a + t.realizedPnl, 0), 2),
      feesPaid: this.feesPaid,
      fundingPaid: this.fundingPaid,
    };
  }
}

export default PaperExchange;
