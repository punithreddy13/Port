"""API surface tests via FastAPI TestClient (feed not started — endpoints
must work against a cold pipeline too)."""
import pytest
from fastapi.testclient import TestClient

from orderflow.core import Config
from orderflow.server.app import create_app


@pytest.fixture
def client(tmp_path):
    cfg = Config(symbol="TEST", tick_size=0.25, data_dir=str(tmp_path), feed="simulated")
    app = create_app(cfg)
    # note: TestClient triggers startup/shutdown, so the sim feed runs briefly
    with TestClient(app) as c:
        yield c


def test_status(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "TEST"
    assert "broker" in body and "risk" in body


def test_ask(client):
    r = client.post("/api/ask", json={"question": "who is in control?"})
    assert r.status_code == 200
    assert "answer" in r.json()


def test_risk_sizing(client):
    r = client.post("/api/risk/size", json={"entry": 100.0, "stop": 99.0, "rr": 2.0})
    assert r.status_code == 200
    body = r.json()
    assert body["size"] > 0
    assert body["take_profit"] == 102.0


def test_strategy_crud_and_backtest(client):
    strategy = {
        "name": "api-test",
        "entry": {"all": [{"metric": "delta_30s", "op": "abs>", "value": 40}]},
        "risk": {"risk_per_trade_pct": 1.0, "stop_ticks": 10, "rr": 2.0},
    }
    r = client.post("/api/strategies", json=strategy)
    assert r.status_code == 200
    r = client.get("/api/strategies")
    assert any(s["name"] == "api-test" for s in r.json()["strategies"])
    assert "delta_30s" in r.json()["metrics"]
    r = client.post("/api/strategies/api-test/backtest", json={"simulate_seconds": 60, "seed": 5})
    assert r.status_code == 200
    assert "report" in r.json()


def test_strategy_validation_rejected(client):
    r = client.post("/api/strategies", json={"name": "bad", "entry": {"all": [{"metric": "nope", "op": ">", "value": 1}]}})
    assert r.status_code == 422


def test_replay_lifecycle(client):
    r = client.post("/api/replay/load", json={"simulate_seconds": 30, "seed": 3})
    assert r.status_code == 200
    assert r.json()["total"] > 0
    r = client.post("/api/replay/control", json={"action": "step", "value": 50})
    assert r.json()["cursor"] == 50
    r = client.post("/api/replay/control", json={"action": "speed", "value": 4})
    assert r.json()["speed"] == 4
    r = client.post("/api/replay/trade", json={"side": "buy", "size": 1})
    assert r.status_code == 200
    r = client.get("/api/replay/performance")
    assert r.status_code == 200
    assert "ai_decisions" in r.json()


def test_trade_endpoint(client):
    r = client.post("/api/trade", json={"side": "buy", "size": 1})
    assert r.status_code == 200
    r = client.post("/api/trade", json={"side": "hold"})
    assert r.status_code == 400


def test_patterns_endpoint(client):
    r = client.get("/api/patterns")
    assert r.status_code == 200
    assert "ai_accuracy" in r.json()
