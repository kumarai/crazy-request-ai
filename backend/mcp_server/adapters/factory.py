"""Adapter factory: pick SQLite or HTTP based on ``MCP_BACKEND``."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from mcp_server.adapters.base import (
    AppointmentRepo,
    BillPayRepo,
    BillingRepo,
    OrderRepo,
    OutageRepo,
    PaymentMethodRepo,
    WriteLogRepo,
)
from mcp_server.adapters.http_impl import (
    HttpAppointmentRepo,
    HttpBillPayRepo,
    HttpBillingRepo,
    HttpOrderRepo,
    HttpOutageRepo,
    HttpPaymentMethodRepo,
)
from mcp_server.adapters.sqlite_impl import (
    SqliteAppointmentRepo,
    SqliteBillPayRepo,
    SqliteBillingRepo,
    SqliteOrderRepo,
    SqliteOutageRepo,
    SqlitePaymentMethodRepo,
    SqliteStore,
    SqliteWriteLogRepo,
)
from mcp_server.config import McpSettings


@dataclass
class Repos:
    billing: BillingRepo
    payment_method: PaymentMethodRepo
    bill_pay: BillPayRepo
    order: OrderRepo
    appointment: AppointmentRepo
    outage: OutageRepo
    write_log: WriteLogRepo
    # retained so main.py can close on shutdown
    _sqlite_store: SqliteStore | None = None
    _http_client: httpx.AsyncClient | None = None


async def build_repos(settings: McpSettings) -> Repos:
    if settings.backend == "sqlite":
        store = SqliteStore(settings.sqlite_path)
        await store.connect()
        return Repos(
            billing=SqliteBillingRepo(store),
            payment_method=SqlitePaymentMethodRepo(store),
            bill_pay=SqliteBillPayRepo(store),
            order=SqliteOrderRepo(store),
            appointment=SqliteAppointmentRepo(store),
            outage=SqliteOutageRepo(store),
            write_log=SqliteWriteLogRepo(store),
            _sqlite_store=store,
        )

    if settings.backend == "http":
        client = httpx.AsyncClient(timeout=10.0)
        # A missing downstream URL on http backend is fatal — no silent
        # fallback, since that would mask prod misconfiguration.
        return Repos(
            billing=HttpBillingRepo(
                settings.downstream_billing_url or "", client
            ),
            payment_method=HttpPaymentMethodRepo(
                settings.downstream_billing_url or "", client
            ),
            bill_pay=HttpBillPayRepo(
                settings.downstream_billing_url or "", client
            ),
            order=HttpOrderRepo(
                settings.downstream_orders_url or "", client
            ),
            appointment=HttpAppointmentRepo(
                settings.downstream_appointments_url or "", client
            ),
            outage=HttpOutageRepo(
                settings.downstream_outage_url or "", client
            ),
            # write_log stays local even on http backend — idempotency
            # is a client-of-downstream concern, not a downstream feature.
            write_log=SqliteWriteLogRepo(SqliteStore(settings.sqlite_path)),
            _http_client=client,
        )

    raise ValueError(f"Unknown MCP_BACKEND: {settings.backend!r}")


async def close_repos(repos: Repos) -> None:
    if repos._sqlite_store is not None:
        await repos._sqlite_store.close()
    if repos._http_client is not None:
        await repos._http_client.aclose()
