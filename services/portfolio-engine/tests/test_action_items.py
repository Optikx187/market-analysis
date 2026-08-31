import asyncio
import datetime
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import action_items as actions
from app import main
from app.database import Base, get_db
from app.models import ActionItem

SCANNER_STATUS: dict[str, object] = {
    "last_scan_result": {
        "timestamp": "2026-01-02T00:00:00+00:00",
        "signals": [
            {
                "ticker": "AAPL",
                "reason": "Momentum breakout with trend agreement",
                "market_regime": {
                    "label": "Trending / normal vol",
                    "trend": "up",
                    "volatility": "normal",
                    "breadth": "broad",
                    "risk": "on",
                },
                "opportunity": {
                    "id": "AAPL:BUY",
                    "ticker": "AAPL",
                    "direction": "BUY",
                    "score": 78.5,
                    "eligible": True,
                    "user_decision": "pending",
                    "evaluated_at": "2026-01-02T00:00:00+00:00",
                    "trade_plan": {"position_size_usd": 2500, "stop_loss": 94.0},
                    "regime": {"market_regime": "trending", "volatility_regime": "normal", "breadth": "broad", "risk": "on"},
                },
            },
            {
                "ticker": "MSFT",
                "opportunity": {"id": "MSFT:BLOCKED", "ticker": "MSFT", "eligible": False},
            },
        ],
    },
}

DATA_QUALITY: dict[str, object] = {
    "AAPL": {"status": "ok", "is_eligible": True, "stale": False, "issues": []},
    "TSLA": {
        "status": "blocked",
        "is_eligible": False,
        "stale": True,
        "age_hours": 96.0,
        "candle_count": 12,
        "issues": ["Candles are 96h old", "Gap detected"],
    },
}

