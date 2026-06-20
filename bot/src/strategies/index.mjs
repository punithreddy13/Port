// strategies/index.mjs — registry of simple, honest trading ideas.
//
// Each strategy is long-only and stateless: given the candle history seen SO
// FAR (no look-ahead), it returns "enter", "exit", or "hold". The engine and
// backtester handle money, fees, and exits identically, so a backtest result
// means something for live trading.
import { smaCross } from "./smaCross.mjs";
import { rsiReversion } from "./rsiReversion.mjs";
import { donchianBreakout } from "./donchianBreakout.mjs";

export const STRATEGIES = {
  smaCross,
  rsiReversion,
  donchianBreakout,
};

export default STRATEGIES;
