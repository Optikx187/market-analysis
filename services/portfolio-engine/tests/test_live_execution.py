import asyncio
import datetime
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Optional

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import live_execution as live
from app import main
from app.brokers.alpaca import AlpacaBroker
from app.brokers.base import BrokerAccount, BrokerAsset, BrokerError, BrokerOrder, BrokerPosition
from app.config import settings
from app.database import Base, _migrate_existing_tables, get_db
from app.models import LiveExecutionAudit, LiveTradingControl

ACK_PHRASE = settings.LIVE_ACK_PHRASE
OPERATOR_TOKEN = "test-live-operator-token"
AUTH_HEADERS = {"X-Live-Operator-Token": OPERATOR_TOKEN}

# A Wednesday inside the US equity session so market-hours gates pass by default.
OPEN_MOMENT = datetime.datetime(2024, 1, 3, 15, 0, tzinfo=datetime.timezone.utc)


class FakeBroker:
    """In-memory stand-in for a broker sandbox.

    Integration coverage never touches a real account: this adapter records
    submissions so idempotency, cancel-all and reconciliation can be asserted
    exactly, and ``fail`` simulates a provider outage.
    """

    name = "fake"
    sandbox = True

    def __init__(self) -> None:
        self.orders: dict[str, dict[str, object]] = {}
        self.submissions: list[str] = []
        self.cancels: list[str] = []
        self.fail = False
        self.account_fail = False
        self.position_fail = False
        self.submit_ambiguous: Optional[str] = None
        self.submit_rejected = False
        self.buying_power: Optional[float] = 100_000.0
        self.trading_blocked = False
        self.tradable = True
        self.shortable = True
        self.halted = False
        self.position_quantity = 0.0
        self.next_status = "accepted"
        self.next_filled = 0.0
        self.next_price: Optional[float] = None

    def configured(self) -> bool:
        return True

    def _raise_if_down(self) -> None:
        if self.fail:
            raise BrokerError(
                "Simulated broker outage",
                status_code=503,
                body={"secret": "must-not-leak"},
                ambiguous=True,
            )

    async def get_account(self) -> BrokerAccount:
        self._raise_if_down()
        if self.account_fail:
            raise BrokerError("Simulated account lookup failure", ambiguous=True)
        return BrokerAccount(
            buying_power=self.buying_power,
            cash=self.buying_power,
            equity=self.buying_power,
            trading_blocked=self.trading_blocked,
            account_id="fake-account",
        )

    async def get_asset(self, symbol: str) -> BrokerAsset:
        self._raise_if_down()
        return BrokerAsset(
            symbol=symbol,
            tradable=self.tradable,
            shortable=self.shortable,
            halted=self.halted,
        )

    async def get_position(self, symbol: str) -> BrokerPosition:
        self._raise_if_down()
        if self.position_fail:
            raise BrokerError("Simulated position lookup failure", ambiguous=True)
        return BrokerPosition(symbol=symbol, quantity=self.position_quantity)

    def _order(self, client_order_id: str) -> BrokerOrder:
        raw = self.orders[client_order_id]
        return BrokerOrder(
            broker_order_id=str(raw["id"]),
            client_order_id=client_order_id,
            status=live.normalize_status(raw["status"]),
            filled_quantity=float(raw["filled_qty"]),
            average_fill_price=raw["filled_avg_price"],  # type: ignore[arg-type]
            raw=raw,
        )

    async def submit_order(self, request: live.OrderRequest, client_order_id: str) -> BrokerOrder:
        self._raise_if_down()
        self.submissions.append(client_order_id)
        if self.submit_rejected:
            raise BrokerError(
                "Broker rejected the order",
                status_code=422,
                body={"secret": "must-not-leak", "raw": "provider payload"},
            )
        if self.submit_ambiguous == "unknown":
            raise BrokerError("Submission response was lost", ambiguous=True)
        if client_order_id not in self.orders:
            self.orders[client_order_id] = {
                "id": f"broker-{len(self.orders) + 1}",
                "client_order_id": client_order_id,
                "status": self.next_status,
                "filled_qty": self.next_filled,
                "filled_avg_price": self.next_price,
            }
        if self.submit_ambiguous == "accepted":
            raise BrokerError("Submission response was lost", ambiguous=True)
        return self._order(client_order_id)

    async def get_order_by_client_id(self, client_order_id: str) -> Optional[BrokerOrder]:
        self._raise_if_down()
        if client_order_id not in self.orders:
            return None
        return self._order(client_order_id)

    async def get_order(self, broker_order_id: str) -> Optional[BrokerOrder]:
        self._raise_if_down()
        for client_order_id, raw in self.orders.items():
            if raw["id"] == broker_order_id:
                return self._order(client_order_id)
        return None

    async def cancel_order(self, broker_order_id: str) -> None:
        self._raise_if_down()
        self.cancels.append(broker_order_id)
        for raw in self.orders.values():
            if raw["id"] == broker_order_id:
                raw["status"] = "pending_cancel"

    async def list_open_orders(self) -> list[BrokerOrder]:
        self._raise_if_down()
        return [
            self._order(client_order_id)
            for client_order_id, raw in self.orders.items()
            if live.normalize_status(raw["status"]) in live.OPEN_STATUSES
        ]


