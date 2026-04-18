"""Repository protocols for the MCP server.

Each domain has one protocol. Two concrete impls live next to this
file: ``sqlite_impl`` (dev, local JSON-ish) and ``http_impl``
(stubs a real downstream API call). The factory picks based on
``MCP_BACKEND``.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BillingRepo(Protocol):
    async def get_invoice(
        self, customer_id: str, invoice_id: str
    ) -> dict[str, Any]: ...

    async def list_invoices(self, customer_id: str) -> list[dict[str, Any]]: ...

    async def list_charges(self, customer_id: str) -> list[dict[str, Any]]: ...

    async def get_balance(self, customer_id: str) -> dict[str, Any]: ...


@runtime_checkable
class PaymentMethodRepo(Protocol):
    async def list(self, customer_id: str) -> list[dict[str, Any]]: ...

    async def add(
        self, customer_id: str, kind: str, last4: str, label: str
    ) -> dict[str, Any]: ...

    async def set_default(
        self, customer_id: str, payment_method_id: str
    ) -> dict[str, Any]: ...


@runtime_checkable
class BillPayRepo(Protocol):
    async def make_payment(
        self,
        customer_id: str,
        amount: float,
        payment_method_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    async def enroll_autopay(
        self,
        customer_id: str,
        payment_method_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...


@runtime_checkable
class OrderRepo(Protocol):
    async def list_catalog(
        self, category: str | None = None
    ) -> list[dict[str, Any]]: ...

    async def quote(
        self, customer_id: str, sku_ids: list[str]
    ) -> dict[str, Any]: ...

    async def get(self, customer_id: str, order_id: str) -> dict[str, Any]: ...

    async def list(self, customer_id: str) -> list[dict[str, Any]]: ...

    async def shipment_status(
        self, customer_id: str, order_id: str
    ) -> dict[str, Any]: ...

    async def place(
        self,
        customer_id: str,
        sku_ids: list[str],
        payment_method_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    async def cancel(
        self, customer_id: str, order_id: str, idempotency_key: str
    ) -> dict[str, Any]: ...


@runtime_checkable
class AppointmentRepo(Protocol):
    async def list_slots(
        self, customer_id: str, topic: str, zip_code: str | None = None
    ) -> list[dict[str, Any]]: ...

    async def list_for_customer(
        self, customer_id: str, include_past: bool = False
    ) -> list[dict[str, Any]]: ...

    async def book(
        self,
        customer_id: str,
        slot_id: str,
        topic: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    async def cancel(
        self,
        customer_id: str,
        appointment_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    async def reschedule(
        self,
        customer_id: str,
        appointment_id: str,
        new_slot_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...


@runtime_checkable
class OutageRepo(Protocol):
    async def area_status(
        self, customer_id: str | None = None, zip_code: str | None = None
    ) -> dict[str, Any]: ...

    async def incident_lookup(self, incident_id: str) -> dict[str, Any]: ...

    async def scheduled_maintenance(
        self, zip_code: str | None = None
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class WriteLogRepo(Protocol):
    """Idempotency store — every write tool checks here first.

    The ``idempotency_key`` is derived server-side as
    ``sha256(conversation_id + tool_name + inputs_json)``. A replay
    returns the stored response verbatim, so double-submits never
    double-charge / double-book.
    """

    async def lookup(self, idempotency_key: str) -> dict[str, Any] | None: ...

    async def record(
        self, idempotency_key: str, tool_name: str, response: dict[str, Any]
    ) -> None: ...
