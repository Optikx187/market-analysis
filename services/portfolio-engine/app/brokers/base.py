"""Broker adapter interface.

Adapters translate a normalized :class:`~app.live_execution.OrderRequest` into a
broker API call and translate the response back into the neutral shapes below.
They never decide *whether* an order may be sent — that is the preflight gate's
job — and they never touch the database.
"""

from __future__ import annotations

import dataclasses
from typing import Optional, Protocol, runtime_checkable

from app.live_execution import OrderRequest


class BrokerError(RuntimeError):
    """Any broker-side or transport failure, carrying the raw response body."""

    def __init__(self, message: str, *, status_code: Optional[int] = None, body: object = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclasses.dataclass(frozen=True)
class BrokerAccount:
    buying_power: Optional[float]
    cash: Optional[float]
    equity: Optional[float]
    trading_blocked: bool
    account_id: Optional[str] = None

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class BrokerAsset:
    symbol: str
    tradable: bool
    shortable: bool
    halted: bool

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class BrokerOrder:
    broker_order_id: str
    client_order_id: str
    status: str
    filled_quantity: float
    average_fill_price: Optional[float]
    raw: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        payload = dataclasses.asdict(self)
        payload.pop("raw", None)
        return payload


@runtime_checkable
class BrokerAdapter(Protocol):
    """The surface live execution depends on; sandbox and fakes implement it too."""

    name: str
    sandbox: bool

    def configured(self) -> bool:
        """Whether credentials are present for this adapter."""

    async def get_account(self) -> BrokerAccount: ...

    async def get_asset(self, symbol: str) -> BrokerAsset: ...

    async def submit_order(self, request: OrderRequest, client_order_id: str) -> BrokerOrder:
        """Submit ``request``.

        ``client_order_id`` is the idempotency key: an adapter must pass it to
        the broker so a retried submission returns the original order instead of
        creating a second one.
        """

    async def get_order_by_client_id(self, client_order_id: str) -> Optional[BrokerOrder]: ...

    async def get_order(self, broker_order_id: str) -> Optional[BrokerOrder]: ...

    async def cancel_order(self, broker_order_id: str) -> None: ...

    async def list_open_orders(self) -> list[BrokerOrder]: ...