class FrozenDatetime(datetime.datetime):
    """Pins ``datetime.datetime.now`` so market-hours gates are deterministic."""

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return OPEN_MOMENT if tz else OPEN_MOMENT.replace(tzinfo=None)


@contextmanager
def _service(database_path, monkeypatch, broker: FakeBroker) -> Iterator[TestClient]:
    url = f"sqlite+aiosqlite:///{database_path}"
    engine = create_async_engine(url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def prepare() -> None:
        setup_engine = create_async_engine(url)
        async with setup_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.run_sync(_migrate_existing_tables)
        await setup_engine.dispose()

    async def override_get_db():
        async with session_factory() as session:
            yield session

    async def fake_candles(ticker: str, interval: str) -> list[dict[str, object]]:
        return [{"timestamp": "2024-01-03T00:00:00", "close": 100.0, "volume": 1_000_000}]

    async def fake_get_json(url: str, params=None) -> dict[str, object]:
        if "/api/quotes/" in url:
            return {"price": 100.0, "updated_at": OPEN_MOMENT.isoformat()}
        return {}

    asyncio.run(prepare())
    monkeypatch.setattr(main, "_fetch_candles", fake_candles)
    monkeypatch.setattr(main, "_get_json", fake_get_json)
    monkeypatch.setattr(main.datetime, "datetime", FrozenDatetime)
    monkeypatch.setattr(settings, "LIVE_OPERATOR_TOKEN", OPERATOR_TOKEN)
    monkeypatch.setattr(main, "async_session", session_factory)
    main.app.dependency_overrides[get_db] = override_get_db
    main.app.dependency_overrides[main.get_broker] = lambda: broker
    try:
        with TestClient(main.app) as test_client:
            test_client.headers.update(AUTH_HEADERS)
            yield test_client
    finally:
        main.app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


@pytest.fixture
def broker() -> FakeBroker:
    return FakeBroker()


@pytest.fixture
def client(tmp_path, monkeypatch, broker) -> Iterator[TestClient]:
    with _service(tmp_path / "live-exec-test.db", monkeypatch, broker) as test_client:
        yield test_client


@pytest.fixture
def armed_client(client, monkeypatch) -> TestClient:
    """A client with configuration enabled and an acknowledgement on record."""
    monkeypatch.setattr(settings, "LIVE_TRADING_ENABLED", True)
    response = client.post("/api/live-trading/acknowledge", json={"phrase": ACK_PHRASE})
    assert response.status_code == 200, response.text
    return client


def _order_payload(**overrides) -> dict:
    payload = {
        "ticker": "AAPL",
        "side": "BUY",
        "order_type": "limit",
        "quantity": 5,
        "limit_price": 100.0,
    }
    payload.update(overrides)
    return payload


def _submit(client: TestClient, key: str = "live-1", **overrides) -> dict:
    payload = _order_payload(**overrides)
    preview = client.post("/api/live-orders/preview", json=payload)
    assert preview.status_code == 200, preview.text
    body = dict(payload)
    body["idempotency_key"] = key
    body["approval_fingerprint"] = preview.json()["approval_fingerprint"]
    response = client.post("/api/live-orders", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _checks(preview: dict) -> dict[str, dict]:
    return {check["name"]: check for check in preview["checks"]}


# --- pure gate logic -------------------------------------------------------


def _context(**overrides) -> live.GateContext:
    defaults = dict(
        now=OPEN_MOMENT,
        config_enabled=True,
        acknowledged=True,
        trading_disabled=False,
        disabled_reason=None,
        broker="fake",
        credentials_present=True,
        sandbox=True,
        reference_price=100.0,
        price_age_seconds=30.0,
        max_price_age_seconds=300.0,
        data_eligible=True,
        data_reason=None,
        halted=False,
        tradable=True,
        shortable=True,
        held_quantity=0.0,
        buying_power=10_000.0,
        account_trading_allowed=True,
        breaker_active=False,
        breaker_reasons=(),
        max_order_notional=100_000.0,
    )
    defaults.update(overrides)
    return live.GateContext(**defaults)  # type: ignore[arg-type]


def _request(**overrides) -> live.OrderRequest:
    defaults = dict(
        ticker="AAPL",
        asset_type="stock",
        side="BUY",
        order_type="limit",
        quantity=5.0,
        time_in_force="day",
        limit_price=100.0,
        stop_price=None,
    )
    defaults.update(overrides)
    return live.OrderRequest(**defaults)  # type: ignore[arg-type]


def test_gates_pass_only_when_every_condition_holds():
    assert live.submittable(live.preflight(_request(), _context()))


@pytest.mark.parametrize(
    "overrides, blocked",
    [
        ({"config_enabled": False}, "live_trading_configured"),
        ({"acknowledged": False}, "operator_acknowledged"),
        ({"trading_disabled": True}, "kill_switch_clear"),
        ({"credentials_present": False}, "broker_configured"),
        ({"price_age_seconds": 3_600.0}, "price_fresh"),
        ({"reference_price": None}, "price_fresh"),
        ({"data_eligible": False}, "data_quality"),
        ({"data_eligible": None}, "data_quality"),
        ({"halted": True}, "not_halted"),
        ({"tradable": False}, "not_halted"),
        ({"buying_power": 10.0}, "buying_power"),
        ({"buying_power": None}, "buying_power"),
        ({"account_trading_allowed": False}, "account_trading_allowed"),
        ({"account_trading_allowed": None}, "account_trading_allowed"),
        ({"breaker_active": True}, "risk_breakers_clear"),
        ({"breaker_active": None}, "risk_breakers_clear"),
        ({"max_order_notional": 10.0}, "notional_cap"),
    ],
)
def test_each_gate_blocks_independently(overrides, blocked):
    checks = live.preflight(_request(), _context(**overrides))
    assert blocked in {check.name for check in live.blockers(checks)}


def test_closed_equity_session_blocks_but_crypto_trades_continuously():
    saturday = datetime.datetime(2024, 1, 6, 15, 0, tzinfo=datetime.timezone.utc)
    stock_checks = live.preflight(_request(), _context(now=saturday))
    assert "market_open" in {check.name for check in live.blockers(stock_checks)}
    crypto_checks = live.preflight(_request(asset_type="crypto"), _context(now=saturday))
    assert live.submittable(crypto_checks)


def test_uncovered_sale_requires_confirmed_short_availability():
    blocked = live.preflight(
        _request(side="SELL"), _context(shortable=False, held_quantity=1.0)
    )
    assert "short_availability" in {check.name for check in live.blockers(blocked)}
    covered = live.preflight(_request(side="SELL"), _context(shortable=False, held_quantity=5.0))
    assert live.submittable(covered)


def test_production_endpoint_warns_without_blocking():
    checks = {check.name: check for check in live.preflight(_request(), _context(sandbox=False))}
    assert not checks["broker_sandbox"].passed
    assert checks["broker_sandbox"].as_dict()["blocking"] is False


def test_fingerprint_changes_with_any_order_field():
    base = live.fingerprint(_request())
    assert base == live.fingerprint(_request())
    assert base != live.fingerprint(_request(quantity=6.0))
    assert base != live.fingerprint(_request(side="SELL"))


def test_reconcile_reports_status_and_quantity_drift():
    diff = live.reconcile(
        {"id": 1, "broker_order_id": "b1", "status": "accepted", "filled_quantity": 0.0},
        {"status": "filled", "filled_qty": 5},
    )
    assert diff["remote_status"] == live.FILLED
    assert diff["in_sync"] is False
    assert diff["remote_filled_quantity"] == 5.0


def test_unknown_broker_status_is_not_treated_as_terminal():
    assert live.normalize_status("something_new") == live.UNKNOWN
    assert live.UNKNOWN not in live.TERMINAL_STATUSES


# --- API behaviour ---------------------------------------------------------


def test_live_trading_is_disabled_by_default(client):
    status = client.get("/api/live-trading/status").json()
    assert status["config_enabled"] is False
    assert status["acknowledged"] is False
    assert status["armed"] is False


def test_acknowledgement_requires_configuration_and_exact_phrase(client, monkeypatch):
    blocked = client.post("/api/live-trading/acknowledge", json={"phrase": ACK_PHRASE})
    assert blocked.status_code == 403
    monkeypatch.setattr(settings, "LIVE_TRADING_ENABLED", True)
    wrong = client.post("/api/live-trading/acknowledge", json={"phrase": "yes please"})
    assert wrong.status_code == 400
    ok = client.post("/api/live-trading/acknowledge", json={"phrase": ACK_PHRASE})
    assert ok.status_code == 200
    assert ok.json()["armed"] is True


def test_submission_is_impossible_before_arming(client, broker):
    payload = _order_payload()
    preview = client.post("/api/live-orders/preview", json=payload)
    assert preview.status_code == 200
    assert preview.json()["submittable"] is False
    body = dict(payload)
    body["idempotency_key"] = "blocked-1"
    body["approval_fingerprint"] = preview.json()["approval_fingerprint"]
    response = client.post("/api/live-orders", json=body)
    assert response.status_code == 201
    order = response.json()
    assert order["status"] == live.REJECTED
    assert broker.submissions == []
    assert "Live trading is disabled by configuration" in order["reject_reason"]


def test_submission_requires_a_matching_approval_fingerprint(armed_client, broker):
    body = _order_payload()
    body["idempotency_key"] = "mismatch-1"
    body["approval_fingerprint"] = "not-the-preview-hash"
    response = armed_client.post("/api/live-orders", json=body)
    assert response.status_code == 400
    assert broker.submissions == []


def test_armed_submission_reaches_the_broker_with_the_idempotency_key(armed_client, broker):
    order = _submit(armed_client, "live-key-1")
    assert order["status"] == live.ACCEPTED
    assert order["broker_order_id"] == "broker-1"
    assert order["sandbox"] is True
    assert broker.submissions == ["live-key-1"]
    events = [entry["event_type"] for entry in order["audit"]]
    assert events == ["approved", "order_request", "broker_response"]


def test_retrying_an_idempotency_key_never_duplicates_the_broker_order(armed_client, broker):
    first = _submit(armed_client, "live-key-2")
    second = _submit(armed_client, "live-key-2")
    assert first["id"] == second["id"]
    assert broker.submissions == ["live-key-2"]
    assert len(armed_client.get("/api/live-orders").json()["orders"]) == 1


def test_stale_data_blocks_the_order_before_the_broker_is_called(armed_client, broker, monkeypatch):
    async def stale(url: str, params=None) -> dict[str, object]:
        return {
            "price": 100.0,
            "updated_at": (OPEN_MOMENT - datetime.timedelta(hours=72)).isoformat(),
        }

    monkeypatch.setattr(main, "_get_json", stale)
    order = _submit(armed_client, "stale-1")
    assert order["status"] == live.REJECTED
    assert "259,200s old" in order["reject_reason"]
    assert broker.submissions == []


def test_insufficient_buying_power_blocks_the_order(armed_client, broker):
    broker.buying_power = 10.0
    order = _submit(armed_client, "bp-1")
    assert order["status"] == live.REJECTED
    assert "buying power" in order["reject_reason"]
    assert broker.submissions == []


def test_halted_symbol_blocks_the_order(armed_client, broker):
    broker.halted = True
    order = _submit(armed_client, "halt-1")
    assert order["status"] == live.REJECTED
    assert "halted" in order["reject_reason"]
    assert broker.submissions == []


def test_uncovered_short_sale_blocks_the_order(armed_client, broker):
    broker.shortable = False
    order = _submit(armed_client, "short-1", side="SELL")
    assert order["status"] == live.REJECTED
    assert "short-sellable" in order["reject_reason"]
    assert broker.submissions == []


def test_closed_session_blocks_the_order(armed_client, broker, monkeypatch):
    class Weekend(datetime.datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            moment = datetime.datetime(2024, 1, 6, 15, 0, tzinfo=datetime.timezone.utc)
            return moment if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr(main.datetime, "datetime", Weekend)
    order = _submit(armed_client, "closed-1")
    assert order["status"] == live.REJECTED
    assert "closed" in order["reject_reason"]
    assert broker.submissions == []


def test_active_risk_breaker_blocks_the_order(armed_client, broker, monkeypatch):
    async def breached(db):
        return {"breaker": {"active": True, "reasons": ["Daily loss limit reached"]}}

    monkeypatch.setattr(main, "_portfolio_risk_status", breached)
    order = _submit(armed_client, "breaker-1")
    assert order["status"] == live.REJECTED
    assert "Daily loss limit reached" in order["reject_reason"]
    assert broker.submissions == []


def test_per_order_notional_cap_blocks_oversized_orders(armed_client, broker, monkeypatch):
    monkeypatch.setattr(settings, "LIVE_MAX_ORDER_NOTIONAL_USD", 100.0)
    order = _submit(armed_client, "cap-1", quantity=50)
    assert order["status"] == live.REJECTED
    assert "per-order cap" in order["reject_reason"]
    assert broker.submissions == []


def test_kill_switch_blocks_new_orders_while_the_broker_is_down(armed_client, broker):
    broker.fail = True
    disabled = armed_client.post("/api/live-trading/disable", json={"reason": "Provider outage"})
    assert disabled.status_code == 200
    assert disabled.json()["trading_disabled"] is True
    order = _submit(armed_client, "killed-1")
    assert order["status"] == live.REJECTED
    assert "Provider outage" in order["reject_reason"]
    assert broker.submissions == []


def test_cancel_all_reports_broker_failures_without_aborting(armed_client, broker):
    _submit(armed_client, "cancel-a")
    _submit(armed_client, "cancel-b", ticker="MSFT")
    broker.fail = True
    failed = armed_client.post("/api/live-orders/cancel-all", json={"reason": "Outage"}).json()
    assert failed["requested"] == 2
    assert failed["canceled"] == 0
    assert failed["failed"] == 2
    broker.fail = False
    succeeded = armed_client.post("/api/live-orders/cancel-all", json={"reason": "Flatten"}).json()
    assert succeeded["canceled"] == 2
    assert succeeded["failed"] == 0
    assert sorted(broker.cancels) == ["broker-1", "broker-2"]
    statuses = {order["status"] for order in armed_client.get("/api/live-orders").json()["orders"]}
    assert statuses == {live.CANCEL_PENDING}
    for remote in broker.orders.values():
        remote["status"] = "canceled"
    reconciled = armed_client.post("/api/live-orders/reconcile").json()
    assert reconciled["out_of_sync"] == 2
    statuses = {order["status"] for order in armed_client.get("/api/live-orders").json()["orders"]}
    assert statuses == {live.CANCELED}


def test_reconciliation_adopts_broker_fills(armed_client, broker):
    order = _submit(armed_client, "recon-1")
    broker.orders["recon-1"]["status"] = "filled"
    broker.orders["recon-1"]["filled_qty"] = 5.0
    broker.orders["recon-1"]["filled_avg_price"] = 101.25
    result = armed_client.post("/api/live-orders/reconcile").json()
    assert result["checked"] == 1
    assert result["out_of_sync"] == 1
    detail = armed_client.get(f"/api/live-orders/{order['id']}").json()
    assert detail["status"] == live.FILLED
    assert detail["filled_quantity"] == 5.0
    assert detail["average_fill_price"] == 101.25
    assert len(detail["fills"]) == 1
    assert [entry["event_type"] for entry in detail["audit"]][-2:] == ["reconciled", "fill"]
    repeated = armed_client.post("/api/live-orders/reconcile").json()
    assert repeated["checked"] == 0


def test_reconciliation_survives_broker_failure(armed_client, broker):
    _submit(armed_client, "recon-2")
    broker.fail = True
    result = armed_client.post("/api/live-orders/reconcile").json()
    assert result["errors"] == 1
    assert result["out_of_sync"] == 0


def test_audit_chain_detects_tampering(armed_client):
    _submit(armed_client, "audit-1")
    verified = armed_client.get("/api/live-orders/audit/verify").json()
    assert verified["intact"] is True
    assert verified["entries"] >= 3

    entries = armed_client.get("/api/live-orders/audit").json()["entries"]
    target = entries[1]
    asyncio.run(_tamper(armed_client, target["id"]))
    broken = armed_client.get("/api/live-orders/audit/verify").json()
    assert broken["intact"] is False
    assert broken["broken_entry_id"] == target["id"]


async def _tamper(client: TestClient, entry_id: int) -> None:
    """Edit an audit row directly to prove the chain detects it."""
    override = main.app.dependency_overrides[get_db]
    async for session in override():
        entry = (
            await session.execute(select(LiveExecutionAudit).where(LiveExecutionAudit.id == entry_id))
        ).scalar_one()
        entry.record_json = '{"event_type": "approved", "message": "tampered"}'
        await session.commit()
        break


def test_paper_and_live_storage_stay_separate(armed_client):
    live_order = _submit(armed_client, "sep-1")
    paper_order = armed_client.post(
        "/api/paper-orders",
        json={
            "idempotency_key": "sep-paper-1",
            "ticker": "AAPL",
            "side": "BUY",
            "order_type": "limit",
            "quantity": 1,
            "limit_price": 100.0,
        },
    )
    assert paper_order.status_code == 201, paper_order.text
    live_ids = [order["id"] for order in armed_client.get("/api/live-orders").json()["orders"]]
    paper_ids = [order["id"] for order in armed_client.get("/api/paper-orders").json()["orders"]]
    assert live_ids == [live_order["id"]]
    assert paper_order.json()["id"] in paper_ids
    assert armed_client.get("/api/paper-orders/mode").json()["mode"] == live.PAPER
    assert armed_client.get("/api/live-trading/status").json()["mode"] == live.LIVE


async def _stored_audit_records() -> list[str]:
    override = main.app.dependency_overrides[get_db]
    async for session in override():
        entries = (await session.execute(select(LiveExecutionAudit))).scalars().all()
        return [entry.record_json for entry in entries]
    return []


def test_broker_rejection_is_recorded_without_sensitive_provider_payload(armed_client, broker):
    broker.submit_rejected = True
    order = _submit(armed_client, "broker-error-1")
    assert order["status"] == live.REJECTED
    assert order["broker_order_id"] is None
    assert order["fills"] == []
    serialized = str(order["audit"])
    assert "must-not-leak" not in serialized
    assert "provider payload" not in serialized
    stored = str(asyncio.run(_stored_audit_records()))
    assert "must-not-leak" not in stored
    assert "provider payload" not in stored
    broker_event = order["audit"][-1]
    assert broker_event["record"]["record"] == {
        "status_code": 422,
        "ambiguous": False,
        "error_type": "broker_rejection",
    }


def test_lifespan_initializes_exactly_one_live_control_row(client):
    async def controls() -> list[LiveTradingControl]:
        override = main.app.dependency_overrides[get_db]
        async for session in override():
            return (await session.execute(select(LiveTradingControl))).scalars().all()
        return []

    rows = asyncio.run(controls())
    assert len(rows) == 1
    assert rows[0].id == 1
    assert rows[0].acknowledged is False


def test_operator_token_is_required_even_when_application_auth_is_disabled(client, monkeypatch):
    missing = client.post(
        "/api/live-trading/disable",
        json={"reason": "unauthorized"},
        headers={"X-Live-Operator-Token": ""},
    )
    assert missing.status_code == 401
    invalid = client.get(
        "/api/live-orders/audit/verify",
        headers={"X-Live-Operator-Token": "wrong"},
    )
    assert invalid.status_code == 401
    monkeypatch.setattr(settings, "LIVE_OPERATOR_TOKEN", "")
    unavailable = client.post("/api/live-orders/reconcile")
    assert unavailable.status_code == 403
    status = client.get("/api/live-trading/status").json()
    assert status["operator_auth_configured"] is False
    assert status["armed"] is False


def test_sell_uses_broker_position_and_fails_closed_when_lookup_fails(armed_client, broker):
    broker.shortable = False
    broker.position_quantity = 5.0
    covered = _submit(armed_client, "covered-sell", side="SELL")
    assert covered["status"] == live.ACCEPTED

    broker.position_fail = True
    unknown = _submit(armed_client, "unknown-position", side="SELL")
    assert unknown["status"] == live.REJECTED
    assert "Broker position quantity could not be confirmed" in unknown["reject_reason"]


def test_paper_position_never_authorizes_a_live_sale(armed_client, broker):
    paper = armed_client.post(
        "/api/trades/manual",
        json={
            "ticker": "AAPL",
            "direction": "BUY",
            "entry_price": 100.0,
            "quantity": 5.0,
        },
    )
    assert paper.status_code == 200, paper.text
    broker.shortable = False
    broker.position_quantity = 0.0
    live_sale = _submit(armed_client, "paper-isolation", side="SELL")
    assert live_sale["status"] == live.REJECTED
    assert "Only 0 broker-held units" in live_sale["reject_reason"]


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_account_lookup_failure_blocks_buys_and_sells(armed_client, broker, side):
    broker.account_fail = True
    if side == "SELL":
        broker.position_quantity = 5.0
    order = _submit(armed_client, f"account-fail-{side.lower()}", side=side)
    assert order["status"] == live.REJECTED
    assert "Broker account state could not be confirmed" in order["reject_reason"]
    assert broker.submissions == []


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://paper-api.alpaca.markets", True),
        ("https://broker-api.sandbox.alpaca.markets/v1", True),
        ("http://localhost:9000", True),
        ("http://127.0.0.1:9000", True),
        ("https://paper-api.alpaca.markets.attacker.example", False),
        ("https://attacker.example/paper-api.alpaca.markets", False),
        ("https://api.alpaca.markets", False),
    ],
)
def test_sandbox_detection_uses_exact_hostname(url, expected):
    assert AlpacaBroker("key", "secret", url).sandbox is expected


def test_alpaca_account_state_requires_complete_boolean_flags(monkeypatch):
    broker = AlpacaBroker("key", "secret", "https://paper-api.alpaca.markets")

    async def missing_flag(*args, **kwargs):
        return {
            "id": "account",
            "buying_power": "1000",
            "trading_blocked": False,
            "account_blocked": False,
        }

    monkeypatch.setattr(broker, "_request", missing_flag)
    with pytest.raises(BrokerError, match="trading-state flags"):
        asyncio.run(broker.get_account())

    async def usable(*args, **kwargs):
        return {
            "id": "account",
            "buying_power": "1000",
            "cash": "500",
            "equity": "1500",
            "trading_blocked": False,
            "account_blocked": False,
            "trade_suspended_by_user": False,
        }

    monkeypatch.setattr(broker, "_request", usable)
    assert asyncio.run(broker.get_account()).trading_blocked is False


def test_ambiguous_submission_recovers_by_client_order_id(armed_client, broker):
    broker.submit_ambiguous = "accepted"
    order = _submit(armed_client, "ambiguous-recovered")
    assert order["status"] == live.ACCEPTED
    assert order["broker_order_id"] == "broker-1"
    assert order["audit"][-1]["event_type"] == "submission_recovered"


def test_ambiguous_submission_remains_recoverable_when_broker_cannot_find_it(armed_client, broker):
    broker.submit_ambiguous = "unknown"
    order = _submit(armed_client, "ambiguous-unknown")
    assert order["status"] == live.SUBMISSION_UNKNOWN
    assert order["reject_reason"] is None
    assert order["audit"][-1]["event_type"] == "submission_unknown"
    assert "must-not-leak" not in str(order["audit"])


def test_cancel_all_includes_unknown_active_statuses(armed_client, broker):
    _submit(armed_client, "unknown-active")
    broker.orders["unknown-active"]["status"] = "vendor_active_state"
    reconciled = armed_client.post("/api/live-orders/reconcile").json()
    assert reconciled["out_of_sync"] == 1
    order = armed_client.get("/api/live-orders").json()["orders"][0]
    assert order["status"] == live.UNKNOWN
    canceled = armed_client.post("/api/live-orders/cancel-all", json={"reason": "Emergency"}).json()
    assert canceled["requested"] == 1
    assert canceled["canceled"] == 1
    assert broker.cancels == ["broker-1"]


def test_cumulative_fill_snapshots_create_only_incremental_fills(armed_client, broker):
    order = _submit(armed_client, "partial-deltas")
    remote = broker.orders["partial-deltas"]
    remote["status"] = "partially_filled"
    remote["filled_qty"] = 2.0
    remote["filled_avg_price"] = 100.0
    armed_client.post("/api/live-orders/reconcile")

    remote["filled_qty"] = 5.0
    remote["filled_avg_price"] = 106.0
    armed_client.post("/api/live-orders/reconcile")
    detail = armed_client.get(f"/api/live-orders/{order['id']}").json()
    assert [fill["quantity"] for fill in detail["fills"]] == [2.0, 3.0]
    assert [fill["price"] for fill in detail["fills"]] == pytest.approx([100.0, 110.0])
    assert sum(fill["quantity"] for fill in detail["fills"]) == 5.0

    armed_client.post("/api/live-orders/reconcile")
    repeated = armed_client.get(f"/api/live-orders/{order['id']}").json()
    assert len(repeated["fills"]) == 2


def test_identical_orders_can_use_distinct_preview_intents(armed_client, broker):
    first = _submit(armed_client, "preview-intent-1")
    second = _submit(armed_client, "preview-intent-2")
    assert first["request_fingerprint"] == second["request_fingerprint"]
    assert first["id"] != second["id"]
    assert broker.submissions == ["preview-intent-1", "preview-intent-2"]


def test_live_quote_timestamp_and_price_drive_the_same_preflight(armed_client, monkeypatch):
    async def quote(url: str, params=None) -> dict[str, object]:
        return {
            "price": 123.45,
            "updated_at": (OPEN_MOMENT - datetime.timedelta(seconds=30)).isoformat(),
        }

    monkeypatch.setattr(main, "_get_json", quote)
    preview = armed_client.post("/api/live-orders/preview", json=_order_payload()).json()
    assert preview["reference_price"] == 123.45
    assert _checks(preview)["price_fresh"]["passed"] is True
    assert "30s old" in _checks(preview)["price_fresh"]["detail"]


async def _duplicate_control_cleanup(database_path, monkeypatch) -> list[dict[str, object]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.execute(text("""
            CREATE TABLE live_trading_control (
                id INTEGER PRIMARY KEY,
                acknowledged BOOLEAN NOT NULL,
                acknowledged_by VARCHAR(120),
                acknowledged_at DATETIME,
                acknowledgement_note TEXT,
                trading_disabled BOOLEAN NOT NULL,
                disabled_reason TEXT,
                disabled_by VARCHAR(120),
                disabled_at DATETIME,
                updated_at DATETIME NOT NULL
            )
        """))
        await connection.execute(text("""
            INSERT INTO live_trading_control
                (id, acknowledged, trading_disabled, updated_at)
            VALUES (1, 1, 0, CURRENT_TIMESTAMP), (2, 1, 0, CURRENT_TIMESTAMP)
        """))
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(main, "async_session", session_factory)
    await main._initialize_live_control()
    async with session_factory() as session:
        controls = (await session.execute(select(LiveTradingControl))).scalars().all()
        result = [
            {
                "id": control.id,
                "acknowledged": control.acknowledged,
                "trading_disabled": control.trading_disabled,
                "disabled_reason": control.disabled_reason,
            }
            for control in controls
        ]
    await engine.dispose()
    return result


def test_duplicate_live_control_rows_are_collapsed_and_disarmed(tmp_path, monkeypatch):
    controls = asyncio.run(_duplicate_control_cleanup(tmp_path / "legacy-controls.db", monkeypatch))
    assert controls == [
        {
            "id": 1,
            "acknowledged": False,
            "trading_disabled": True,
            "disabled_reason": "Duplicate live-control rows detected; operator review required",
        }
    ]
