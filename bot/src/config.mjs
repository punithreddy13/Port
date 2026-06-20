// config.mjs
// Central settings for the honest paper-trading bot.
//
// THE ONE RULE: never fake a fill, a price, or a profit.
// Everything below is a real, documented assumption. Where a number depends on
// your real venue (fees, funding), it is marked "VERIFY" — confirm it against
// your exchange's official fee page before trusting any result.

export const CONFIG = {
  // --- Market & data source -------------------------------------------------
  // The bot trades these instruments with FAKE money against REAL prices.
  market: "coinbase",
  symbols: ["BTC-USD", "ETH-USD"],

  // Live price source: CoinDesk Data API (free, no key required for basic use).
  // Docs: https://developer.coindesk.com/  (Spot tick / OHLCV endpoints).
  // The replay source reads recorded REAL candles from data/recorded/*.json so
  // the engine and backtester can run offline on genuine historical prices.
  dataApiBase: "https://data-api.coindesk.com",

  // --- Honest cost model ----------------------------------------------------
  // Taker fee charged on BOTH entry and exit notional. This is the single
  // biggest reason naive strategies lose: you pay it every round trip.
  // Default 0.6% reflects Coinbase Advanced Trade's highest retail taker tier.
  // VERIFY against your account's real tier: https://www.coinbase.com/advanced-fees
  takerFeeRate: 0.006,

  // Slippage: the gap between the price you see and the price you actually get.
  // Applied ADVERSELY to every fill (you buy a little higher, sell a little
  // lower). 5 bps = 0.05% is a conservative liquid-market estimate; thin
  // markets are worse. This is an estimate, not a measured fill.
  slippageRate: 0.0005,

  // Funding: applies to PERPETUAL FUTURES, not spot. We trade spot here, so
  // funding is 0 by honest default. If you point this at perps, set a real
  // funding rate (typical baseline ~0.01% per 8h) and the holding interval.
  // We do NOT invent a spot funding charge.
  fundingRatePer8h: 0.0,

  // --- Account -------------------------------------------------------------
  startingBalanceUsd: 10000,

  // --- Risk / sizing -------------------------------------------------------
  // Fraction of full Kelly to use. Full Kelly is too aggressive and blows up;
  // 0.5 (half-Kelly) is the common, more survivable choice.
  kellyFraction: 0.5,
  // Hard cap on fraction of equity risked per position, regardless of Kelly.
  maxPositionFraction: 0.2,
  // Per-trade stop loss / take profit (as fraction of entry price).
  stopLossPct: 0.03,
  takeProfitPct: 0.06,

  // --- Reset-and-learn ------------------------------------------------------
  // If equity falls to this fraction of the STARTING balance, the bot admits
  // the run blew up, closes everything truthfully, banks the lesson, resets to
  // the starting balance, and tries the next generation a bit more cautiously.
  blowupFloorFraction: 0.5,

  // --- Engine loop ----------------------------------------------------------
  // Seconds between live cycles. Keep it slow; this is paper, not HFT.
  loopSeconds: 15,

  // --- Backtest "pass" gates ------------------------------------------------
  // A strategy is only KEPT if it clears every one of these on real history.
  // These are deliberately strict so weak ideas get thrown out honestly.
  backtest: {
    minTrades: 8,
    minProfitFactor: 1.1,
    minExpectancyUsd: 0,
    maxDrawdownFraction: 0.35,
  },
};

export default CONFIG;
