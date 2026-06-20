# 🤖 Honest Paper Trading Bot

Real live crypto prices, **fake money**, real fees/funding/slippage, and a
truthful close on every trade. Built from the *Simulation Trading Bot* guide by
**@fabrichhhhhh**, whose one rule beats every other rule:

> **Never fake a fill, a price, or a profit.**

This is a **simulation**, not a money machine. It has no secret edge. It will
lose trades and it can blow up the whole balance — and it is built to show you
that truthfully. That honesty is the whole point.

---

## What's inside

| Piece | File | What it does |
|---|---|---|
| Honest engine | `src/paperExchange.mjs` | Opens/closes positions with fake money, charges **real** taker fees on both sides, adverse slippage on every fill, and funding (0 for spot — we don't invent it). Every close uses the real price; losses are never rounded away. |
| Real data | `src/dataSource.mjs`, `src/record.mjs` | Live prices from the free **CoinDesk Data API**; recorded **real** candles for offline backtest/replay. |
| Strategies | `src/strategies/` | Three simple, long-only ideas: SMA crossover, RSI mean-reversion, Donchian breakout. |
| Backtester | `src/backtester.mjs` | Scores each strategy on real history with the **same** cost model and keeps only the ones that honestly pass. |
| Kelly sizing | `src/kelly.mjs` | Fractional (half-)Kelly position sizing from each strategy's own track record. No edge → no bet. |
| Reset-and-learn | `src/riskManager.mjs` | On a blow-up, closes everything truthfully, banks the lesson, resets, and tries the next generation more cautiously. |
| Live engine | `src/engine.mjs` | The loop: real prices → approved strategies → Kelly size → truthful exits → persist state. |
| Dashboard | `src/server.mjs`, `public/dashboard.html` | A live browser view of equity, positions, trades, fees, and banked lessons. |
| Honesty tests | `src/selftest.mjs` | Proves a flat round-trip loses money, a real loss is reported in full, etc. |

## Quick start

```bash
cd bot
npm test                 # prove the engine doesn't lie
npm run backtest         # score strategies on the included REAL price history
npm run start -- --replay   # replay recorded real prices (offline, honest: trades only approved strategies)
npm run serve            # open http://localhost:4317 for the live dashboard
```

To trade **live** real prices (still fake money):

```bash
npm run record           # refresh real price history from CoinDesk
npm start                # poll live prices every 15s; trades only backtest-approved strategies
```

Want to *see* the full machinery (sizing, exits, dashboard, blow-ups) even
though no strategy passed? Run the clearly-labeled demo — it trades the
**rejected** strategies on **real** prices, for show only:

```bash
npm run start -- --replay --demo
```

## The honest result

On the included ~100 days of real BTC/ETH prices, **no strategy passes** the
backtest gates once real taker fees (0.6%/side) and slippage are charged. That
is not a bug — it's the lesson. Simple signals rarely beat costs, so the live
engine **trades nothing** until something earns its place.

```
smaCross          trades=0   ❌ reject
rsiReversion      PF=0.54    ❌ reject
donchianBreakout  PF=1.65 but only 2 trades  ❌ reject (too few)
```

## Honesty notes (verify before trusting)

- **Fees** default to Coinbase Advanced Trade's top retail taker tier (0.6%).
  Confirm your real tier: <https://www.coinbase.com/advanced-fees>.
- **Slippage** (0.05%/fill) is a conservative estimate, not a measured fill.
- **Funding** applies to perpetual futures, not spot. It is **0** here. If you
  point this at perps, set a real funding rate — we never invent one.
- **Spot is long-only.** You can't truthfully short coins you don't own, so the
  bot doesn't pretend to.

## Going to real money

Don't — not until a strategy survives a long honest paper run *and* you fully
understand the risk. This repo deliberately ships with nothing approved. Paper
trade as long as you like first.
