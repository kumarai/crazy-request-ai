"""HTTP implementations of the MCP repository protocols.

Stubs for production. When real downstream APIs exist, replace each
``raise NotImplementedError`` with an ``httpx`` call. The tool layer
never changes — only the factory needs to flip to ``MCP_BACKEND=http``.
"""
from __future__ import annotations

from typing import Any

import httpx


class HttpBillingRepo:
    def __init__(self, base_url: str, client: httpx.AsyncClient) -> None:
        self._base = base_url.rstrip("/")
        self._client = client

    async def get_invoice(
        self, customer_id: str, invoice_id: str
    ) -> dict[str, Any]:
        r = await self._client.get(
            f"{self._base}/customers/{customer_id}/invoices/{invoice_id}"
        )
        r.raise_for_status()
        return r.json()

    async def list_invoices(self, customer_id: str) -> list[dict[str, Any]]:
        r = await self._client.get(f"{self._base}/customers/{customer_id}/invoices")
        r.raise_for_status()
        return r.json().get("invoices", [])

    async def list_charges(self, customer_id: str) -> list[dict[str, Any]]:
        r = await self._client.get(f"{self._base}/customers/{customer_id}/charges")
        r.raise_for_status()
        return r.json().get("charges", [])

    async def get_balance(self, customer_id: str) -> dict[str, Any]:
        r = await self._client.get(f"{self._base}/customers/{customer_id}/balance")
        r.raise_for_status()
        return r.json()


class _NotImplementedRepo:
    """Placeholder raising a clear error until a real downstream exists."""

    def __init__(self, domain: str) -> None:
        self._domain = domain

    def __getattr__(self, name: str):  # noqa: ANN001
        async def _raise(*args: Any, **kwargs: Any) -> Any:
            raise NotImplementedError(
                f"HTTP adapter for {self._domain}.{name} is not wired yet — "
                f"set MCP_BACKEND=sqlite for dev."
            )
        return _raise


# Until real downstreams are defined, everything except billing delegates
# to the placeholder. Replace as APIs come online.
HttpPaymentMethodRepo = lambda base_url, client: _NotImplementedRepo("payment_method")  # type: ignore[assignment]
HttpBillPayRepo = lambda base_url, client: _NotImplementedRepo("bill_pay")  # type: ignore[assignment]
HttpOrderRepo = lambda base_url, client: _NotImplementedRepo("order")  # type: ignore[assignment]
HttpAppointmentRepo = lambda base_url, client: _NotImplementedRepo("appointment")  # type: ignore[assignment]
HttpOutageRepo = lambda base_url, client: _NotImplementedRepo("outage")  # type: ignore[assignment]
