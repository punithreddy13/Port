/* OrderFlow Terminal frontend.
 *
 * Zero dependencies by design: everything renders on two <canvas> elements
 * plus lightweight DOM panels, driven by one WebSocket frame stream.
 * State kept client-side: rolling heatmap columns, vwap series, price range.
 */
"use strict";

// ---------------------------------------------------------------- state
const S = {
  frames: [],          // rolling heat columns {ts, mid, levels:[[p,signed]]}
  maxFrames: 300,
  vwapSeries: [],      // [ts, vwap]
  lastFrame: null,
  priceCenter: null,   // smoothed view centre
  ticksVisible: 90,
  heatScale: 50,       // rolling max size for colour normalisation
  lastPrice: null,
  prevPrice: null,
  replayTotal: 0,
};

const $ = (id) => document.getElementById(id);
const heatCanvas = $("heatmap");
const deltaCanvas = $("delta-panel");

// ---------------------------------------------------------------- websocket
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/stream`);
  ws.onopen = () => setConn(true);
  ws.onclose = () => { setConn(false); setTimeout(connect, 1500); };
  ws.onmessage = (msg) => {
    const frame = JSON.parse(msg.data);
    if (frame.type === "frame") onFrame(frame);
  };
  setInterval(() => { if (ws.readyState === 1) ws.send("ping"); }, 15000);
}
function setConn(ok) {
  $("conn-dot").classList.toggle("ok", ok);
  $("conn-text").textContent = ok ? "streaming" : "reconnecting";
}

// ---------------------------------------------------------------- frame intake
function onFrame(f) {
  S.lastFrame = f;
  if (f.heat) {
    const prev = S.frames[S.frames.length - 1];
    if (!prev || prev.ts !== f.heat.ts) {
      S.frames.push(f.heat);
      if (S.frames.length > S.maxFrames) S.frames.shift();
    }
  }
  if (f.vwap != null) {
    S.vwapSeries.push([f.ts, f.vwap]);
    if (S.vwapSeries.length > S.maxFrames) S.vwapSeries.shift();
  }
  S.prevPrice = S.lastPrice;
  S.lastPrice = f.last ?? f.mid;

  renderTopbar(f);
  renderDom(f);
  renderTape(f);
  renderAI(f);
  renderFooter(f);
  drawHeatmap(f);
  drawDeltaPanel(f);
}

// ---------------------------------------------------------------- top bar
function renderTopbar(f) {
  $("symbol").textContent = f.symbol;
  const el = $("last-price");
  el.textContent = S.lastPrice != null ? fmt(S.lastPrice) : "—";
  el.className = "price " + (S.prevPrice != null && S.lastPrice > S.prevPrice ? "up"
    : S.prevPrice != null && S.lastPrice < S.prevPrice ? "down" : "");
  const a = f.assessment;
  if (!a) return;
  setChip("chip-control", `CONTROL ${a.control.toUpperCase()}`,
    a.control === "buyers" ? "buyers" : a.control === "sellers" ? "sellers" : "");
  setChip("chip-inst", `INST ${a.institutional_bias.toUpperCase()}`,
    a.institutional_bias === "accumulating" ? "buyers" : a.institutional_bias === "distributing" ? "sellers" : "");
  setChip("chip-cont", `CONT ${(a.p_continuation * 100).toFixed(0)}%`, a.p_continuation > 0.65 ? "hot" : "");
  setChip("chip-rev", `REV ${(a.p_reversal * 100).toFixed(0)}%`, a.p_reversal > 0.6 ? "hot" : "");
  setChip("chip-conf", `CONF ${(a.confidence * 100).toFixed(0)}%`, "");
}
function setChip(id, text, cls) {
  const el = $(id);
  el.textContent = text;
  el.className = "chip " + cls;
}

// ---------------------------------------------------------------- DOM ladder
function renderDom(f) {
  const dom = $("dom");
  const asks = (f.dom?.asks || []).slice(0, 12).reverse();
  const bids = (f.dom?.bids || []).slice(0, 12);
  const maxQ = Math.max(1, ...asks.map((r) => r[1]), ...bids.map((r) => r[1]));
  let html = "";
  for (const [p, q] of asks) html += domRow("", p, q, "ask", maxQ, false);
  let best = true;
  for (const [p, q] of bids) { html += domRow(q, p, "", "bid", maxQ, best); best = false; }
  dom.innerHTML = html;
}
function domRow(bidQ, price, askQ, side, maxQ, best) {
  const q = side === "bid" ? bidQ : askQ;
  const pct = Math.min(100, (q / maxQ) * 100).toFixed(0);
  const bar = `<span class="bar" style="width:${pct}%"></span>`;
  return `<div class="dom-row ${best ? "best" : ""}">
    <span class="dom-bid">${side === "bid" ? bar + `<span class="qty">${fmtQ(q)}</span>` : ""}</span>
    <span class="p">${fmt(price)}</span>
    <span class="dom-ask">${side === "ask" ? bar + `<span class="qty">${fmtQ(q)}</span>` : ""}</span>
  </div>`;
}

// ---------------------------------------------------------------- tape
function renderTape(f) {
  const rows = (f.tape || []).slice().reverse();
  const avg = rows.length ? rows.reduce((s, r) => s + r.q, 0) / rows.length : 1;
  $("tape").innerHTML = rows.map((r) =>
    `<div class="tape-row ${r.s}"><span>${fmtT(r.ts)}</span>
     <span>${fmt(r.p)}</span><span class="${r.q > 4 * avg ? "big" : ""}">${fmtQ(r.q)}</span></div>`
  ).join("");
}

// ---------------------------------------------------------------- AI panels
function renderAI(f) {
  const card = $("plan-card");
  const plan = f.assessment?.trade_plan;
  if (plan) {
    card.className = "card";
    card.innerHTML =
      `<span class="dir-${plan.direction}">${plan.direction.toUpperCase()}</span> @ ${fmt(plan.entry)}<br>` +
      `stop ${fmt(plan.stop)} · target ${fmt(plan.targets[0])} · R:R ${plan.risk_reward}<br>` +
      `<span style="color:var(--text-dim)">${plan.rationale}</span>`;
  } else {
    card.className = "card muted";
    card.textContent = "no active trade plan — waiting for edge";
  }
  $("narration").innerHTML = (f.narration || []).slice().reverse().map((n) =>
    `<div class="nar-row ${n.severity}"><span class="t">${fmtT(n.ts)}</span>${esc(n.text)}</div>`
  ).join("");
  $("signal-list").innerHTML = (f.signals || []).slice().reverse().map((s) =>
    `<div class="sig-row"><span class="sig-badge ${s.side || "none"}">${badge(s.type)}</span>
     <span>${fmt(s.price)}</span><span class="sig-conf">${(s.confidence * 100).toFixed(0)}%</span></div>`
  ).join("");
}
const BADGES = {
  liquidity_wall: "WALL", spoofing: "SPOOF", iceberg: "ICE", absorption: "ABS",
  liquidity_sweep: "SWEEP", imbalance: "IMB", delta_imbalance: "ΔIMB",
  large_participant: "BLOCK", liquidity_migration: "MIGR", liquidity_exhaustion: "EXH",
  pulled_liquidity: "PULL", added_liquidity: "ADD", hidden_liquidity: "HIDN",
  aggressive_flow: "AGGR", break_of_structure: "BOS", change_of_character: "CHOCH",
  fair_value_gap: "FVG", order_block: "OB", breaker_block: "BRKR", mitigation_block: "MIT",
  stop_hunt: "HUNT", fake_breakout: "FAKE", trapped_buyers: "TRAPB", trapped_sellers: "TRAPS",
  accumulation: "ACC", distribution: "DIST", equal_highs: "EQH", equal_lows: "EQL",
  swing_high: "SH", swing_low: "SL", liquidity_pool: "POOL",
};
const badge = (t) => BADGES[t] || t.slice(0, 5).toUpperCase();

// ---------------------------------------------------------------- footer
function renderFooter(f) {
  const b = f.broker || {};
  const pos = b.position || 0;
  const posEl = $("pos-readout");
  posEl.textContent = pos === 0 ? "flat" : `${pos > 0 ? "LONG" : "SHORT"} ${Math.abs(pos)} @ ${fmt(b.avg_price)}`;
  posEl.className = "readout " + (pos > 0 ? "long" : pos < 0 ? "short" : "");
  const pnl = (b.realized || 0) + (b.unrealized || 0);
  $("pnl-readout").textContent = `P&L ${pnl.toFixed(2)}${f.risk?.halted ? " ⛔ " + f.risk.halt_reason : ""}`;

  const r = f.replay;
  if (r) {
    S.replayTotal = r.total;
    const pct = r.total ? ((r.cursor / r.total) * 1000) | 0 : 0;
    if (document.activeElement !== $("replay-scrub")) $("replay-scrub").value = pct;
    $("replay-status").textContent = r.finished ? "replay done"
      : r.playing ? `replay ${r.speed}× ${fmtT(r.ts)}` : `replay paused ${fmtT(r.ts)}`;
  } else {
    $("replay-status").textContent = "live";
  }
}

// ---------------------------------------------------------------- heatmap
function drawHeatmap(f) {
  const ctx = heatCanvas.getContext("2d");
  const W = heatCanvas.width = heatCanvas.clientWidth * devicePixelRatio;
  const H = heatCanvas.height = heatCanvas.clientHeight * devicePixelRatio;
  ctx.clearRect(0, 0, W, H);
  if (!S.frames.length) return;
  const tick = f.tick || 0.25;

  // smooth-follow price centre
  const target = f.mid ?? S.priceCenter ?? 0;
  S.priceCenter = S.priceCenter == null ? target : S.priceCenter * 0.9 + target * 0.1;
  const span = S.ticksVisible * tick;
  const pTop = S.priceCenter + span / 2, pBot = S.priceCenter - span / 2;
  const y = (price) => ((pTop - price) / span) * H;
  const rowH = Math.max(1, H / S.ticksVisible);

  // rolling max for colour normalisation
  let maxSize = 1;
  for (const fr of S.frames) for (const [, s] of fr.levels) maxSize = Math.max(maxSize, Math.abs(s));
  S.heatScale = S.heatScale * 0.95 + maxSize * 0.05;

  const t0 = S.frames[0].ts, t1 = S.frames[S.frames.length - 1].ts;
  const tSpan = Math.max(1e-9, t1 - t0);
  const plotW = W * 0.86; // right margin for price axis + profile gutter
  const x = (ts) => ((ts - t0) / tSpan) * plotW;
  const colW = Math.max(1, plotW / S.frames.length + 0.5);

  // liquidity columns
  for (const fr of S.frames) {
    const cx = x(fr.ts);
    for (const [p, s] of fr.levels) {
      if (p > pTop || p < pBot) continue;
      ctx.fillStyle = heatColor(Math.abs(s) / S.heatScale, s > 0);
      ctx.fillRect(cx, y(p) - rowH / 2, colW, rowH);
    }
  }

  // structure zones
  if ($("toggle-zones").checked && f.structure) {
    for (const z of f.structure.zones || []) {
      const zy = y(z.top), zh = y(z.bottom) - y(z.top);
      ctx.fillStyle = z.side === "buy" ? "rgba(46,204,143,0.10)" : "rgba(255,84,112,0.10)";
      ctx.fillRect(0, zy, plotW, zh);
      ctx.strokeStyle = z.side === "buy" ? "rgba(46,204,143,0.35)" : "rgba(255,84,112,0.35)";
      ctx.strokeRect(0.5, zy + 0.5, plotW - 1, zh - 1);
    }
    for (const pool of f.structure.pools || []) {
      ctx.strokeStyle = "rgba(240,180,41,0.6)";
      ctx.setLineDash([6, 5]);
      ctx.beginPath(); ctx.moveTo(0, y(pool.price)); ctx.lineTo(plotW, y(pool.price)); ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  // candles
  if ($("toggle-candles").checked && f.candles?.length) {
    const cw = Math.max(2, (f.candles.length > 1
      ? x(t0 + (f.candles[1].t - f.candles[0].t)) : 8) * 0.7);
    for (const c of f.candles) {
      const cx = x(c.t + (f.candles.length > 1 ? (f.candles[1].t - f.candles[0].t) / 2 : 0));
      if (cx < 0) continue;
      const up = c.c >= c.o;
      ctx.strokeStyle = up ? "rgba(46,204,143,0.9)" : "rgba(255,84,112,0.9)";
      ctx.fillStyle = up ? "rgba(46,204,143,0.35)" : "rgba(255,84,112,0.35)";
      ctx.beginPath(); ctx.moveTo(cx, y(c.h)); ctx.lineTo(cx, y(c.l)); ctx.stroke();
      const top = y(Math.max(c.o, c.c)), bh = Math.max(1, Math.abs(y(c.o) - y(c.c)));
      ctx.fillRect(cx - cw / 2, top, cw, bh);
      ctx.strokeRect(cx - cw / 2, top, cw, bh);
    }
  }

  // volume bubbles
  if ($("toggle-bubbles").checked) {
    let maxQ = 1;
    for (const b of f.bubbles || []) maxQ = Math.max(maxQ, b.q);
    for (const b of f.bubbles || []) {
      if (b.ts < t0) continue;
      const r = 2 + Math.sqrt(b.q / maxQ) * 14 * devicePixelRatio;
      ctx.fillStyle = b.s === "buy" ? "rgba(46,204,143,0.45)" : "rgba(255,84,112,0.45)";
      ctx.beginPath(); ctx.arc(x(b.ts), y(b.p), r, 0, Math.PI * 2); ctx.fill();
    }
  }

  // signal markers
  if ($("toggle-signals").checked) {
    ctx.font = `${10 * devicePixelRatio}px monospace`;
    for (const s of f.signals || []) {
      if (s.ts < t0 || !s.price) continue;
      ctx.fillStyle = s.side === "buy" ? "#2ecc8f" : s.side === "sell" ? "#ff5470" : "#f0b429";
      ctx.fillText(badge(s.type), x(s.ts) + 3, y(s.price) - 3);
    }
  }

  // VWAP line
  if (S.vwapSeries.length > 1) {
    ctx.strokeStyle = "rgba(53,195,240,0.8)";
    ctx.lineWidth = devicePixelRatio;
    ctx.beginPath();
    let started = false;
    for (const [ts, v] of S.vwapSeries) {
      if (ts < t0) continue;
      const px = x(ts), py = y(v);
      if (!started) { ctx.moveTo(px, py); started = true; } else ctx.lineTo(px, py);
    }
    ctx.stroke();
  }

  // volume profile gutter (right of plot, left of axis)
  if ($("toggle-profile").checked && f.profile?.bins?.length) {
    const gx = plotW, gw = W * 0.06;
    let maxV = 1;
    for (const [, v] of f.profile.bins) maxV = Math.max(maxV, v);
    const va = f.profile.value_area;
    for (const [p, v, bv, sv] of f.profile.bins) {
      if (p > pTop || p < pBot) continue;
      const w = (v / maxV) * gw;
      const inVa = va && p >= va[0] && p <= va[1];
      ctx.fillStyle = inVa ? "rgba(53,195,240,0.45)" : "rgba(53,195,240,0.18)";
      ctx.fillRect(gx, y(p) - rowH / 2, w, Math.max(1, rowH - 1));
    }
    if (f.profile.poc != null) {
      ctx.strokeStyle = "rgba(240,180,41,0.9)";
      ctx.beginPath(); ctx.moveTo(gx, y(f.profile.poc)); ctx.lineTo(gx + gw, y(f.profile.poc)); ctx.stroke();
    }
  }

  // price axis
  ctx.fillStyle = "#6b7689";
  ctx.font = `${10 * devicePixelRatio}px monospace`;
  const step = niceStep(span / 10);
  for (let p = Math.ceil(pBot / step) * step; p <= pTop; p += step) {
    ctx.fillText(fmt(p), W * 0.935, y(p) + 3);
    ctx.fillStyle = "rgba(107,118,137,0.12)";
    ctx.fillRect(0, y(p), plotW, 1);
    ctx.fillStyle = "#6b7689";
  }
  // last price marker
  if (S.lastPrice != null) {
    ctx.fillStyle = "#c9d2e0";
    ctx.fillRect(0, y(S.lastPrice), W * 0.92, devicePixelRatio);
    ctx.fillStyle = "#0b0e14";
    ctx.fillRect(W * 0.92, y(S.lastPrice) - 8 * devicePixelRatio, W * 0.08, 16 * devicePixelRatio);
    ctx.fillStyle = "#ffffff";
    ctx.fillText(fmt(S.lastPrice), W * 0.935, y(S.lastPrice) + 3);
  }
}

function heatColor(t, isBid) {
  // intensity ramp: dark → colour → white-hot; bids green-cyan, asks red-amber
  t = Math.min(1, Math.max(0, Math.pow(t, 0.55)));
  if (isBid) {
    const r = Math.round(10 + 90 * t * t), g = Math.round(40 + 190 * t), b = Math.round(60 + 130 * t);
    return `rgb(${r},${g},${b})`;
  }
  const r = Math.round(60 + 195 * t), g = Math.round(25 + 110 * t), b = Math.round(40 + 40 * t);
  return `rgb(${r},${g},${b})`;
}

// ---------------------------------------------------------------- delta panel
function drawDeltaPanel(f) {
  const ctx = deltaCanvas.getContext("2d");
  const W = deltaCanvas.width = deltaCanvas.clientWidth * devicePixelRatio;
  const H = deltaCanvas.height = deltaCanvas.clientHeight * devicePixelRatio;
  ctx.clearRect(0, 0, W, H);
  const candles = f.candles || [];
  const half = H / 2;

  // per-candle delta histogram (bottom half baseline at 3/4 height)
  if (candles.length) {
    let maxD = 1;
    for (const c of candles) maxD = Math.max(maxD, Math.abs(c.d));
    const bw = W / Math.max(24, candles.length);
    candles.forEach((c, i) => {
      const h = (Math.abs(c.d) / maxD) * (H * 0.42);
      const up = c.d >= 0;
      ctx.fillStyle = up ? "rgba(46,204,143,0.7)" : "rgba(255,84,112,0.7)";
      ctx.fillRect(i * bw + 1, up ? H * 0.75 - h : H * 0.75, bw - 2, h);
    });
    ctx.fillStyle = "rgba(107,118,137,0.4)";
    ctx.fillRect(0, H * 0.75, W, 1);
  }

  // cumulative delta line (top half)
  const cd = f.cum_delta || [];
  if (cd.length > 1) {
    let lo = Infinity, hi = -Infinity;
    for (const [, v] of cd) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    const range = Math.max(1e-9, hi - lo);
    ctx.strokeStyle = "#35c3f0";
    ctx.lineWidth = devicePixelRatio;
    ctx.beginPath();
    cd.forEach(([ts, v], i) => {
      const px = (i / (cd.length - 1)) * W;
      const py = 6 + (1 - (v - lo) / range) * (half - 12);
      i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
    });
    ctx.stroke();
    ctx.fillStyle = "#6b7689";
    ctx.font = `${10 * devicePixelRatio}px monospace`;
    ctx.fillText(`CUM Δ ${cd[cd.length - 1][1].toFixed(0)}`, 8, 14 * devicePixelRatio);
    const last = candles.length ? candles[candles.length - 1].d : 0;
    ctx.fillText(`BAR Δ ${last.toFixed(0)}`, 8, H * 0.75 - 6);
  }
}

// ---------------------------------------------------------------- helpers
const fmt = (p) => p == null ? "—" : (+p).toFixed(p < 10 ? 4 : 2);
const fmtQ = (q) => q >= 1000 ? (q / 1000).toFixed(1) + "k" : (+q).toFixed(0);
const fmtT = (ts) => new Date(ts * 1000).toISOString().slice(11, 19);
const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
function niceStep(raw) {
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / mag;
  return (n < 1.5 ? 1 : n < 3.5 ? 2.5 : n < 7.5 ? 5 : 10) * mag;
}

// ---------------------------------------------------------------- controls
async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return res.json();
}

$("btn-buy").onclick = () => trade("buy");
$("btn-sell").onclick = () => trade("sell");
$("btn-flat").onclick = () => {
  const pos = S.lastFrame?.broker?.position || 0;
  if (pos !== 0) trade(pos > 0 ? "sell" : "buy", Math.abs(pos));
};
function trade(side, size) {
  size = size ?? (parseFloat($("trade-size").value) || 1);
  const inReplay = S.lastFrame?.replay && !S.lastFrame.replay.finished;
  api(inReplay ? "/api/replay/trade" : "/api/trade", { side, size });
}

$("btn-replay-load").onclick = async () => {
  $("replay-status").textContent = "loading replay…";
  await api("/api/replay/load", { simulate_seconds: 600, seed: Date.now() % 100000, ai_trading: true });
  await api("/api/replay/control", { action: "play" });
};
$("btn-replay-play").onclick = () => api("/api/replay/control", { action: "play" });
$("btn-replay-pause").onclick = () => api("/api/replay/control", { action: "pause" });
$("replay-speed").onchange = (e) =>
  api("/api/replay/control", { action: "speed", value: parseFloat(e.target.value) });
$("replay-scrub").onchange = (e) => {
  const r = S.lastFrame?.replay;
  if (!r) return;
  const ts = r.start_ts + (e.target.value / 1000) * (r.end_ts - r.start_ts);
  api("/api/replay/control", { action: "jump", value: ts });
};

$("btn-ask").onclick = ask;
$("ask-input").addEventListener("keydown", (e) => { if (e.key === "Enter") ask(); });
async function ask() {
  const q = $("ask-input").value.trim();
  if (!q) return;
  const res = await api("/api/ask", { question: q });
  const text = typeof res.answer === "object" ? JSON.stringify(res.answer, null, 1) : String(res.answer);
  const nar = $("narration");
  nar.insertAdjacentHTML("afterbegin",
    `<div class="nar-row alert"><span class="t">Q</span>${esc(q)}</div>` +
    `<div class="nar-row notice"><span class="t">A</span>${esc(text)} ` +
    `<span style="color:var(--text-dim)">(conf ${(res.confidence * 100).toFixed(0)}%)</span></div>`);
  $("ask-input").value = "";
}

connect();
