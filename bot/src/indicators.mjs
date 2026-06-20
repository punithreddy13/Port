// indicators.mjs — plain, verifiable technical indicators.
// Each function takes an array of numbers (usually closes) and returns an array
// aligned to the input (null until enough data exists). No look-ahead.

// Simple Moving Average.
export function sma(values, period) {
  const out = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

// Relative Strength Index (Wilder's smoothing).
// Formula: RSI = 100 - 100/(1+RS), RS = avgGain/avgLoss.
// Reference: Wilder, "New Concepts in Technical Trading Systems" (1978).
export function rsi(values, period = 14) {
  const out = new Array(values.length).fill(null);
  if (values.length <= period) return out;
  let gain = 0, loss = 0;
  for (let i = 1; i <= period; i++) {
    const ch = values[i] - values[i - 1];
    if (ch >= 0) gain += ch; else loss -= ch;
  }
  let avgGain = gain / period;
  let avgLoss = loss / period;
  out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  for (let i = period + 1; i < values.length; i++) {
    const ch = values[i] - values[i - 1];
    const g = ch > 0 ? ch : 0;
    const l = ch < 0 ? -ch : 0;
    avgGain = (avgGain * (period - 1) + g) / period;
    avgLoss = (avgLoss * (period - 1) + l) / period;
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return out;
}

// Rolling highest high / lowest low over `period` (excludes current bar).
export function rollingExtremes(highs, lows, period) {
  const hi = new Array(highs.length).fill(null);
  const lo = new Array(lows.length).fill(null);
  for (let i = 0; i < highs.length; i++) {
    if (i < period) continue;
    let h = -Infinity, l = Infinity;
    for (let j = i - period; j < i; j++) {
      if (highs[j] > h) h = highs[j];
      if (lows[j] < l) l = lows[j];
    }
    hi[i] = h; lo[i] = l;
  }
  return { hi, lo };
}

// Sample standard deviation of an array.
export function stdev(arr) {
  if (arr.length < 2) return 0;
  const mean = arr.reduce((a, b) => a + b, 0) / arr.length;
  const v = arr.reduce((a, b) => a + (b - mean) ** 2, 0) / (arr.length - 1);
  return Math.sqrt(v);
}
