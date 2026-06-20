// util.mjs — small shared helpers. No trading logic here.
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { mkdirSync, readFileSync, writeFileSync, existsSync, appendFileSync } from "node:fs";

const __filename = fileURLToPath(import.meta.url);
export const SRC_DIR = dirname(__filename);
export const BOT_DIR = dirname(SRC_DIR);
export const DATA_DIR = join(BOT_DIR, "data");
export const RECORDED_DIR = join(DATA_DIR, "recorded");

export function ensureDirs() {
  mkdirSync(RECORDED_DIR, { recursive: true });
}

export function readJson(path, fallback = null) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return fallback;
  }
}

export function writeJson(path, obj) {
  ensureDirs();
  writeFileSync(path, JSON.stringify(obj, null, 2));
}

export function appendLine(path, line) {
  ensureDirs();
  appendFileSync(path, line + "\n");
}

export const fileExists = existsSync;

export const usd = (n) => `$${Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const pct = (n) => `${(n * 100).toFixed(2)}%`;
export const nowSec = () => Math.floor(Date.now() / 1000);
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Round to a sane number of decimals so logged numbers are not noise.
export const round = (n, d = 8) => {
  const f = 10 ** d;
  return Math.round(n * f) / f;
};
