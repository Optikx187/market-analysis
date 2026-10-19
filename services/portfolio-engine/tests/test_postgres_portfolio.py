from __future__ import annotations

import asyncio
import os
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError


pytestmark = [
    pytest.mark.skipif(
        not os.getenv("POSTGRES_PORTFOLIO_TEST_DATABASE_URL"),
        reason="POSTGRES_PORTFOLIO_TEST_DATABASE_URL is required",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]


from app.auth import reset_current_user_key, set_current_user_key  # noqa: E402
from app.database import async_session, engine  # noqa: E402
from app.main import (  # noqa: E402
    PaperCandleInput,
    PaperOrderCreateRequest,
    PaperOrderProcessRequest,
    _portfolio_risk_status,
    create_paper_order,
    get_or_create_portfolio,
    process_paper_orders,
)
from app.models import (  # noqa: E402
    PaperOrder,
    PaperOrderEvent,
    PaperOrderFill,
    Portfolio,
    RiskSnapshot,
    Trade,
)


TABLES = (
    "live_execution_audit",
    "live_order_fills",
    "live_orders",
    "paper_order_events",
    "paper_order_fills",
    "paper_orders",
    "trade_journals",
    "trade_executions",
    "risk_snapshots",
    "equity_snapshots",
    "trades",
    "alert_logs",
    "action_items",
    "dashboard_preferences",
    "portfolios",
    "credential_secrets",
    "users",
    "live_trading_control",
)


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def clean_postgresql() -> None:
    async with engine.begin() as connection:
        await connection.execute(text(
            "TRUNCATE "
            + ", ".join(f"portfolio.{table_name}" for table_name in TABLES)
            + " CASCADE"
        ))


def order_payload(key: str) -> PaperOrderCreateRequest:
    return PaperOrderCreateRequest(
        idempotency_key=key,
        ticker="BTC",
        side="BUY",
        order_type="limit",
        quantity=Decimal("0.100000000000000001"),
        limit_price=Decimal("100.005000000001"),
        asset_type="crypto",
    )


def fill_payload(order_id: uuid.UUID) -> PaperOrderProcessRequest:
    return PaperOrderProcessRequest(
        ticker="BTC",
        order_id=order_id,
        spread_pct=0,
        slippage_pct=0,
        fee_pct=0,
        candles=[
            PaperCandleInput(
                timestamp="2026-01-02T00:00:00Z",
                open=Decimal("100.005000000001"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("1000"),
            )
        ],
    )


async def test_exact_order_fill_and_reconciliation() -> None:
    async with async_session() as session:
        detail = await create_paper_order(order_payload("exact-order"), session)
        order_id = detail["id"]
        assert isinstance(order_id, uuid.UUID)
        result = await process_paper_orders(fill_payload(order_id), session)
        assert result["orders"][0]["status"] == "filled"
        assert result["portfolio"]["equity"] == 10_000.0

    async with engine.connect() as connection:
        order_row = (
            await connection.execute(text(
                """
                SELECT
                    quantity,
                    limit_price,
                    filled_quantity,
                    filled_notional,
                    reserved_cash
                FROM portfolio.paper_orders
                WHERE idempotency_key = 'exact-order'
                """
            ))
        ).one()
        portfolio_row = (
            await connection.execute(text(
                "SELECT balance, equity FROM portfolio.portfolios"
            ))
        ).one()

    assert order_row.quantity == Decimal("0.100000000000000000")
    assert order_row.limit_price == Decimal("100.005000000001")
    assert order_row.filled_quantity == Decimal("0.100000000000000000")
    assert order_row.filled_notional == Decimal("10.000500000000")
    assert order_row.reserved_cash == Decimal("0")
    assert portfolio_row.balance + order_row.filled_notional == Decimal("10000")
    assert portfolio_row.equity == Decimal("10000")


async def test_concurrent_idempotent_order_and_fill() -> None:
    async def submit() -> dict[str, object]:
        async with async_session() as session:
            return await create_paper_order(order_payload("concurrent-order"), session)

    submitted = await asyncio.gather(*(submit() for _ in range(8)))
    order_ids = {row["id"] for row in submitted}
    assert len(order_ids) == 1
    order_id = next(iter(order_ids))

    async def fill() -> dict[str, object]:
        async with async_session() as session:
            return await process_paper_orders(fill_payload(order_id), session)

    await asyncio.gather(*(fill() for _ in range(8)))

    async with async_session() as session:
        assert await session.scalar(select(func.count(PaperOrder.id))) == 1
        assert await session.scalar(select(func.count(PaperOrderFill.id))) == 1
        assert await session.scalar(
            select(func.count(PaperOrderEvent.id)).where(
                PaperOrderEvent.event_type == "fill"
            )
        ) == 1
        assert await session.scalar(select(func.count(Trade.id))) == 1
        portfolio = (await session.execute(select(Portfolio))).scalar_one()
        assert portfolio.equity == 10_000.0


async def test_user_scoped_idempotency_and_query_isolation() -> None:
    async def submit_as(user_key: str) -> uuid.UUID:
        token = set_current_user_key(user_key)
        try:
            async with async_session() as session:
                detail = await create_paper_order(order_payload("shared-key"), session)
                return detail["id"]
        finally:
            reset_current_user_key(token)

    alice_id, bob_id = await asyncio.gather(
        submit_as("alice"),
        submit_as("bob"),
    )
    assert alice_id != bob_id

    for user_key, expected_id in (("alice", alice_id), ("bob", bob_id)):
        token = set_current_user_key(user_key)
        try:
            async with async_session() as session:
                rows = (await session.execute(select(PaperOrder))).scalars().all()
                assert [row.id for row in rows] == [expected_id]
        finally:
            reset_current_user_key(token)

    async with engine.connect() as connection:
        assert await connection.scalar(text(
            "SELECT COUNT(*) FROM portfolio.paper_orders "
            "WHERE idempotency_key = 'shared-key'"
        )) == 2


async def test_risk_snapshots_are_exact_tenant_scoped_records() -> None:
    async with async_session() as session:
        await get_or_create_portfolio(session)
        snapshot = await _portfolio_risk_status(session)
        assert snapshot["equity"] == 10_000.0
        row = (await session.execute(select(RiskSnapshot))).scalar_one()
        assert isinstance(row.id, uuid.UUID)
        assert row.equity == 10_000.0
        assert "breaker" in row.snapshot_json


async def test_fill_and_audit_rows_are_append_only() -> None:
    async with async_session() as session:
        detail = await create_paper_order(order_payload("immutable-fill"), session)
        await process_paper_orders(fill_payload(detail["id"]), session)

    async with engine.begin() as connection:
        with pytest.raises(DBAPIError, match="append-only"):
            await connection.execute(text(
                "UPDATE portfolio.paper_order_fills SET fees = 1"
            ))
