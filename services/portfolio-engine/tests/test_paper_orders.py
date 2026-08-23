import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import main
from app import paper_orders as paper
from app.database import Base, _migrate_existing_tables, get_db

# Frictions are pinned per request so every expected fill price is exact:
# a 1% one-way cost and a 1% volume participation cap.
FRICTION = {"spread_pct": 0.0, "slippage_pct": 0.01, "participation_pct": 0.01, "fee_pct": 0.0}


@contextmanager
def _service(database_path, monkeypatch) -> Iterator[TestClient]:
    """A portfolio-engine client bound to ``database_path`` using the real migration path."""
    url = f"sqlite+aiosqlite:///{database_path}"
    engine = create_async_engine(url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def prepare() -> None:
        """Create/upgrade the schema the way ``init_db`` does, on its own engine."""
        setup_engine = create_async_engine(url)
        async with setup_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.run_sync(_migrate_existing_tables)
        await setup_engine.dispose()

    async def override_get_db():
        async with session_factory() as session:
            yield session

    async def no_candles(ticker: str, interval: str) -> list[dict[str, object]]:
        return []

    asyncio.run(prepare())
    monkeypatch.setattr(main, "_fetch_candles", no_candles)
    main.app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    with _service(tmp_path / "paper-orders-test.db", monkeypatch) as test_client:
        yield test_client


def _candle(day: int, open_: float, high: float, low: float, close: float, volume: float) -> dict:
    return {
        "timestamp": f"2024-01-{day:02d}T00:00:00",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _create(client: TestClient, **overrides) -> dict:
    payload = {
        "idempotency_key": overrides.pop("idempotency_key", "key-1"),
        "ticker": overrides.pop("ticker", "AAPL"),
        "side": overrides.pop("side", "BUY"),
        "order_type": overrides.pop("order_type", "market"),
        "quantity": overrides.pop("quantity", 10),
    }
    payload.update(overrides)
    response = client.post("/api/paper-orders", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _process(client: TestClient, ticker: str, candles: list[dict], **overrides) -> dict:
    response = client.post(
        "/api/paper-orders/process",
        json={"ticker": ticker, "candles": candles, **FRICTION, **overrides},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _order(client: TestClient, order_id: int) -> dict:
    response = client.get(f"/api/paper-orders/{order_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _portfolio(client: TestClient) -> dict:
    response = client.get("/api/portfolio")
    assert response.status_code == 200, response.text
    return response.json()


def _event_types(order: dict) -> list[str]:
    return [event["event_type"] for event in order["events"]]


def _long_position(client: TestClient, ticker: str, quantity: float, price: float) -> dict:
    response = client.post(
        "/api/trades/manual",
        json={
            "ticker": ticker,
            "direction": "BUY",
            "entry_price": price,
            "quantity": quantity,
            "stop_loss": price * 0.9,
            "asset_type": "crypto" if ticker == "BTC" else "stock",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_mode_endpoint_never_advertises_a_live_path(client: TestClient) -> None:
    mode = client.get("/api/paper-orders/mode").json()
    assert mode["mode"] == "paper"
    assert mode["live_trading_enabled"] is False


def test_duplicate_idempotency_key_returns_the_original_order(client: TestClient) -> None:
    payload = {
        "idempotency_key": "retry-me",
        "ticker": "AAPL",
        "side": "BUY",
        "order_type": "market",
        "quantity": 10,
        "reference_price": 110,
    }
    first = client.post("/api/paper-orders", json=payload)
    second = client.post("/api/paper-orders", json=payload)
    assert first.status_code == 201 and second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["events"] == first.json()["events"]

    listing = client.get("/api/paper-orders").json()
    assert len(listing["orders"]) == 1
    # One reservation only: 10 * 110 held aside from a 10,000 starting balance.
    assert _portfolio(client)["balance"] == 8900.0
    assert _portfolio(client)["equity"] == 10000.0


def test_market_buy_partial_fills_track_cash_position_and_weighted_average(client: TestClient) -> None:
    order = _create(
        client,
        idempotency_key="aapl-market",
        quantity=10,
        reference_price=110,
        order_type="market",
    )
    assert order["status"] == "submitted"
    assert order["reserved_cash"] == 1100.0
    assert _portfolio(client)["balance"] == 8900.0

    # Volume 500 with a 1% participation cap only allows five shares this candle.
    _process(client, "AAPL", [_candle(2, 100, 101, 99, 100.5, 500)])
    partial = _order(client, order["id"])
    assert partial["status"] == "partially_filled"
    assert partial["filled_quantity"] == 5
    assert partial["remaining_quantity"] == 5
    assert partial["average_fill_price"] == 101.0
    assert partial["reserved_cash"] == 550.0
    assert _portfolio(client)["balance"] == 8945.0

    _process(client, "AAPL", [_candle(3, 102, 103, 101.5, 102.5, 2000)])
    filled = _order(client, order["id"])
    assert filled["status"] == "filled"
    assert filled["remaining_quantity"] == 0
    assert [fill["price"] for fill in filled["fills"]] == [101.0, 103.02]
    assert filled["average_fill_price"] == 102.01
    assert filled["reserved_cash"] == 0.0
    assert _portfolio(client)["balance"] == 8979.9

    trades = client.get("/api/trades").json()
    assert len(trades) == 1
    assert trades[0]["quantity"] == 10
    assert trades[0]["entry_price"] == 102.01

    # Replaying the same candles must not fill anything twice.
    _process(client, "AAPL", [_candle(2, 100, 101, 99, 100.5, 500), _candle(3, 102, 103, 101.5, 102.5, 2000)])
    assert _order(client, order["id"])["filled_quantity"] == 10
    assert len(client.get(f"/api/paper-orders/{order['id']}/fills").json()["fills"]) == 2

    reconcile = client.get("/api/paper-orders/reconcile").json()
    assert reconcile["fills_match_orders"] is True
    assert reconcile["equity_balanced"] is True
    assert reconcile["filled_quantity"] == 10

    # The reported components are cent-exact, so the displayed sum is the reported total.
    components = ["balance", "position_capital", "reserved_cash"]
    assert sum(paper.to_cents(reconcile[key]) for key in components) == paper.to_cents(
        reconcile["expected_equity"]
    )
    assert abs(paper.to_cents(reconcile["equity"]) - paper.to_cents(reconcile["expected_equity"])) <= 1


def test_buy_limit_never_fills_worse_than_its_limit(client: TestClient) -> None:
    order = _create(
        client,
        idempotency_key="aapl-limit",
        order_type="limit",
        quantity=1,
        limit_price=100,
    )
    assert order["reserved_cash"] == 100.0

    _process(client, "AAPL", [_candle(2, 105, 106, 101, 104, 10_000)])
    resting = _order(client, order["id"])
    assert resting["status"] == "submitted"
    assert resting["filled_quantity"] == 0
    assert resting["events"][-1]["detail"]["reason"] == "limit_not_marketable"

    # The open plus friction is above the limit, so the fill is capped at the limit.
    _process(client, "AAPL", [_candle(3, 100.5, 101, 99.5, 100.2, 10_000)])
    filled = _order(client, order["id"])
    assert filled["status"] == "filled"
    assert filled["fills"][0]["price"] == 100.0
    assert filled["fills"][0]["price"] <= filled["limit_price"]
    assert _portfolio(client)["balance"] == 9900.0


def test_sell_stop_only_fills_once_the_stop_is_crossed(client: TestClient) -> None:
    _long_position(client, "AAPL", quantity=10, price=100)
    order = _create(
        client,
        idempotency_key="aapl-stop",
        side="SELL",
        order_type="stop",
        quantity=5,
        stop_price=95,
    )

    _process(client, "AAPL", [_candle(2, 97, 98, 96, 97, 10_000)])
    working = _order(client, order["id"])
    assert working["triggered"] is False
    assert working["filled_quantity"] == 0
    assert working["events"][-1]["detail"]["reason"] == "stop_not_crossed"

    _process(client, "AAPL", [_candle(3, 94, 95, 90, 92, 10_000)])
    filled = _order(client, order["id"])
    assert filled["triggered"] is True
    assert filled["status"] == "filled"
    assert filled["fills"][0]["price"] == 93.06
    assert "triggered" in _event_types(filled)

    trade = client.get("/api/trades").json()[0]
    assert trade["status"] == "OPEN"
    assert trade["realized_quantity"] == 5
    # 9,000 cash + 500 released capital - 34.70 price loss - 4.70 exit slippage.
    assert _portfolio(client)["balance"] == 9460.6


def test_stop_limit_can_trigger_without_filling(client: TestClient) -> None:
    order = _create(
        client,
        idempotency_key="aapl-stop-limit",
        order_type="stop_limit",
        quantity=1,
        stop_price=105,
        limit_price=105.1,
    )
    assert order["reservation_price"] == 105.1

    _process(client, "AAPL", [_candle(2, 105.5, 106, 105.3, 105.9, 10_000)])
    triggered = _order(client, order["id"])
    assert triggered["triggered"] is True
    assert triggered["status"] == "submitted"
    assert triggered["fills"] == []
    assert triggered["events"][-1]["detail"]["reason"] == "limit_not_marketable"

    _process(client, "AAPL", [_candle(3, 105, 105.2, 104.5, 104.8, 10_000)])
    filled = _order(client, order["id"])
    assert filled["status"] == "filled"
    assert filled["fills"][0]["price"] == 105.1


def test_bracket_children_activate_and_one_exit_cancels_its_sibling(client: TestClient) -> None:
    parent = _create(
        client,
        idempotency_key="aapl-bracket",
        order_type="bracket",
        quantity=4,
        reference_price=100,
        take_profit_price=120,
        stop_loss_price=90,
    )
    assert parent["role"] == "entry"
    assert [child["status"] for child in parent["children"]] == ["pending", "pending"]

    _process(client, "AAPL", [_candle(2, 100, 101, 99, 100.8, 10_000)])
    entered = _order(client, parent["id"])
    assert entered["status"] == "filled"
    assert [child["status"] for child in entered["children"]] == ["submitted", "submitted"]
    assert "children_activated" in _event_types(entered)

    _process(client, "AAPL", [_candle(3, 118, 121, 117, 120.5, 10_000)])
    children = {child["role"]: child for child in _order(client, parent["id"])["children"]}
    take_profit = _order(client, children["take_profit"]["id"])
    stop_loss = _order(client, children["stop_loss"]["id"])
    assert take_profit["status"] == "filled"
    assert take_profit["fills"][0]["price"] == 120.0
    assert stop_loss["status"] == "canceled"
    assert "oco_canceled" in _event_types(stop_loss)

    # The canceled sibling can never fill, even when its stop is crossed later.
    _process(client, "AAPL", [_candle(4, 89, 90, 85, 86, 10_000)])
    assert _order(client, stop_loss["id"])["filled_quantity"] == 0

    trades = client.get("/api/trades").json()
    assert len(trades) == 1
    assert trades[0]["status"] == "CLOSED"
    assert trades[0]["realized_quantity"] == 4
    assert trades[0]["pnl"] == 72.0
    assert _portfolio(client)["balance"] == 10072.0


def test_crypto_trailing_stop_ratchets_only_in_the_favorable_direction(client: TestClient) -> None:
    _long_position(client, "BTC", quantity=2, price=100)
    order = _create(
        client,
        idempotency_key="btc-trailing",
        ticker="BTC",
        asset_type="crypto",
        side="SELL",
        order_type="trailing_stop",
        quantity=2,
        trail_percent=10,
    )
    assert order["asset_type"] == "crypto"

    _process(client, "BTC", [_candle(2, 100, 110, 100, 108, 10_000)])
    first = _order(client, order["id"])
    assert first["trail_reference_price"] == 110.0
    assert first["effective_stop_price"] == 99.0
    assert first["filled_quantity"] == 0

    _process(client, "BTC", [_candle(3, 110, 120, 109, 118, 10_000)])
    ratcheted = _order(client, order["id"])
    assert ratcheted["trail_reference_price"] == 120.0
    assert ratcheted["effective_stop_price"] == 108.0
    assert ratcheted["filled_quantity"] == 0

    # A lower high must not move the trail back down; the stop is hit instead.
    _process(client, "BTC", [_candle(4, 115, 116, 105, 106, 10_000)])
    filled = _order(client, order["id"])
    assert filled["trail_reference_price"] == 120.0
    assert filled["effective_stop_price"] == 108.0
    assert filled["status"] == "filled"
    assert filled["fills"][0]["price"] == 106.92
    assert _portfolio(client)["balance"] == 10011.68

    _process(client, "BTC", [_candle(5, 100, 101, 90, 91, 10_000)])
    assert len(_order(client, order["id"])["fills"]) == 1
    assert client.get("/api/trades").json()[0]["status"] == "CLOSED"


def test_insufficient_buying_power_is_rejected_without_touching_cash(client: TestClient) -> None:
    order = _create(
        client,
        idempotency_key="too-big",
        quantity=1000,
        reference_price=100,
    )
    assert order["status"] == "rejected"
    assert order["reserved_cash"] == 0.0
    assert "Insufficient buying power" in order["reject_reason"]
    assert _event_types(order) == ["created", "rejected"]
    assert _portfolio(client)["balance"] == 10000.0
    assert client.get("/api/trades").json() == []

    _process(client, "AAPL", [_candle(2, 100, 101, 99, 100.5, 10_000)])
    assert _order(client, order["id"])["filled_quantity"] == 0


def test_selling_more_than_the_open_position_is_rejected(client: TestClient) -> None:
    _long_position(client, "AAPL", quantity=2, price=100)
    order = _create(
        client,
        idempotency_key="oversell",
        side="SELL",
        order_type="limit",
        quantity=5,
        limit_price=110,
    )
    assert order["status"] == "rejected"
    assert "Insufficient position" in order["reject_reason"]


def test_canceled_orders_release_cash_and_can_never_fill(client: TestClient) -> None:
    order = _create(
        client,
        idempotency_key="cancel-me",
        order_type="limit",
        quantity=1,
        limit_price=50,
    )
    assert _portfolio(client)["balance"] == 9950.0

    canceled = client.post(f"/api/paper-orders/{order['id']}/cancel", json={"reason": "changed my mind"})
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "canceled"
    assert canceled.json()["reserved_cash"] == 0.0
    assert _portfolio(client)["balance"] == 10000.0

    # Cancelling twice is a safe retry, and a canceled order cannot fill.
    again = client.post(f"/api/paper-orders/{order['id']}/cancel", json={})
    assert again.status_code == 200
    assert len(again.json()["events"]) == len(canceled.json()["events"])

    _process(client, "AAPL", [_candle(2, 45, 46, 40, 44, 10_000)])
    unchanged = _order(client, order["id"])
    assert unchanged["status"] == "canceled"
    assert unchanged["filled_quantity"] == 0
    assert _portfolio(client)["balance"] == 10000.0


def test_canceling_a_bracket_parent_cancels_its_children(client: TestClient) -> None:
    parent = _create(
        client,
        idempotency_key="bracket-cancel",
        order_type="bracket",
        quantity=2,
        reference_price=100,
        take_profit_price=120,
        stop_loss_price=90,
    )
    client.post(f"/api/paper-orders/{parent['id']}/cancel", json={})
    detail = _order(client, parent["id"])
    assert detail["status"] == "canceled"
    assert [child["status"] for child in detail["children"]] == ["canceled", "canceled"]
    assert _portfolio(client)["balance"] == 10000.0


def test_expired_orders_release_cash_and_stop_working(client: TestClient) -> None:
    order = _create(
        client,
        idempotency_key="expire-me",
        order_type="limit",
        quantity=1,
        limit_price=50,
        time_in_force="day",
        expires_at="2024-01-03T00:00:00",
    )
    _process(
        client,
        "AAPL",
        [_candle(2, 60, 61, 55, 58, 10_000), _candle(3, 52, 53, 45, 46, 10_000)],
    )
    expired = _order(client, order["id"])
    assert expired["status"] == "expired"
    assert expired["reserved_cash"] == 0.0
    assert _portfolio(client)["balance"] == 10000.0

    _process(client, "AAPL", [_candle(4, 45, 46, 40, 44, 10_000)])
    assert _order(client, order["id"])["filled_quantity"] == 0


def test_day_orders_expire_on_the_next_session_without_an_explicit_expiry(
    tmp_path, monkeypatch
) -> None:
    """A DAY order anchors to its first candle's session and dies on the next one."""
    database_path = tmp_path / "paper-orders-day.db"
    with _service(database_path, monkeypatch) as client:
        order = _create(
            client,
            idempotency_key="day-order",
            ticker="NFLX",
            order_type="limit",
            quantity=1,
            limit_price=50,
            time_in_force="day",
        )
        assert order["expires_at"] is None
        assert order["reserved_cash"] == 50.0

        # Non-marketable candle: the order works for the rest of its own session.
        _process(client, "NFLX", [_candle(2, 60, 61, 55, 58, 10_000)])
        anchored = _order(client, order["id"])
        assert anchored["status"] == "submitted"
        assert anchored["expires_at"].startswith("2024-01-03T00:00:00")
        assert anchored["reserved_cash"] == 50.0
        assert "day_session_anchored" in _event_types(anchored)

        # The next session's candle expires it and gives the reservation back.
        _process(client, "NFLX", [_candle(3, 59, 60, 54, 57, 10_000)])
        expired = _order(client, order["id"])
        assert expired["status"] == "expired"
        assert expired["reserved_cash"] == 0.0
        assert _portfolio(client)["balance"] == 10000.0
        expiry = [event for event in expired["events"] if event["event_type"] == "expired"]
        assert len(expiry) == 1
        assert expiry[0]["to_status"] == "expired"
        assert expiry[0]["created_at"] is not None

    with _service(database_path, monkeypatch) as client:
        restored = _order(client, order["id"])
        assert restored == expired

        # A marketable candle after expiry can never fill an expired order.
        _process(client, "NFLX", [_candle(4, 45, 46, 40, 44, 10_000)])
        assert _order(client, restored["id"]) == restored
        assert _portfolio(client)["balance"] == 10000.0


def test_day_bracket_children_expire_only_after_activation(client: TestClient) -> None:
    parent = _create(
        client,
        idempotency_key="day-bracket",
        order_type="bracket",
        quantity=1,
        limit_price=100,
        take_profit_price=120,
        stop_loss_price=90,
        time_in_force="day",
    )
    # Pending children are not working orders, so the first session cannot expire them.
    _process(client, "AAPL", [_candle(2, 99, 101, 98, 100.5, 10_000)])
    filled = _order(client, parent["id"])
    assert filled["status"] == "filled"
    assert [child["status"] for child in filled["children"]] == ["submitted", "submitted"]
    assert all(child["expires_at"] is None for child in filled["children"])

    # Once activated they anchor to the candle that works them, then expire.
    _process(client, "AAPL", [_candle(3, 105, 106, 104, 105.5, 10_000)])
    activated = _order(client, parent["id"])
    assert [child["status"] for child in activated["children"]] == ["submitted", "submitted"]

    _process(client, "AAPL", [_candle(4, 105, 106, 104, 105.5, 10_000)])
    expired = _order(client, parent["id"])
    assert [child["status"] for child in expired["children"]] == ["expired", "expired"]


def test_terminal_orders_reject_further_transitions(client: TestClient) -> None:
    order = _create(client, idempotency_key="fill-then-cancel", quantity=1, reference_price=110)
    _process(client, "AAPL", [_candle(2, 100, 101, 99, 100.5, 10_000)])
    assert _order(client, order["id"])["status"] == "filled"

    response = client.post(f"/api/paper-orders/{order['id']}/cancel", json={})
    assert response.status_code == 409
    assert "can no longer be canceled" in response.json()["detail"]


def test_validation_errors_are_actionable(client: TestClient) -> None:
    missing_limit = client.post(
        "/api/paper-orders",
        json={
            "idempotency_key": "bad-limit",
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "limit",
            "quantity": 1,
        },
    )
    assert missing_limit.status_code == 400
    assert "requires a limit price" in missing_limit.json()["detail"]

    bad_bracket = client.post(
        "/api/paper-orders",
        json={
            "idempotency_key": "bad-bracket",
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "bracket",
            "quantity": 1,
            "reference_price": 100,
            "take_profit_price": 90,
            "stop_loss_price": 120,
        },
    )
    assert bad_bracket.status_code == 400
    assert "stop loss below its take profit" in bad_bracket.json()["detail"]

    no_reference = client.post(
        "/api/paper-orders",
        json={
            "idempotency_key": "no-reference",
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "market",
            "quantity": 1,
        },
    )
    assert no_reference.status_code == 400
    assert "reference_price" in no_reference.json()["detail"]

    assert client.get("/api/paper-orders").json()["orders"] == []
    assert _portfolio(client)["balance"] == 10000.0


def test_process_rejects_inconsistent_candles(client: TestClient) -> None:
    _create(client, idempotency_key="candle-guard", quantity=1, reference_price=110)
    response = client.post(
        "/api/paper-orders/process",
        json={"ticker": "AAPL", "candles": [_candle(2, 100, 99, 101, 100, 1000)], **FRICTION},
    )
    assert response.status_code == 400
    assert "inconsistent high/low range" in response.json()["detail"]

    empty = client.post("/api/paper-orders/process", json={"ticker": "AAPL", "candles": [], **FRICTION})
    assert empty.status_code == 400
    assert "No candles available" in empty.json()["detail"]


def test_filters_narrow_the_order_list(client: TestClient) -> None:
    _create(client, idempotency_key="f1", quantity=1, reference_price=110)
    _create(
        client,
        idempotency_key="f2",
        ticker="BTC",
        asset_type="crypto",
        order_type="limit",
        quantity=1,
        limit_price=90,
    )
    assert len(client.get("/api/paper-orders?ticker=BTC").json()["orders"]) == 1
    assert len(client.get("/api/paper-orders?asset_type=crypto").json()["orders"]) == 1
    assert len(client.get("/api/paper-orders?order_type=market").json()["orders"]) == 1
    assert len(client.get("/api/paper-orders?status=submitted").json()["orders"]) == 2
    assert len(client.get("/api/paper-orders?side=SELL").json()["orders"]) == 0
    assert client.get("/api/paper-orders?status=nonsense").status_code == 400


def test_audit_trail_and_fills_survive_a_restart(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "paper-orders-restart.db"
    with _service(database_path, monkeypatch) as client:
        order = _create(
            client,
            idempotency_key="restart",
            quantity=10,
            reference_price=110,
        )
        _process(client, "AAPL", [_candle(2, 100, 101, 99, 100.5, 500)])
        before = _order(client, order["id"])
        assert before["status"] == "partially_filled"

    with _service(database_path, monkeypatch) as client:
        after = _order(client, order["id"])
        assert after == before
        assert after["reserved_cash"] == 550.0
        assert _portfolio(client)["balance"] == 8945.0
        assert client.get("/api/trades").json()[0]["quantity"] == 5

        _process(client, "AAPL", [_candle(3, 102, 103, 101.5, 102.5, 2000)])
        completed = _order(client, order["id"])
        assert completed["status"] == "filled"
        assert completed["average_fill_price"] == 102.01

        # Audit history is append-only: earlier events are untouched and ordered.
        assert completed["events"][: len(before["events"])] == before["events"]
        ids = [event["id"] for event in completed["events"]]
        timestamps = [event["created_at"] for event in completed["events"]]
        assert ids == sorted(ids)
        assert timestamps == sorted(timestamps)
        chain = [(event["from_status"], event["to_status"]) for event in completed["events"]]
        for previous, current in zip(chain, chain[1:]):
            assert previous[1] == current[0]


def test_manual_trades_and_attribution_stay_compatible_with_paper_fills(client: TestClient) -> None:
    manual = _long_position(client, "AAPL", quantity=10, price=100)
    order = _create(
        client,
        idempotency_key="mixed",
        side="SELL",
        order_type="limit",
        quantity=10,
        limit_price=110,
    )
    _process(client, "AAPL", [_candle(2, 112, 113, 109, 112.5, 10_000)])
    assert _order(client, order["id"])["status"] == "filled"

    executions = client.get(f"/api/trades/{manual['id']}/executions").json()["executions"]
    assert [execution["kind"] for execution in executions] == ["ENTRY", "EXIT"]
    attribution = client.get("/api/attribution").json()
    assert [trade["id"] for trade in attribution["trades"]] == [manual["id"]]
    assert len(attribution["journals"]) == 1
    journal = client.get(f"/api/trades/{manual['id']}/journal")
    assert journal.status_code == 200
    assert journal.json()["net_pnl"] == pytest.approx(client.get("/api/trades").json()[0]["pnl"])


class TestSimulationSemantics:
    """Pure fill-rule checks that need no database."""

    def _candle(self, **overrides) -> paper.Candle:
        values = {"open": 100.0, "high": 105.0, "low": 95.0, "close": 101.0, "volume": 1_000.0}
        values.update(overrides)
        return paper.parse_candle({"timestamp": "2024-01-02T00:00:00", **values})

    def test_terminal_states_allow_no_transition(self) -> None:
        for status in paper.TERMINAL_STATUSES:
            assert paper.ALLOWED_TRANSITIONS[status] == frozenset()
            assert not paper.transition_allowed(status, paper.FILLED)

    def test_buy_limit_fill_is_capped_at_the_limit(self) -> None:
        state = paper.OrderState(side="BUY", order_type="limit", quantity=1, limit_price=99.0)
        outcome = paper.simulate_candle(state, self._candle(), paper.FillConfig(slippage_pct=0.5))
        assert outcome.fill_price == 99.0

    def test_sell_limit_fill_is_floored_at_the_limit(self) -> None:
        state = paper.OrderState(side="SELL", order_type="limit", quantity=1, limit_price=104.0)
        outcome = paper.simulate_candle(state, self._candle(), paper.FillConfig(slippage_pct=0.5))
        assert outcome.fill_price == 104.0

    def test_participation_cap_limits_a_single_candle(self) -> None:
        state = paper.OrderState(side="BUY", order_type="market", quantity=100)
        outcome = paper.simulate_candle(
            state, self._candle(volume=500), paper.FillConfig(participation_pct=0.01)
        )
        assert outcome.fill_quantity == 5
        assert outcome.reason == "partially_filled"

    def test_zero_volume_candle_cannot_fill(self) -> None:
        state = paper.OrderState(side="BUY", order_type="market", quantity=1)
        outcome = paper.simulate_candle(state, self._candle(volume=0), paper.FillConfig())
        assert outcome.filled is False
        assert outcome.reason == "no_liquidity"

    def test_trailing_reference_ratchets_one_way_per_side(self) -> None:
        candle = self._candle()
        assert paper.trailing_reference("SELL", 110.0, candle) == 110.0
        assert paper.trailing_reference("SELL", 100.0, candle) == 105.0
        assert paper.trailing_reference("BUY", 90.0, candle) == 90.0
        assert paper.trailing_reference("BUY", 100.0, candle) == 95.0

    def test_trailing_stop_distance_uses_percent_before_amount(self) -> None:
        assert paper.trailing_stop_price("SELL", 120.0, 10.0, 50.0) == 108.0
        assert paper.trailing_stop_price("SELL", 120.0, None, 5.0) == 115.0
        assert paper.trailing_stop_price("BUY", 100.0, 10.0, None) == 110.0

    def test_weighted_average_price_of_fills(self) -> None:
        assert paper.weighted_average_price([(5, 101.0), (5, 103.02)]) == 102.01
        assert paper.weighted_average_price([]) is None

    def test_prices_round_half_up_at_four_decimals(self) -> None:
        assert paper.round_price(101.07575) == 101.0758
        assert paper.round_price(105.07875) == 105.0788
        assert paper.round_price(-105.07875) == -105.0788
        assert paper.round_price(101.075749) == 101.0757
        assert paper.round_price(2.5) == 2.5

    def test_half_tick_weighted_average_rounds_half_up(self) -> None:
        assert paper.weighted_average_price([(1, 100.0), (1, 102.1515)]) == 101.0758

    def test_half_tick_fill_price_rounds_half_up(self) -> None:
        state = paper.OrderState(side="BUY", order_type="market", quantity=1)
        candle = self._candle(open=105.07875, high=105.5, low=104.0, close=105.0)
        outcome = paper.simulate_candle(state, candle, paper.FillConfig())
        # The raw half-tick 105.07875 persists as 105.0788, not a truncated 105.0787.
        assert outcome.fill_price == 105.0788

    def test_rounding_never_fills_a_limit_order_past_its_limit(self) -> None:
        state = paper.OrderState(
            side="BUY", order_type="limit", quantity=1, limit_price=105.07875
        )
        outcome = paper.simulate_candle(
            state,
            self._candle(open=105.07875, high=105.5, low=104.0, close=105.0),
            paper.FillConfig(),
        )
        assert outcome.fill_price == 105.07875

    def test_cash_rounds_half_up_to_cents(self) -> None:
        assert paper.round_cash(1115.835) == 1115.84
        assert paper.round_cash(1115.8349) == 1115.83
        assert paper.to_cents(1115.835) == 111584
        assert paper.to_cents(8935.67) == 893567

    def test_equity_tolerance_accepts_exactly_one_cent(self) -> None:
        components = [8935.67, 1115.84, 0.0]
        assert paper.equity_balanced(10051.51, components) is True  # 0 cents
        assert paper.equity_balanced(10051.50, components) is True  # 1 cent below
        assert paper.equity_balanced(10051.52, components) is True  # 1 cent above
        assert paper.equity_balanced(10051.49, components) is False  # 2 cents
        assert paper.equity_balanced(10051.53, components) is False  # 2 cents

    def test_equity_tolerance_uses_the_rounded_component_values(self) -> None:
        # Position capital carries a half cent: the comparison must use the 1115.84
        # the API and UI display, not the raw 1115.835.
        assert paper.equity_balanced(10051.51, [8935.67, 1115.835, 0.0]) is True
        assert paper.equity_balanced(10051.50, [8935.67, 1115.835, 0.0]) is True
        assert paper.equity_balanced(10049.51, [8935.67, 1115.835, 0.0]) is False

    def test_intraday_candles_are_never_fabricated(self) -> None:
        with pytest.raises(ValueError):
            paper.parse_candle({"timestamp": "2024-01-02T00:00:00", "open": 100, "high": 99})
        with pytest.raises(ValueError):
            paper.parse_candle(
                {"timestamp": "2024-01-02T00:00:00", "open": 100, "high": 99, "low": 98, "close": 100}
            )