EARNINGS: dict[str, object] = {
    "upcoming": [
        {"ticker": "AAPL", "earnings_date": "2026-01-05", "days_until": 3},
        {"ticker": "NVDA", "earnings_date": "2026-03-01", "days_until": 45},
    ],
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A client factory over one on-disk SQLite file so restarts can be simulated."""
    database_path = tmp_path / "action-items.db"
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

    async def scanner_status() -> dict[str, object]:
        return SCANNER_STATUS

    async def data_quality() -> dict[str, object]:
        return DATA_QUALITY

    async def earnings() -> dict[str, object]:
        return EARNINGS

    async def data_status() -> dict[str, object]:
        return {
            "connectivity": {
                "yahoo": {
                    "online": True,
                    "last_checked": None,
                    "last_online": None,
                    "last_offline": None,
                }
            }
        }

    async def candles(ticker: str, interval: str) -> list[dict[str, object]]:
        return [{"close": 92.0}]

    asyncio.run(create_tables())
    monkeypatch.setattr(main, "_fetch_return_series", empty_returns)
    monkeypatch.setattr(main, "_fetch_scanner_status", scanner_status)
    monkeypatch.setattr(main, "_fetch_data_quality", data_quality)
    monkeypatch.setattr(main, "_fetch_upcoming_earnings", earnings)
    monkeypatch.setattr(main, "_fetch_data_status", data_status)
    monkeypatch.setattr(main, "_fetch_candles", candles)
    main.app.dependency_overrides[get_db] = override_get_db

    class Env:
        sessions = session_factory

        def client(self) -> TestClient:
            return TestClient(main.app)

    yield Env()
    main.app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


@pytest.fixture
def client(env) -> Iterator[TestClient]:
    yield env.client()


def _manual_trade(ticker: str = "AAPL") -> dict[str, object]:
    return {
        "ticker": ticker,
        "direction": "BUY",
        "entry_price": 100,
        "quantity": 10,
        "stop_loss": 90,
        "target_price": 130,
        "asset_type": "stock",
        "sector": "Technology",
    }


def _by_key(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    return {str(item["source_key"]): item for item in payload["items"]}  # type: ignore[index]


# --- candidate builders -------------------------------------------------------


def test_opportunity_candidates_skip_ineligible_and_decided():
    candidates = actions.build_opportunity_candidates(SCANNER_STATUS)
    assert [candidate.source_key for candidate in candidates] == ["opportunity:AAPL:BUY"]
    candidate = candidates[0]
    assert candidate.deep_link == {"tab": "scanner", "ticker": "AAPL", "opportunity_id": "AAPL:BUY"}
    assert candidate.is_mandatory is False

    decided = {"last_scan_result": {"signals": [
        {"ticker": "AAPL", "opportunity": {"id": "AAPL:BUY", "ticker": "AAPL", "eligible": True, "user_decision": "approved"}},
    ]}}
    assert actions.build_opportunity_candidates(decided) == []


@pytest.mark.parametrize(
    ("close", "expected_severity"),
    [(92.0, actions.SEVERITY_WARNING), (89.0, actions.SEVERITY_CRITICAL), (96.0, None)],
)
def test_stop_proximity_threshold_is_deterministic(close, expected_severity):
    trades = [{"id": 7, "ticker": "AAPL", "direction": "BUY", "entry_price": 100.0, "stop_loss": 90.0}]
    candidates = actions.build_stop_proximity_candidates(trades, {"AAPL": close}, 0.25)
    if expected_severity is None:
        assert candidates == []
        return
    candidate = candidates[0]
    assert candidate.severity == expected_severity
    assert candidate.source_key == "stop_proximity:trade:7"
    assert candidate.deep_link == {"tab": "trades", "ticker": "AAPL", "trade_id": 7}
    assert candidate.payload["threshold_pct"] == 0.25


def test_stop_proximity_skips_missing_inputs():
    trades = [
        {"id": 1, "ticker": "AAPL", "direction": "BUY", "entry_price": 100.0, "stop_loss": 90.0},
        {"id": 2, "ticker": "MSFT", "direction": "BUY", "entry_price": 100.0, "stop_loss": None},
        {"id": 3, "ticker": "NVDA", "direction": "BUY", "entry_price": 100.0, "stop_loss": 100.0},
    ]
    closes: dict[str, float | None] = {"AAPL": None, "MSFT": 95.0, "NVDA": 95.0}
    assert actions.build_stop_proximity_candidates(trades, closes, 0.25) == []


def test_short_trade_stop_proximity_uses_inverted_distance():
    trades = [{"id": 4, "ticker": "BTC", "direction": "SELL", "entry_price": 100.0, "stop_loss": 110.0}]
    candidates = actions.build_stop_proximity_candidates(trades, {"BTC": 108.0}, 0.25)
    assert candidates[0].payload["remaining_pct"] == pytest.approx(0.2)


def test_data_quality_candidates_flag_blocked_as_mandatory():
    candidates = actions.build_data_quality_candidates(DATA_QUALITY)
    assert [candidate.source_key for candidate in candidates] == ["data_quality:TSLA"]
    candidate = candidates[0]
    assert candidate.severity == actions.SEVERITY_CRITICAL
    assert candidate.is_mandatory is True
    assert candidate.deep_link["section"] == "data-quality"
    assert "Gap detected" in candidate.message


def test_data_quality_handles_malformed_issues():
    candidates = actions.build_data_quality_candidates({"AAPL": {"status": "warning", "issues": "oops"}})
    assert candidates[0].payload["issues"] == []


def test_earnings_candidates_respect_window():
    candidates = actions.build_earnings_candidates(EARNINGS, 7)
    assert [candidate.source_key for candidate in candidates] == ["earnings:AAPL"]
    assert candidates[0].payload["days_until"] == 3
    assert actions.build_earnings_candidates(EARNINGS, 1) == []


def test_breaker_candidates_are_mandatory_per_flag():
    breaker = {
        "active": True,
        "daily_loss_active": True,
        "drawdown_active": True,
        "weekly_loss_active": False,
        "reasons": ["Daily loss 3.2% exceeds 2.0%"],
        "current_drawdown_pct": 12.0,
    }
    candidates = actions.build_breaker_candidates(breaker)
    assert sorted(candidate.source_key for candidate in candidates) == [
        "risk_breaker:daily_loss",
        "risk_breaker:drawdown",
    ]
    assert all(candidate.is_mandatory for candidate in candidates)
    assert actions.build_breaker_candidates({"active": False, "daily_loss_active": True}) == []


def test_order_review_candidates_only_cover_terminal_failures():
    orders = [
        {"id": 1, "ticker": "AAPL", "status": "rejected", "reject_reason": "Breaker active"},
        {"id": 2, "ticker": "AAPL", "status": "filled"},
        {"id": 3, "ticker": "BTC", "status": "expired"},
    ]
    candidates = actions.build_order_review_candidates(orders)
    assert [candidate.order_id for candidate in candidates] == [1, 3]
    assert candidates[0].deep_link == {"tab": "orders", "ticker": "AAPL", "order_id": 1}


def test_payload_hash_is_stable_and_sensitive():
    first = actions.operational_candidate("quant", "boom")
    assert first.payload_hash == actions.operational_candidate("quant", "boom").payload_hash
    assert first.payload_hash != actions.operational_candidate("quant", "other").payload_hash


def test_widget_normalization_keeps_order_and_drops_unknown():
    widgets = actions.normalize_widgets([{"id": "heat", "enabled": False}, {"id": "nope"}, {"id": "heat"}])
    assert widgets[0] == {"id": "heat", "enabled": False}
    assert [widget["id"] for widget in widgets] == ["heat", *[w for w in actions.WIDGET_IDS if w != "heat"]]
    assert actions.normalize_mode("nonsense") == "detailed"
    assert actions.normalize_mode("compact") == "compact"


def test_expired_snooze_helper():
    now = datetime.datetime(2026, 1, 1, 12, 0)
    assert actions.expired_snooze(actions.STATUS_SNOOZED, now - datetime.timedelta(minutes=1), now) is True
    assert actions.expired_snooze(actions.STATUS_SNOOZED, now + datetime.timedelta(minutes=1), now) is False
    assert actions.expired_snooze(actions.STATUS_OPEN, None, now) is False


# --- aggregation API ----------------------------------------------------------


def test_refresh_aggregates_sources_with_deep_links(client):
    client.post("/api/trades/manual", json=_manual_trade())
    payload = client.post("/api/action-items/refresh").json()
    items = _by_key(payload)

    assert "opportunity:AAPL:BUY" in items
    assert "data_quality:TSLA" in items
    assert "earnings:AAPL" in items
    assert any(key.startswith("stop_proximity:trade:") for key in items)
    for item in items.values():
        assert item["deep_link"]["tab"] in {"scanner", "trades", "orders", "settings"}  # type: ignore[index]
    assert payload["counts"]["unresolved"] == len(items)
    assert payload["refreshed"]["created"] == len(items)


def test_refresh_is_idempotent_for_active_sources(client):
    client.post("/api/trades/manual", json=_manual_trade())
    first = client.post("/api/action-items/refresh").json()
    second = client.post("/api/action-items/refresh").json()

    assert second["refreshed"] == {"created": 0, "updated": first["refreshed"]["created"], "cleared": 0}
    assert len(second["items"]) == len(first["items"])
    assert [item["id"] for item in second["items"]] == [item["id"] for item in first["items"]]


def test_state_survives_restart_and_reload(env):
    with env.client() as client:
        client.post("/api/action-items/refresh")
        item = _by_key(client.get("/api/action-items").json())["earnings:AAPL"]
        assert client.post(f"/api/action-items/{item['id']}/acknowledge").status_code == 200

    with env.client() as restarted:
        payload = restarted.get("/api/action-items?status=acknowledged").json()
        acknowledged = _by_key(payload)["earnings:AAPL"]
        assert acknowledged["id"] == item["id"]
        assert acknowledged["acknowledged_at"] is not None
        # Re-aggregating after the restart keeps the acknowledgement and adds no duplicate.
        refreshed = restarted.post("/api/action-items/refresh").json()
        assert refreshed["refreshed"]["created"] == 0
        assert _by_key(refreshed)["earnings:AAPL"]["status"] == "acknowledged"


def test_transitions_are_retry_safe_and_audited(client):
    client.post("/api/action-items/refresh")
    item_id = _by_key(client.get("/api/action-items").json())["earnings:AAPL"]["id"]

    first = client.post(f"/api/action-items/{item_id}/acknowledge").json()
    repeat = client.post(f"/api/action-items/{item_id}/acknowledge").json()
    assert repeat["status"] == "acknowledged"
    assert repeat["acknowledged_at"] == first["acknowledged_at"]

    snoozed = client.post(f"/api/action-items/{item_id}/snooze", json={"minutes": 30}).json()
    assert snoozed["status"] == "snoozed"
    assert snoozed["snoozed_until"] > snoozed["snoozed_at"]

    resolved = client.post(f"/api/action-items/{item_id}/resolve").json()
    repeat_resolved = client.post(f"/api/action-items/{item_id}/resolve").json()
    assert resolved["resolved_at"] == repeat_resolved["resolved_at"]
    assert resolved["snoozed_until"] is None

    reopened = client.post(f"/api/action-items/{item_id}/reopen").json()
    assert reopened["status"] == "open"
    assert reopened["resolved_at"] is None

    assert client.post("/api/action-items/9999/acknowledge").status_code == 404
    assert client.post(f"/api/action-items/{item_id}/snooze", json={"minutes": 0}).status_code == 422


def test_expired_snooze_returns_to_open(env):
    client = env.client()
    client.post("/api/action-items/refresh")
    item_id = _by_key(client.get("/api/action-items").json())["earnings:AAPL"]["id"]
    client.post(f"/api/action-items/{item_id}/snooze", json={"minutes": 60})

    async def expire() -> None:
        async with env.sessions() as session:
            item = (await session.execute(select(ActionItem).where(ActionItem.id == item_id))).scalar_one()
            item.snoozed_until = datetime.datetime.utcnow() - datetime.timedelta(minutes=5)
            await session.commit()

    asyncio.run(expire())
    assert client.get(f"/api/action-items/{item_id}").json()["status"] == "open"


def test_mandatory_items_ignore_ordinary_filters(client):
    client.post("/api/action-items/refresh")
    filtered = client.get("/api/action-items?category=opportunity").json()
    keys = _by_key(filtered)
    assert "opportunity:AAPL:BUY" in keys
    assert keys["data_quality:TSLA"]["is_mandatory"] is True
    assert "earnings:AAPL" not in keys

    severity_filtered = _by_key(client.get("/api/action-items?severity=info").json())
    assert "data_quality:TSLA" in severity_filtered

    source_filtered = _by_key(client.get("/api/action-items?source_type=earnings").json())
    assert "data_quality:TSLA" in source_filtered
    assert filtered["mandatory_note"]
    assert client.get("/api/action-items?category=nonsense").status_code == 400


def test_resolved_source_clears_and_reopens_when_condition_changes(client, monkeypatch):
    client.post("/api/action-items/refresh")

    async def quiet_earnings() -> dict[str, object]:
        return {"upcoming": []}

    monkeypatch.setattr(main, "_fetch_upcoming_earnings", quiet_earnings)
    cleared = client.post("/api/action-items/refresh").json()
    assert cleared["refreshed"]["cleared"] >= 1
    resolved = _by_key(client.get("/api/action-items?status=resolved").json())["earnings:AAPL"]
    assert resolved["source_active"] is False

    async def changed_earnings() -> dict[str, object]:
        return {"upcoming": [{"ticker": "AAPL", "earnings_date": "2026-01-04", "days_until": 2}]}

    monkeypatch.setattr(main, "_fetch_upcoming_earnings", changed_earnings)
    client.post("/api/action-items/refresh")
    reopened = _by_key(client.get("/api/action-items").json())["earnings:AAPL"]
    assert reopened["status"] == "open"
    assert reopened["id"] == resolved["id"]


def test_resolved_source_reopens_when_unchanged_source_returns(client, monkeypatch):
    client.post("/api/action-items/refresh")

    async def quiet_earnings() -> dict[str, object]:
        return {"upcoming": []}

    monkeypatch.setattr(main, "_fetch_upcoming_earnings", quiet_earnings)
    client.post("/api/action-items/refresh")
    resolved = _by_key(client.get("/api/action-items?status=resolved").json())["earnings:AAPL"]

    async def restored_earnings() -> dict[str, object]:
        return EARNINGS

    monkeypatch.setattr(main, "_fetch_upcoming_earnings", restored_earnings)
    client.post("/api/action-items/refresh")
    reopened = _by_key(client.get("/api/action-items").json())["earnings:AAPL"]
    assert reopened["status"] == "open"
    assert reopened["source_active"] is True
    assert reopened["id"] == resolved["id"]


def test_service_failure_creates_operational_item_without_failing(client, monkeypatch):
    async def broken_scanner() -> dict[str, object]:
        return {"error": "Connection refused"}

    monkeypatch.setattr(main, "_fetch_scanner_status", broken_scanner)
    payload = client.post("/api/action-items/refresh").json()
    assert payload["items"]
    operational = _by_key(payload)["operational:quant-engine scanner"]
    assert operational["category"] == "operations"
    assert operational["deep_link"] == {"tab": "settings", "section": "system-health"}
    assert "opportunity:AAPL:BUY" not in _by_key(payload)


def test_user_isolation(env, monkeypatch):
    def header_user(request) -> str | None:
        return request.headers.get("X-Test-User")

    monkeypatch.setattr(main, "get_current_user", header_user)
    client = env.client()
    client.post("/api/action-items/refresh", headers={"X-Test-User": "alice"})
    alice = _by_key(client.get("/api/action-items", headers={"X-Test-User": "alice"}).json())
    bob = client.get("/api/action-items", headers={"X-Test-User": "bob"}).json()
    assert alice
    assert bob["items"] == []
    assert bob["user_key"] == "bob"

    item_id = alice["earnings:AAPL"]["id"]
    assert client.post(
        f"/api/action-items/{item_id}/acknowledge", headers={"X-Test-User": "bob"}
    ).status_code == 404

    client.put(
        "/api/dashboard-preferences",
        json={"widgets": [{"id": "heat", "enabled": True}], "mode": "compact"},
        headers={"X-Test-User": "alice"},
    )
    bob_preferences = client.get("/api/dashboard-preferences", headers={"X-Test-User": "bob"}).json()
    assert bob_preferences["mode"] == "detailed"
    assert bob_preferences["widgets"][0]["id"] == actions.WIDGET_IDS[0]


# --- dashboard preferences ----------------------------------------------------


def test_dashboard_preferences_defaults_and_persistence(env):
    with env.client() as client:
        defaults = client.get("/api/dashboard-preferences").json()
        assert [widget["id"] for widget in defaults["widgets"]] == list(actions.WIDGET_IDS)
        assert defaults["available_widgets"] == list(actions.WIDGET_IDS)

        reordered = [{"id": "heat", "enabled": False}, {"id": "pnl", "enabled": True}]
        saved = client.put("/api/dashboard-preferences", json={"widgets": reordered, "mode": "compact"}).json()
        assert saved["widgets"][:2] == [{"id": "heat", "enabled": False}, {"id": "pnl", "enabled": True}]
        assert saved["mode"] == "compact"

    with env.client() as restarted:
        persisted = restarted.get("/api/dashboard-preferences").json()
        assert persisted["widgets"][0] == {"id": "heat", "enabled": False}
        assert persisted["mode"] == "compact"
        assert restarted.post("/api/dashboard-preferences/reset").json()["mode"] == "detailed"


def test_dashboard_preference_validation(client):
    assert client.put(
        "/api/dashboard-preferences", json={"widgets": [{"id": "ghost", "enabled": True}], "mode": "detailed"}
    ).status_code == 400
    assert client.put(
        "/api/dashboard-preferences", json={"widgets": [{"id": "heat", "enabled": True}], "mode": "spacious"}
    ).status_code == 400


def test_saved_layouts_round_trip(env):
    with env.client() as client:
        layout = {"widgets": [{"id": "pnl", "enabled": True}, {"id": "heat", "enabled": False}], "mode": "compact"}
        saved = client.put("/api/dashboard-preferences/layouts/Mobile", json=layout).json()
        assert saved["layouts"]["Mobile"]["mode"] == "compact"
        assert saved["layouts"]["Mobile"]["widgets"][1] == {"id": "heat", "enabled": False}
        assert client.put("/api/dashboard-preferences/layouts/%20", json=layout).status_code == 400
        assert client.put(
            "/api/dashboard-preferences/layouts/Mobile",
            json={"widgets": [{"id": "pnl", "enabled": True}], "mode": "wide"},
        ).status_code == 400

    with env.client() as restarted:
        assert "Mobile" in restarted.get("/api/dashboard-preferences").json()["layouts"]
        assert restarted.delete("/api/dashboard-preferences/layouts/Mobile").json()["layouts"] == {}
        assert restarted.delete("/api/dashboard-preferences/layouts/Mobile").status_code == 404


# --- dashboard summary --------------------------------------------------------


def test_dashboard_summary_exposes_widget_inputs(client):
    client.post("/api/action-items/refresh")
    summary = client.get("/api/dashboard-summary").json()

    assert summary["cash"]["available"] is True
    assert summary["regime"]["available"] is True
    assert summary["regime"]["scanned_at"] == "2026-01-02T00:00:00+00:00"
    assert summary["top_opportunities"]["items"][0]["ticker"] == "AAPL"
    assert summary["provider_health"]["available"] is True
    assert summary["provider_health"]["connectivity"]["yahoo"]["online"] is True
    assert summary["action_counts"]["unresolved"] >= 1


def test_dashboard_summary_fails_closed_on_provider_errors(client, monkeypatch):
    async def broken() -> dict[str, object]:
        return {"error": "Connection refused"}

    monkeypatch.setattr(main, "_fetch_scanner_status", broken)
    monkeypatch.setattr(main, "_fetch_data_status", broken)
    summary = client.get("/api/dashboard-summary").json()

    assert summary["regime"] == {"available": False, "reason": "Connection refused"}
    assert summary["top_opportunities"]["available"] is False
    assert summary["top_opportunities"]["items"] == []
    assert summary["provider_health"]["available"] is False
