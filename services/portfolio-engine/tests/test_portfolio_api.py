import asyncio
import json
from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import main
from app.database import Base, get_db


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    database_path = tmp_path / "portfolio-test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def create_tables() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    async def empty_returns(tickers: set[str]) -> dict[str, dict[str, float]]:
        return {ticker: {} for ticker in tickers}

    asyncio.run(create_tables())
    monkeypatch.setattr(main, "_fetch_return_series", empty_returns)
    main.app.dependency_overrides[get_db] = override_get_db
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


def _trade(
    ticker: str,
    direction: str,
    stop_loss: float,
    asset_type: str,
    sector: str,
) -> dict[str, object]:
    return {
        "ticker": ticker,
        "direction": direction,
        "entry_price": 100,
        "quantity": 10,
        "stop_loss": stop_loss,
        "target_price": 120 if direction == "BUY" else 80,
        "asset_type": asset_type,
        "sector": sector,
    }


def _signal(ticker: str, optimal_size_usd: float, kelly_pct: float) -> dict[str, object]:
    return {
        "ticker": ticker,
        "direction": "BUY",
        "status": "ACTIVE",
        "trigger_price": 100,
        "stop_loss": 95,
        "target_price": 115,
        "reason": "Deterministic test signal",
        "risk_reward": 3,
        "atr_value": 2,
        "rsi_value": 55,
        "suppressed": False,
        "kelly_pct": kelly_pct,
        "optimal_size_usd": optimal_size_usd,
        "volatility_scalar": 1,
        "asset_type": "stock",
        "sector": "Technology",
    }


def test_risk_endpoint_reports_long_short_heat_exposure_and_stress(client: TestClient) -> None:
    assert client.post("/api/trades/manual", json=_trade("AAPL", "BUY", 95, "stock", "Technology")).status_code == 200
    assert client.post("/api/trades/manual", json=_trade("BTC", "SELL", 105, "crypto", "Crypto")).status_code == 200

    response = client.get("/api/portfolio/risk")
    assert response.status_code == 200
    risk = response.json()

    assert {position["ticker"]: position["risk_to_stop_usd"] for position in risk["positions"]} == {
        "AAPL": 50,
        "BTC": 50,
    }
    assert risk["heat"]["raw_risk_usd"] == 100
    assert risk["heat"]["effective_pct"] == 1
    assert risk["exposure"]["asset_class"] == {"crypto": 10, "stock": 10}
    assert risk["exposure"]["direction"] == {"LONG": 10, "SHORT": 10}
    stress = {scenario["name"]: scenario["estimated_pnl"] for scenario in risk["stress_tests"]}
    assert stress == {
        "Broad equity shock": -50,
        "Crypto shock": 200,
        "Combined risk-off shock": 150,
    }

    dashboard = client.get("/api/dashboard-summary")
    assert dashboard.status_code == 200
    assert dashboard.json()["risk"]["heat"]["raw_risk_usd"] == 100

    recommendation = client.get(
        "/api/portfolio/recommendation",
        params={"ticker": "AAPL", "current_price": 100, "sector": "Technology"},
    )
    assert recommendation.status_code == 200
    assert recommendation.json()["suggested_position_usd"] == 0
    assert recommendation.json()["risk_decision"]["action"] == "rejected"
    assert "MAX_TICKER_EXPOSURE" in {
        reason["code"] for reason in recommendation.json()["risk_decision"]["reasons"]
    }


def test_manual_long_and_short_closes_return_reserved_capital_and_pnl(client: TestClient) -> None:
    long_trade = client.post("/api/trades/manual", json=_trade("AAPL", "BUY", 95, "stock", "Technology")).json()
    short_trade = client.post("/api/trades/manual", json=_trade("BTC", "SELL", 105, "crypto", "Crypto")).json()

    closed_long = client.post(f"/api/trades/{long_trade['id']}/close", json={"exit_price": 112})
    closed_short = client.post(f"/api/trades/{short_trade['id']}/close", json={"exit_price": 88})

    assert closed_long.status_code == 200
    assert closed_short.status_code == 200
    assert closed_long.json()["pnl"] == 120
    assert closed_short.json()["pnl"] == 120
    portfolio = client.get("/api/portfolio").json()
    assert portfolio["balance"] == 10_240
    assert portfolio["equity"] == 10_240
    assert portfolio["total_pnl"] == 240
    assert portfolio["win_count"] == 2


