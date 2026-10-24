"""All documented operations through HTTP; synthetic providers only."""
import asyncio
import importlib.util
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.server import create_app
from api import api_models
from paper_trading.market import OfflineQuoteProvider
from conftest import Clock, Quotes, prediction

HEADERS = {"Authorization": "Bearer token"}


@pytest.fixture
def offline(tmp_path, monkeypatch):
    import yfinance

    def forbidden(*a, **kw):
        raise AssertionError("offline API attempted market network access")

    monkeypatch.setattr(yfinance, "download", forbidden)
    monkeypatch.setattr(yfinance, "Ticker", forbidden)
    app = create_app(
        f'sqlite:///{tmp_path / "api.sqlite"}',
        users={"token": "alice", "other": "bob"},
        enable_checker=False,
        quote_mode="offline",
    )
    with TestClient(app, headers=HEADERS) as client:
        yield app, client


def test_complete_documented_workflow_and_decimal_schemas(offline):
    app, client = offline
    path = Path(__file__).resolve().parents[1] / "examples/api_smoke.py"
    spec = importlib.util.spec_from_file_location("api_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.run_workflow(client)
    assert result["status"] == "passed"
    scenario = client.get(f"/api/v1/scenarios/{result['scenario_id']}").json()
    assert isinstance(scenario["portfolio"]["cash_balance"], str)
    assert Decimal(scenario["portfolio"]["cash_balance"]) < 10000  # round-trip costs
    schema = client.get("/openapi.json").json()
    for route in app.routes:
        if route.path.startswith("/api/") or route.path in ("/health", "/ready"):
            assert route.response_model is not None
            for method in route.methods:
                operation = schema["paths"][route.path][method.lower()]
                assert (
                    operation["tags"]
                    and operation["summary"]
                    and operation["description"]
                )
                assert operation["responses"][str(route.status_code or 200)]["content"][
                    "application/json"
                ]["schema"]
                if route.path.startswith("/api/"):
                    assert operation["security"] == [{"HTTPBearer": []}]
                    assert operation["responses"]["422"]["content"]["application/json"][
                        "schema"
                    ]["$ref"].endswith("/ErrorResponse")
    assert not hasattr(api_models, "TierLimitsResponse")
    assert not hasattr(api_models, "APIResponse")


def test_all_documented_routes_exist(offline):
    app, _ = offline
    doc = (Path(__file__).resolve().parents[1] / "docs/PAPER_TRADING.md").read_text()
    for method, path in re.findall(r"`(GET|POST) (/[^`? ]+)", doc):
        normalized = re.sub(r"\{[^}]+\}", "{}", path)
        assert any(
            method in getattr(r, "methods", set())
            and re.sub(r"\{[^}]+\}", "{}", r.path) == normalized
            for r in app.routes
        ), (method, path)


def test_openapi_examples_are_executable(offline):
    _, client = offline
    schema = client.get("/openapi.json").json()["components"]["schemas"]
    scenario = client.post(
        "/api/v1/scenarios", json=schema["ScenarioRequest"]["examples"][0]
    ).json()
    signal = client.post(
        f"/api/v1/scenarios/{scenario['id']}/signals",
        json=schema["SignalRequest"]["examples"][0],
    )
    assert (
        signal.status_code == 201 and signal.json()["decision_json"]["action"] == "BUY"
    )
    entry = {
        **schema["PaperTradeRequest"]["examples"][0],
        "signal_id": signal.json()["id"],
    }
    assert (
        client.post(
            "/api/v1/paper-trading/trade",
            json=entry,
            headers={"Idempotency-Key": "swagger-entry"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/backtest/run", json=schema["BacktestRequest"]["examples"][0]
        ).status_code
        == 201
    )


def test_authentication_ownership_conflicts_and_pass(offline):
    _, client = offline
    assert (
        client.get("/api/v1/scenarios", headers={"Authorization": ""}).status_code
        == 401
    )
    assert (
        client.get(
            "/api/v1/scenarios", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    scenario = client.post("/api/v1/scenarios", json={"name": "mine"}).json()
    sid = scenario["id"]
    schema = client.get("/openapi.json").json()["components"]["schemas"]
    body = schema["SignalRequest"]["examples"][0]
    signal = client.post(f"/api/v1/scenarios/{sid}/signals", json=body).json()
    key = {"Idempotency-Key": "entry"}
    entry = {"signal_id": signal["id"], "dollars": "100"}
    trade = client.post("/api/v1/paper-trading/trade", json=entry, headers=key).json()[
        "trade"
    ]
    assert (
        client.post(
            "/api/v1/paper-trading/trade", json={**entry, "dollars": "99"}, headers=key
        ).status_code
        == 409
    )
    assert client.post("/api/v1/paper-trading/trade", json=entry).status_code == 422
    for path in (
        f"/api/v1/scenarios/{sid}",
        f"/api/v1/scenarios/{sid}/history",
        f"/api/v1/signals/{signal['id']}",
    ):
        assert (
            client.get(path, headers={"Authorization": "Bearer other"}).status_code
            == 403
        )
    assert (
        client.post(
            f"/api/v1/paper-trading/close/{trade['id']}",
            headers={"Authorization": "Bearer other", "Idempotency-Key": "x"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/scenarios/{sid}/signals",
            json=body,
            headers={"Authorization": "Bearer other"},
        ).status_code
        == 403
    )
    passed = client.post(
        f"/api/v1/scenarios/{sid}/signals",
        json={**body, "prediction": {**body["prediction"], "mu": -0.01}},
    ).json()
    assert passed["decision_json"]["action"] == "PASS"
    assert (
        client.post(
            "/api/v1/paper-trading/trade",
            json={"signal_id": passed["id"]},
            headers={"Idempotency-Key": "pass"},
        ).status_code
        == 400
    )
    path = f"/api/v1/paper-trading/close/{trade['id']}"
    assert client.post(path, headers={"Idempotency-Key": "close"}).status_code == 200
    assert (
        client.post(path, headers={"Idempotency-Key": "different-close"}).status_code
        == 409
    )
    replay = client.post(
        "/api/v1/backtest/run", json=schema["BacktestRequest"]["examples"][0]
    ).json()
    other = client.post(
        "/api/v1/backtest/run", json=schema["BacktestRequest"]["examples"][0]
    ).json()
    assert (
        client.post(
            "/api/v1/backtest/compare",
            json={"ids": [replay["id"], other["id"]]},
            headers={"Authorization": "Bearer other"},
        ).status_code
        == 403
    )
    assert client.get(
        "/api/v1/backtest/runs", headers={"Authorization": "Bearer other"}
    ).json() == {"runs": []}
    assert client.get(
        "/api/v1/scenarios", headers={"Authorization": "Bearer other"}
    ).json() == {"scenarios": []}
    assert (
        client.post(
            "/api/v1/scenarios/compare", json={"ids": [sid, "unknown"]}
        ).status_code
        == 404
    )


def test_health_readiness_no_secrets_and_provider_failure(tmp_path):
    clock, quotes = Clock(), Quotes(Clock())
    app = create_app(
        f'sqlite:///{tmp_path / "secret-db.sqlite"}',
        quotes,
        users={"SECRET_TOKEN": "alice"},
        enable_checker=False,
        clock=clock,
    )
    with TestClient(app, headers={"Authorization": "Bearer SECRET_TOKEN"}) as client:
        state = client.get("/health").json()
        assert state["authentication_configured"] and state["database"] == {
            "backend": "sqlite",
            "connected": True,
        }
        assert state["quote_provider_mode"] == "custom" and not state["offline"]
        assert (
            state["checker"]["status"] == "disabled"
            and client.get("/ready").status_code == 200
        )
        client.post("/api/v1/scenarios", json={"name": "Ready"})

        def fail(*args):
            raise RuntimeError("SECRET_TOKEN /private/path signed-url?token=secret")

        quotes.get_quotes = fail
        for method, path in [
            ("GET", "/api/v1/market/quotes?tickers=AAPL"),
            ("POST", "/api/v1/market/refresh"),
        ]:
            response = client.request(method, path)
            assert response.status_code == 503
            assert response.json() == {
                "ok": False,
                "detail": "Market data temporarily unavailable",
            }
        assert client.get("/ready").status_code == 503
        assert (
            client.get("/health").json()["checker"]["last_error"]
            == "Market refresh failed"
        )
        assert (
            "SECRET_TOKEN" not in client.get("/health").text
            and "secret-db" not in client.get("/health").text
        )
        bad = client.post(
            "/api/v1/scenarios", json={"name": {"private": "SECRET_TOKEN"}}
        )
        assert bad.status_code == 422 and "SECRET_TOKEN" not in bad.text


def test_offline_fixed_prices_and_unsupported_symbols(offline):
    _, client = offline
    assert client.get("/health").json()["offline"] is True
    assert (
        client.get("/api/v1/market/quotes?tickers=AAPL").json()["quotes"]["AAPL"][
            "source"
        ]
        == "synthetic_offline"
    )
    assert (
        client.get("/api/v1/market/quotes?tickers=AAPL").json()["quotes"]["AAPL"][
            "price"
        ]
        == "100"
    )
    assert client.get("/api/v1/market/quotes?tickers=UNKNOWN").status_code == 503
    assert client.get("/api/v1/market/metadata/UNKNOWN").status_code == 503
    assert client.get("/api/v1/market/quotes?tickers=BAD%2FURL").status_code == 422


def test_unconfigured_readiness_and_invalid_modes(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPER_QUOTE_MODE", raising=False)
    with TestClient(create_app("sqlite://", users={}, enable_checker=False)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/health").json()["quote_provider_mode"] == "offline"
        assert client.get("/ready").status_code == 503
        assert client.get("/api/v1/scenarios").status_code == 503
    with pytest.raises(ValueError, match="PAPER_QUOTE_MODE"):
        create_app("sqlite://", users={}, quote_mode="typo")


def test_checker_health_lifecycle_and_database_failure(monkeypatch):
    app = create_app(
        "sqlite://", users={"token": "alice"}, enable_checker=True, quote_mode="offline"
    )
    # Before lifespan a configured checker is not actually running.
    with TestClient(app) as client:
        assert client.get("/health").json()["checker"]["status"] == "running"
        assert client.get("/ready").status_code == 200

        def broken(*a, **kw):
            raise RuntimeError("private connection URL")

        monkeypatch.setattr(
            app.state.paper_trading_service.store.engine, "connect", broken
        )
        assert client.get("/ready").status_code == 503
        assert client.get("/health").json()["database"]["connected"] is False
        assert "private connection URL" not in client.get("/health").text


def test_offline_never_constructs_external_adapter_or_metadata_cache(
    monkeypatch, tmp_path
):
    import api.server as server

    def forbidden(*a, **kw):
        raise AssertionError("external adapter constructed")

    monkeypatch.setattr(server, "YFinanceQuoteProvider", forbidden)
    monkeypatch.setattr(server, "MarketEnricher", forbidden)
    path = tmp_path / "market-cache.json"
    monkeypatch.setenv("PAPER_MARKET_CACHE", str(path))
    with TestClient(
        create_app(
            "sqlite://",
            users={"token": "alice"},
            enable_checker=False,
            quote_mode="offline",
        ),
        headers=HEADERS,
    ) as client:
        assert client.get("/api/v1/market/metadata/AAPL").status_code == 200
        assert client.get("/api/v1/market/quotes?tickers=MSFT").status_code == 200
    assert not path.exists()


def test_enabled_checker_before_start_and_after_shutdown():
    app = create_app(
        "sqlite://", users={"token": "alice"}, enable_checker=True, quote_mode="offline"
    )
    client = TestClient(app)
    assert client.get("/health").json()["checker"]["status"] == "not_started"
    assert client.get("/ready").status_code == 503
    with client:
        assert client.get("/ready").status_code == 200
    assert client.get("/health").json()["checker"]["status"] == "stopped"
    assert client.get("/ready").status_code == 503


def test_manual_refresh_records_success_and_recovers_safely(tmp_path):
    clock, provider = Clock(), Quotes(Clock())
    app = create_app(
        "sqlite://",
        provider,
        users={"token": "alice"},
        enable_checker=False,
        clock=clock,
    )
    with TestClient(app, headers=HEADERS) as client:
        provider.failure = True
        assert client.post("/api/v1/market/refresh").status_code == 503
        assert client.get("/ready").status_code == 503
        provider.failure = False
        assert client.post("/api/v1/market/refresh").status_code == 200
        state = client.get("/health").json()
        assert state["checker"]["last_success_at"] == clock().isoformat().replace(
            "+00:00", "Z"
        )
        assert state["checker"]["last_error"] is None
        assert state["last_refresh"]["tickers_requested_count"] == 0
        assert client.get("/ready").status_code == 200


def test_unexpected_error_has_documented_sanitized_json(offline, monkeypatch):
    app, _ = offline

    def broken(*a, **kw):
        raise RuntimeError("credential/private-path")

    monkeypatch.setattr(app.state.paper_trading_service, "list_scenarios", broken)
    with TestClient(app, headers=HEADERS, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/scenarios")
        assert response.status_code == 500
        assert response.json() == {"ok": False, "detail": "Local API operation failed"}


def test_mismatched_quote_is_unavailable_without_leaking_details():
    from paper_trading.market import Quote

    clock = Clock()

    class Wrong:
        def get_quotes(self, symbols):
            return {"AAPL": Quote("MSFT", 100, clock(), clock(), "wrong-provider")}

    with TestClient(
        create_app(
            "sqlite://",
            Wrong(),
            users={"token": "alice"},
            enable_checker=False,
            clock=clock,
        ),
        headers=HEADERS,
    ) as client:
        response = client.get("/api/v1/market/quotes?tickers=AAPL")
        assert response.status_code == 503
        assert response.json()["detail"] == "Market data temporarily unavailable"
