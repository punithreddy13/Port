// dataSource.mjs — where REAL prices come from. Two honest modes:
//
//   live   : HTTP calls to the CoinDesk Data API (free). Real-time prices.
//   replay : reads recorded REAL candles from data/recorded/*.json and steps
//            through them. Same genuine prices, just captured earlier. Used for
//            the backtester and for verifying the engine offline.
//
// We never synthesize a price. If a source is unavailable we say so and stop.
import { join } from "node:path";
import CONFIG from "./config.mjs";
import { RECORDED_DIR, readJson } from "./util.mjs";

// Normalize one CoinDesk OHLCV row -> our compact candle.
function normalizeCandle(row) {
  return {
    t: row.TIMESTAMP,
    o: row.OPEN,
    h: row.HIGH,
    l: row.LOW,
    c: row.CLOSE,
    v: row.VOLUME,
  };
}

function recordedPath(market, instrument, frequency) {
  return join(RECORDED_DIR, `${market}_${instrument}_${frequency}.json`);
}

// ---- Live source ----------------------------------------------------------
async function httpJson(url) {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status} from ${url}`);
  return res.json();
}

export const liveSource = {
  // Current real price for an instrument.
  async tick(instrument) {
    const url = `${CONFIG.dataApiBase}/spot/v1/latest/tick?market=${CONFIG.market}&instruments=${instrument}`;
    const json = await httpJson(url);
    const d = json?.Data?.[instrument];
    if (!d || typeof d.PRICE !== "number") {
      throw new Error(`No live price for ${instrument}; refusing to guess one.`);
    }
    return { instrument, price: d.PRICE, t: d.PRICE_LAST_UPDATE_TS };
  },
  // Historical real candles (newest last).
  async candles(instrument, frequency = "days", limit = 100) {
    const url = `${CONFIG.dataApiBase}/spot/v1/historical/${frequency}?market=${CONFIG.market}&instrument=${instrument}&limit=${limit}`;
    const json = await httpJson(url);
    const rows = json?.Data;
    if (!Array.isArray(rows) || rows.length === 0) {
      throw new Error(`No historical candles for ${instrument}.`);
    }
    return rows.map(normalizeCandle);
  },
};

// ---- Replay source (recorded real prices) ---------------------------------
export function loadRecorded(instrument, frequency = "days") {
  const file = recordedPath(CONFIG.market, instrument, frequency);
  const json = readJson(file);
  if (!json?.Data) return null;
  return json.Data.map(normalizeCandle);
}

// A cursor that walks recorded candles one bar at a time, like a live feed.
export function makeReplaySource(instrument, frequency = "days") {
  const candles = loadRecorded(instrument, frequency);
  if (!candles) {
    throw new Error(
      `No recorded data for ${instrument} (${frequency}). Run "npm run record" first.`
    );
  }
  let i = 0;
  return {
    instrument,
    total: candles.length,
    hasNext: () => i < candles.length,
    next: () => candles[i++],
    history: () => candles.slice(0, i), // everything seen so far, no look-ahead
    all: () => candles.slice(),
    reset: () => { i = 0; },
  };
}