def test_breaker_rejects_increase_but_allows_reduction(client: TestClient) -> None:
    payload = _trade("LOSS", "BUY", 95, "stock", "Industrials")
    payload["quantity"] = 40
    trade = client.post("/api/trades/manual", json=payload).json()
    assert client.post(f"/api/trades/{trade['id']}/close", json={"exit_price": 90}).status_code == 200

    proposal = {
        "ticker": "NEW",
        "direction": "BUY",
        "entry_price": 100,
        "quantity": 1,
        "stop_loss": 95,
        "asset_type": "stock",
        "sector": "Technology",
    }
    increase = client.post("/api/portfolio/risk/evaluate", json=proposal)
    reduction = client.post("/api/portfolio/risk/evaluate", json={**proposal, "intent": "reduce"})

    assert increase.status_code == 200
    assert increase.json()["approved"] is False
    assert increase.json()["reasons"][0]["code"] == "CIRCUIT_BREAKER_ACTIVE"
    assert increase.json()["breaker"]["daily_loss_active"] is True
    assert reduction.status_code == 200
    assert reduction.json()["approved"] is True
    assert reduction.json()["action"] == "approve_reduction"


def test_invalid_risk_setting_values_are_rejected_or_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for value in (0, -0.01, 1.01, "not-a-number"):
        with pytest.raises(HTTPException):
            asyncio.run(main.update_env_setting({"key": "MAX_PORTFOLIO_HEAT_PCT", "value": value}))

    monkeypatch.setattr(main, "_find_env_path", lambda: None)
    monkeypatch.setattr(main, "_read_env", lambda path: {"MAX_PORTFOLIO_HEAT_PCT": "-1"})
    assert main._risk_limits().max_portfolio_heat_pct == main.settings.MAX_PORTFOLIO_HEAT_PCT


def test_process_signal_persists_reduced_and_rejected_risk_decisions(client: TestClient) -> None:
    reduced = client.post("/api/process-signal", json=_signal("AAPL", 3_000, 20))
    rejected = client.post("/api/process-signal", json=_signal("MSFT", 3_000, 0))
    suppressed_payload = {**_signal("OLD", 500, 5), "suppressed": True, "status": "STALE"}
    suppressed = client.post("/api/process-signal", json=suppressed_payload)

    assert reduced.status_code == 200
    assert reduced.json()["approved"] is True
    assert reduced.json()["optimal_size_usd"] == 1_000
    assert reduced.json()["risk_decision"]["action"] == "reduced"
    assert reduced.json()["risk_decision"]["reasons"][0]["code"] == "FRACTIONAL_KELLY_CAP"
    assert rejected.status_code == 200
    assert rejected.json()["approved"] is False
    assert rejected.json()["optimal_size_usd"] == 0
    assert rejected.json()["risk_decision"]["action"] == "rejected"
    assert "MAX_TICKER_EXPOSURE" in {
        reason["code"] for reason in rejected.json()["risk_decision"]["reasons"]
    }
    assert suppressed.status_code == 200
    assert suppressed.json()["approved"] is False
    assert suppressed.json()["optimal_size_usd"] == 0
    assert suppressed.json()["risk_decision"]["reasons"][0]["code"] == "SIGNAL_SUPPRESSED"

    alerts = client.get("/api/alerts").json()
    assert len(alerts) == 3
    persisted = {alert["ticker"]: json.loads(alert["risk_decision_json"]) for alert in alerts}
    assert persisted["AAPL"]["action"] == "reduced"
    assert persisted["MSFT"]["action"] == "rejected"
    assert persisted["OLD"]["recommended_size_usd"] == 0


def test_trade_and_signal_persist_regime_traceability(client: TestClient) -> None:
    payload = _trade("AAPL", "BUY", 95, "stock", "Technology")
    payload["market_regime"] = {
        "trend": "bull",
        "volatility": "low",
        "breadth": "strong",
        "risk": "risk_on",
        "label": "Bull · Low Vol · Strong Breadth · Risk On",
    }
    payload["timeframe_agreement"] = {"score": 80}

    trade = client.post("/api/trades/manual", json=payload)

    assert trade.status_code == 200
    assert trade.json()["market_regime"] == "bull"
    assert trade.json()["risk_regime"] == "risk_on"
    assert trade.json()["timeframe_agreement"] == 80
    persisted_trade = client.get("/api/trades").json()[0]
    assert persisted_trade["regime_label"] == "Bull · Low Vol · Strong Breadth · Risk On"

    signal_payload = _signal("MSFT", 1_000, 5)
    signal_payload["market_regime"] = payload["market_regime"]
    signal_payload["timeframe_agreement"] = {"score": 75}
    decision = client.post("/api/process-signal", json=signal_payload)
    assert decision.status_code == 200
    alerts = client.get("/api/alerts").json()
    assert alerts[0]["market_regime"] == "bull"
    assert alerts[0]["breadth_regime"] == "strong"
    assert alerts[0]["timeframe_agreement"] == 75


def test_missing_regime_metadata_uses_compatible_unknown_defaults(client: TestClient) -> None:
    trade = client.post(
        "/api/trades/manual",
        json=_trade("BTC", "BUY", 95, "crypto", "Crypto"),
    )

    assert trade.status_code == 200
    assert trade.json()["market_regime"] == "unknown"
    assert trade.json()["regime_label"] == "Unknown"
    assert trade.json()["timeframe_agreement"] is None
