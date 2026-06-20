// record.mjs — capture REAL price history to data/recorded/*.json so the
// backtester and replay engine run on genuine prices offline.
//
// Usage: npm run record
// It hits the free CoinDesk Data API for each configured symbol and saves the
// raw response (real candles). Run it whenever you want fresh history.
import CONFIG from "./config.mjs";
import { writeJson, RECORDED_DIR, ensureDirs } from "./util.mjs";
import { join } from "node:path";

async function fetchRaw(instrument, frequency, limit) {
  const url = `${CONFIG.dataApiBase}/spot/v1/historical/${frequency}?market=${CONFIG.market}&instrument=${instrument}&limit=${limit}`;
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${instrument}`);
  const json = await res.json();
  if (!Array.isArray(json?.Data) || json.Data.length === 0) {
    throw new Error(`Empty data for ${instrument} — refusing to save a fake file.`);
  }
  return json.Data;
}

async function main() {
  ensureDirs();
  const frequency = "days";
  const limit = 100;
  for (const inst of CONFIG.symbols) {
    try {
      const rows = await fetchRaw(inst, frequency, limit);
      const path = join(RECORDED_DIR, `${CONFIG.market}_${inst}_${frequency}.json`);
      writeJson(path, { Data: rows });
      console.log(`Saved ${rows.length} real ${frequency} candles for ${inst} -> ${path}`);
    } catch (e) {
      console.error(`! ${inst}: ${e.message}`);
    }
  }
}

main();
