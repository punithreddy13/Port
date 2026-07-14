"""FastAPI application: REST control plane + WebSocket streaming plane.

Run with:  uvicorn orderflow.server.app:app  (or `python -m orderflow`).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..core import Config, Side, Topic
from ..backtest.replay import ReplayEngine
from ..feeds.recorder import load_events
from ..feeds.simulated import MarketSimulator
from ..strategy.backtester import StrategyBacktester
from ..strategy.schema import METRICS, StrategyError
from ..analytics.performance import analyze, compare
from .state import Runtime

log = logging.getLogger("orderflow.server")
FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.load()
    runtime = Runtime(config)
    app = FastAPI(title="OrderFlow Terminal", version="0.1.0")
    app.state.runtime = runtime
    clients: set[WebSocket] = set()

    # ------------------------------------------------------------------ lifecycle
    @app.on_event("startup")
    async def _startup() -> None:
        runtime.start_feed()
        app.state.pump_task = asyncio.create_task(_pump())

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        app.state.pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app.state.pump_task
        runtime.stop()

    async def _pump() -> None:
        """Frame loop: capture book history, run assessments, broadcast."""
        interval = 1.0 / config.ui_fps
        while True:
            await asyncio.sleep(interval)
            try:
                # drive interactive replay if one is playing
                rp = runtime.replay
                if rp and rp.status.playing:
                    rp.step_seconds(interval * rp.status.speed)
                    frame = runtime.build_frame(rp.status.ts, rp.pipeline)
                    frame["replay"] = rp.status.as_dict()
                else:
                    frame = runtime.build_frame(time.time())
                    if rp:
                        frame["replay"] = rp.status.as_dict()
            except Exception:  # noqa: BLE001 - keep the loop alive
                log.exception("frame build failed")
                continue
            dead = []
            for ws in clients:
                try:
                    await ws.send_json(frame)
                except Exception:  # noqa: BLE001
                    dead.append(ws)
            for ws in dead:
                clients.discard(ws)

    # ------------------------------------------------------------------ websocket
    @app.websocket("/ws/stream")
    async def stream(ws: WebSocket) -> None:
        await ws.accept()
        clients.add(ws)
        try:
            while True:
                await ws.receive_text()  # keepalive / ignore client chatter
        except WebSocketDisconnect:
            clients.discard(ws)

    # ------------------------------------------------------------------ REST: status & AI
    @app.get("/api/status")
    def status() -> dict:
        return {
            "symbol": config.symbol,
            "feed": config.feed,
            "events": runtime.pipeline.bus.events_published,
            "signals": runtime.pipeline.liquidity.signals_emitted,
            "broker": runtime.pipeline.broker.summary(),
            "risk": runtime.pipeline.risk.summary(),
            "ai_accuracy": runtime.pipeline.learning.ai_accuracy(),
        }

    @app.post("/api/ask")
    def ask(body: dict) -> dict:
        q = body.get("question", "")
        if not q:
            raise HTTPException(400, "missing 'question'")
        with runtime.lock:
            return runtime.pipeline.fusion.answer(q, time.time(), runtime.pipeline.book_engine.book.mid)

    @app.get("/api/patterns")
    def patterns() -> dict:
        return {"patterns": runtime.pipeline.learning.patterns.report(),
                "ai_accuracy": runtime.pipeline.learning.ai_accuracy()}

    # ------------------------------------------------------------------ REST: risk & trading
    @app.get("/api/risk")
    def get_risk() -> dict:
        return runtime.pipeline.risk.summary()

    @app.post("/api/risk/size")
    def size(body: dict) -> dict:
        tr = runtime.pipeline.risk.position_size(
            float(body["entry"]), float(body["stop"]), float(body.get("rr", 2.0)))
        return vars(tr)

    @app.post("/api/trade")
    def trade(body: dict) -> dict:
        try:
            side = Side(body["side"])
        except (KeyError, ValueError):
            raise HTTPException(400, "side must be 'buy' or 'sell'") from None
        with runtime.lock:
            order = runtime.pipeline.broker.submit(
                side, float(body.get("size", 1)),
                stop=body.get("stop"), take_profit=body.get("take_profit"),
                setup=body.get("setup", "manual"), trader="trader",
            )
        return {"order_id": order.id, "status": order.status,
                "rejections": runtime.pipeline.broker.state.rejected[-1:] if order.status == "rejected" else []}

    @app.get("/api/performance")
    def performance() -> dict:
        trades = runtime.pipeline.broker.closed_trades
        groups: dict[str, list] = {}
        for t in trades:
            groups.setdefault(t.trader, []).append(t)
        return {
            "overall": analyze(trades).as_dict(),
            "comparison": compare(groups) if groups else {},
        }

    # ------------------------------------------------------------------ REST: strategies
    @app.get("/api/strategies")
    def list_strategies() -> dict:
        return {"strategies": list(runtime.strategies.values()), "metrics": METRICS}

    @app.post("/api/strategies")
    def put_strategy(body: dict) -> dict:
        try:
            return runtime.put_strategy(body)
        except StrategyError as e:
            raise HTTPException(422, str(e)) from None

    @app.post("/api/strategies/{name}/backtest")
    def backtest(name: str, body: dict | None = None) -> dict:
        strategy = runtime.strategies.get(name)
        if not strategy:
            raise HTTPException(404, f"no strategy '{name}'")
        body = body or {}
        events = _get_history_events(body)
        bt = StrategyBacktester(strategy, config)
        return bt.run(events)

    # ------------------------------------------------------------------ REST: replay
    def _get_history_events(body: dict) -> list:
        path = body.get("path")
        if path:
            p = Path(config.data_dir) / Path(path).name  # confine to data dir
            if not p.exists():
                raise HTTPException(404, f"no recording at {p}")
            return load_events(p)
        seconds = float(body.get("simulate_seconds", 300))
        seconds = min(seconds, 3600.0)
        sim = MarketSimulator(config.symbol, tick=config.tick_size, seed=int(body.get("seed", 42)))
        snapshot = (Topic.BOOK_SNAPSHOT, sim.snapshot())
        events = list(sim.run_for(seconds))
        return [snapshot, *events]

    @app.post("/api/replay/load")
    def replay_load(body: dict) -> dict:
        events = _get_history_events(body or {})
        runtime.replay = ReplayEngine(events=events, config=config)
        if body.get("ai_trading"):
            runtime.replay.enable_ai_trading()
        return runtime.replay.status.as_dict()

    @app.post("/api/replay/control")
    def replay_control(body: dict) -> dict:
        rp = runtime.replay
        if not rp:
            raise HTTPException(409, "no replay loaded")
        action = body.get("action")
        if action == "play":
            rp.play()
        elif action == "pause":
            rp.pause()
        elif action == "speed":
            rp.set_speed(float(body.get("value", 1.0)))
        elif action == "jump":
            rp.jump_to(float(body.get("value", rp.status.start_ts)))
        elif action == "step":
            rp.step(int(body.get("value", 1)))
        else:
            raise HTTPException(400, f"unknown action '{action}'")
        return rp.status.as_dict()

    @app.post("/api/replay/trade")
    def replay_trade(body: dict) -> dict:
        rp = runtime.replay
        if not rp:
            raise HTTPException(409, "no replay loaded")
        return rp.user_trade(body["side"], float(body.get("size", 1)),
                             stop=body.get("stop"), take_profit=body.get("take_profit"))

    @app.get("/api/replay/performance")
    def replay_performance() -> JSONResponse:
        rp = runtime.replay
        if not rp:
            raise HTTPException(409, "no replay loaded")
        return JSONResponse(rp.performance())

    # ------------------------------------------------------------------ frontend
    if FRONTEND_DIR.exists():
        app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(FRONTEND_DIR / "index.html")

    return app


app = create_app()


def main() -> None:
    import uvicorn

    cfg = Config.load()
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
