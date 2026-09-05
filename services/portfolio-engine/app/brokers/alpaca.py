"""Alpaca broker adapter.

Defaults to the Alpaca paper (sandbox) endpoint. ``sandbox`` is derived from the
base URL rather than configured separately so a production host can never be
described as a sandbox in the UI or audit log.
"""

from __future__ import annotations

from typing import Optional

import httpx

from app.brokers.base import BrokerAccount, BrokerAsset, BrokerError, BrokerOrder
from app.live_execution import LIMIT, MARKET, OrderRequest, STOP, STOP_LIMIT, normalize_status

SANDBOX_HOSTS = ("paper-api.alpaca.markets", "broker-api.sandbox.alpaca.markets", "localhost", "127.0.0.1")

ORDER_TYPE_MAP = {MARKET: "market", LIMIT: "limit", STOP: "stop", STOP_LIMIT: "stop_limit"}


def _optional_float(value: object) -> Optional[float]:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class AlpacaBroker:
    name = "alpaca"

    def __init__(self, api_key: str, api_secret: str, base_url: str, timeout: float = 10.0):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def sandbox(self) -> bool:
        return any(host in self.base_url for host in SANDBOX_HOSTS)

    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict[str, object]] = None,
        allow_404: bool = False,
    ) -> object:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(method, url, headers=self._headers, json=json_body)
        except httpx.HTTPError as exc:
            raise BrokerError(f"Alpaca request failed: {exc}") from exc
        if response.status_code == 404 and allow_404:
            return None
        if response.status_code >= 400:
            raise BrokerError(
                f"Alpaca returned HTTP {response.status_code}",
                status_code=response.status_code,
                body=response.text,
            )
        if not response.content:
            return None
        return response.json()

    def _order(self, payload: dict[str, object]) -> BrokerOrder:
        return BrokerOrder(
            broker_order_id=str(payload.get("id") or ""),
            client_order_id=str(payload.get("client_order_id") or ""),
            status=normalize_status(payload.get("status")),
            filled_quantity=_optional_float(payload.get("filled_qty")) or 0.0,
            average_fill_price=_optional_float(payload.get("filled_avg_price")),
            raw=payload,
        )

    async def get_account(self) -> BrokerAccount:
        payload = await self._request("GET", "/v2/account")
        if not isinstance(payload, dict):
            raise BrokerError("Alpaca account response was not an object")
        return BrokerAccount(
            buying_power=_optional_float(payload.get("buying_power")),
            cash=_optional_float(payload.get("cash")),
            equity=_optional_float(payload.get("equity")),
            trading_blocked=bool(payload.get("trading_blocked")),
            account_id=str(payload.get("id") or "") or None,
        )

    async def get_asset(self, symbol: str) -> BrokerAsset:
        payload = await self._request("GET", f"/v2/assets/{symbol}", allow_404=True)
        if not isinstance(payload, dict):
            raise BrokerError(f"Alpaca does not expose asset {symbol}")
        status = str(payload.get("status") or "").lower()
        return BrokerAsset(
            symbol=str(payload.get("symbol") or symbol),
            tradable=bool(payload.get("tradable")),
            shortable=bool(payload.get("shortable")),
            halted=status not in ("active", ""),
        )

    async def submit_order(self, request: OrderRequest, client_order_id: str) -> BrokerOrder:
        body: dict[str, object] = {
            "symbol": request.ticker,
            "qty": str(request.quantity),
            "side": request.side.lower(),
            "type": ORDER_TYPE_MAP[request.order_type],
            "time_in_force": request.time_in_force,
            "client_order_id": client_order_id,
        }
        if request.limit_price is not None:
            body["limit_price"] = str(request.limit_price)
        if request.stop_price is not None:
            body["stop_price"] = str(request.stop_price)
        payload = await self._request("POST", "/v2/orders", json_body=body)
        if not isinstance(payload, dict):
            raise BrokerError("Alpaca order response was not an object")
        return self._order(payload)

    async def get_order_by_client_id(self, client_order_id: str) -> Optional[BrokerOrder]:
        payload = await self._request(
            "GET",
            f"/v2/orders:by_client_order_id?client_order_id={client_order_id}",
            allow_404=True,
        )
        return self._order(payload) if isinstance(payload, dict) else None

    async def get_order(self, broker_order_id: str) -> Optional[BrokerOrder]:
        payload = await self._request("GET", f"/v2/orders/{broker_order_id}", allow_404=True)
        return self._order(payload) if isinstance(payload, dict) else None

    async def cancel_order(self, broker_order_id: str) -> None:
        await self._request("DELETE", f"/v2/orders/{broker_order_id}", allow_404=True)

    async def list_open_orders(self) -> list[BrokerOrder]:
        payload = await self._request("GET", "/v2/orders?status=open&limit=500")
        if not isinstance(payload, list):
            return []
        return [self._order(row) for row in payload if isinstance(row, dict)]
