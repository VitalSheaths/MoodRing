"""Exercise the documented offline HTTP API. Never fetches SEC or market data.

Start the API with PAPER_QUOTE_MODE=offline, then run with PAPER_SMOKE_TOKEN set.
The workflow creates ordinary paper ledger records; use a disposable local ledger.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx


def run_workflow(client):
    def request(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"API smoke failed: {method} {path} (HTTP {response.status_code})"
            )
        return response.json()

    ready = request("GET", "/ready")
    if ready["quote_provider_mode"] != "offline":
        raise RuntimeError("API smoke requires explicitly synthetic offline mode")
    first = request(
        "POST",
        "/api/v1/scenarios",
        json={"name": "Offline API lesson", "config": {"auto_exit": False}},
    )
    second = request(
        "POST",
        "/api/v1/scenarios",
        json={"name": "Comparison lesson", "config": {"auto_exit": False}},
    )
    sid = first["id"]
    assert sid in {s["id"] for s in request("GET", "/api/v1/scenarios")["scenarios"]}
    request("GET", f"/api/v1/scenarios/{sid}")
    request("POST", "/api/v1/scenarios/compare", json={"ids": [sid, second["id"]]})
    now = datetime.now(timezone.utc)
    prediction = {
        "prob_up": 0.8,
        "mu": 0.03,
        "var": 0.0004,
        "trade_success_prob": 0.75,
        "drawdown_risk": 0.02,
        "prediction_time": now.isoformat(),
        "feature_available_at": (now - timedelta(seconds=1)).isoformat(),
    }
    # Test clients may use a fixed clock; use the API's own generated lesson timestamps.
    schema = request("GET", "/openapi.json")
    prediction.update(
        schema["components"]["schemas"]["PredictionRequest"]["examples"][0]
    )
    signal = request(
        "POST",
        f"/api/v1/scenarios/{sid}/signals",
        json={"ticker": "AAPL", "prediction": prediction},
    )
    assert signal["decision_json"]["action"] == "BUY"
    passed = request(
        "POST",
        f"/api/v1/scenarios/{sid}/signals",
        json={"ticker": "MSFT", "prediction": {**prediction, "mu": -0.01}},
    )
    assert passed["decision_json"]["action"] == "PASS"
    request("GET", f"/api/v1/signals/{signal['id']}")
    entry = {
        "json": {"signal_id": signal["id"], "dollars": "100"},
        "headers": {"Idempotency-Key": f"lesson-entry-{uuid4()}"},
    }
    trade = request("POST", "/api/v1/paper-trading/trade", **entry)["trade"]
    assert (
        request("POST", "/api/v1/paper-trading/trade", **entry)["trade"]["id"]
        == trade["id"]
    )
    request(
        "GET",
        "/api/v1/market/quotes",
        params=[("tickers", "AAPL"), ("tickers", "MSFT")],
    )
    assert (
        "SYNTHETIC"
        in request("GET", "/api/v1/market/metadata/AAPL")["data"]["shortName"]
    )
    request("POST", "/api/v1/market/refresh")
    exit_path = f"/api/v1/paper-trading/close/{trade['id']}"
    exit_headers = {"Idempotency-Key": f"lesson-exit-{uuid4()}"}
    closed = request("POST", exit_path, headers=exit_headers)["trade"]
    assert closed["status"] == "closed"
    assert request("POST", exit_path, headers=exit_headers)["trade"] == closed
    history = request("GET", f"/api/v1/scenarios/{sid}/history")
    assert len(history["fills"]) == len(history["orders"]) == 2
    backtest = schema["components"]["schemas"]["BacktestRequest"]["examples"][0]
    runs = [
        request("POST", "/api/v1/backtest/run", json=backtest)["id"] for _ in range(2)
    ]
    saved = request("GET", "/api/v1/backtest/runs")["runs"]
    assert set(runs) <= {r["id"] for r in saved}
    request("POST", "/api/v1/backtest/compare", json={"ids": runs})
    return {
        "mode": "synthetic_offline",
        "scenario_id": sid,
        "trade_id": closed["id"],
        "fills": len(history["fills"]),
        "backtest_runs_created": len(runs),
        "status": "passed",
    }


def self_contained():
    """Launch an owned disposable loopback API with no external-data access."""
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="moodring-api-lesson-") as directory:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        token = secrets.token_urlsafe(32)
        env = {
            **os.environ,
            "PYTHONPATH": str(root / "src"),
            "PAPER_DATABASE_URL": f"sqlite:///{directory}/lesson.sqlite",
            "PAPER_QUOTE_MODE": "offline",
            "PAPER_ENABLE_PRICE_CHECKER": "false",
            "PAPER_API_USERS_JSON": json.dumps({token: "learner"}),
        }
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "api.server:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                deadline = time.monotonic() + 30
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Disposable API exited before readiness")
                    try:
                        if client.get("/ready", timeout=1).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Disposable API did not become ready")
                    time.sleep(0.1)
                return run_workflow(client)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--self-contained",
        action="store_true",
        help="Start a disposable offline API, run the lesson, stop it and remove its temporary ledger",
    )
    args = parser.parse_args()
    if args.self_contained:
        print(json.dumps(self_contained(), indent=2))
        return
    url = urlsplit(args.base_url)
    if (
        url.scheme != "http"
        or url.hostname not in ("127.0.0.1", "localhost", "::1")
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/")
    ):
        parser.error("Use a loopback HTTP base URL without credentials, query or path")
    token = os.getenv("PAPER_SMOKE_TOKEN", "")
    if not token:
        parser.error("Set PAPER_SMOKE_TOKEN to a configured local user token")
    # No redirects or environment proxy forwarding of the local bearer secret.
    with httpx.Client(
        base_url=args.base_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        print(json.dumps(run_workflow(client), indent=2))


if __name__ == "__main__":
    main()
