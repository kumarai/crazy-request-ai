"""FastMCP server: registers all telecom-support tools over streamable HTTP.

Tools are grouped by domain. Every write tool derives its own idempotency
key from ``(conversation_id, tool, inputs)`` and checks ``write_log``
before mutating — replays return the stored response unchanged.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from mcp_server.adapters.factory import Repos

logger = logging.getLogger("[mcp]")


def _idempotency_key(conversation_id: str, tool: str, inputs: dict[str, Any]) -> str:
    """Server-derived idempotency key — inputs are hashed in a stable order."""
    payload = json.dumps(inputs, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256()
    h.update(conversation_id.encode("utf-8"))
    h.update(b"|")
    h.update(tool.encode("utf-8"))
    h.update(b"|")
    h.update(payload.encode("utf-8"))
    return h.hexdigest()


async def _with_idempotency(
    repos: Repos,
    conversation_id: str,
    tool: str,
    inputs: dict[str, Any],
    run,
):
    """Look up prior response by key; if miss, run the write + record it."""
    key = _idempotency_key(conversation_id, tool, inputs)
    prior = await repos.write_log.lookup(key)
    if prior is not None:
        logger.info("idempotent replay: tool=%s key=%s", tool, key[:12])
        return {**prior, "_replayed": True}
    result = await run(key)
    try:
        await repos.write_log.record(key, tool, result)
    except Exception as e:  # pragma: no cover - log and proceed
        logger.error("write_log.record failed: %s", e)
    return result


def _build_transport_security() -> TransportSecuritySettings:
    """Allow-list containerized hostnames.

    Default MCP protection rejects anything other than ``localhost`` and
    ``127.0.0.1`` to block DNS rebinding. Inside docker-compose the
    backend calls ``http://mcp-server:8765/mcp`` — that Host header is
    rejected with ``421 Misdirected Request`` unless we whitelist it.
    ``MCP_ALLOWED_HOSTS`` lets deployments add more hosts (comma-sep).
    """
    defaults = [
        "localhost",
        "127.0.0.1",
        "mcp-server",
        "mcp-server:8765",
        "0.0.0.0",
        "0.0.0.0:8765",
    ]
    extra = os.environ.get("MCP_ALLOWED_HOSTS", "")
    hosts = defaults + [h.strip() for h in extra.split(",") if h.strip()]
    # Origin list: browsers only. Backend→MCP is server-to-server,
    # so no Origin header is sent; leave the list broad in dev.
    origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def build_mcp(repos: Repos) -> FastMCP:
    mcp = FastMCP(
        name="telecom-support-mcp",
        instructions=(
            "Telecom customer-support tools. Read tools return current "
            "account state; write tools (make_payment, enroll_autopay, "
            "place_order, book_appointment, etc.) require a "
            "conversation_id for idempotency. Callers MUST validate the "
            "customer_id against an authenticated session before invoking "
            "any write tool."
        ),
        transport_security=_build_transport_security(),
    )

    # ---------- Billing (read) ----------

    @mcp.tool()
    async def billing_get_invoice(customer_id: str, invoice_id: str) -> dict:
        """Get a specific invoice for a customer."""
        return await repos.billing.get_invoice(customer_id, invoice_id)

    @mcp.tool()
    async def billing_list_invoices(customer_id: str) -> list[dict]:
        """List recent invoices for a customer."""
        return await repos.billing.list_invoices(customer_id)

    @mcp.tool()
    async def billing_list_charges(customer_id: str) -> list[dict]:
        """List recent charges for a customer."""
        return await repos.billing.list_charges(customer_id)

    @mcp.tool()
    async def billing_get_balance(customer_id: str) -> dict:
        """Get current balance + past-due for a customer."""
        return await repos.billing.get_balance(customer_id)

    # ---------- Payment methods (read + write) ----------

    @mcp.tool()
    async def payment_method_list(customer_id: str) -> list[dict]:
        """List saved payment methods (cards + bank accounts) for a customer."""
        return await repos.payment_method.list(customer_id)

    @mcp.tool()
    async def payment_method_add(
        customer_id: str,
        conversation_id: str,
        kind: str,
        last4: str,
        label: str,
    ) -> dict:
        """Add a new payment method. ``kind`` is 'card' or 'bank'."""
        async def _run(_key: str) -> dict:
            return await repos.payment_method.add(customer_id, kind, last4, label)
        return await _with_idempotency(
            repos, conversation_id, "payment_method_add",
            {"customer_id": customer_id, "last4": last4, "kind": kind},
            _run,
        )

    @mcp.tool()
    async def payment_method_set_default(
        customer_id: str, conversation_id: str, payment_method_id: str
    ) -> dict:
        """Mark a saved payment method as the default."""
        async def _run(_key: str) -> dict:
            return await repos.payment_method.set_default(customer_id, payment_method_id)
        return await _with_idempotency(
            repos, conversation_id, "payment_method_set_default",
            {"customer_id": customer_id, "payment_method_id": payment_method_id},
            _run,
        )

    # ---------- Bill pay (write) ----------

    @mcp.tool()
    async def bill_pay_make_payment(
        customer_id: str,
        conversation_id: str,
        amount: float,
        payment_method_id: str,
    ) -> dict:
        """Submit a payment against the customer's balance."""
        async def _run(key: str) -> dict:
            return await repos.bill_pay.make_payment(
                customer_id, amount, payment_method_id, key
            )
        return await _with_idempotency(
            repos, conversation_id, "bill_pay_make_payment",
            {"customer_id": customer_id, "amount": amount,
             "payment_method_id": payment_method_id},
            _run,
        )

    @mcp.tool()
    async def bill_pay_enroll_autopay(
        customer_id: str, conversation_id: str, payment_method_id: str
    ) -> dict:
        """Enroll the customer in autopay using the given payment method."""
        async def _run(key: str) -> dict:
            return await repos.bill_pay.enroll_autopay(
                customer_id, payment_method_id, key
            )
        return await _with_idempotency(
            repos, conversation_id, "bill_pay_enroll_autopay",
            {"customer_id": customer_id, "payment_method_id": payment_method_id},
            _run,
        )

    # ---------- Orders (read + write) ----------

    @mcp.tool()
    async def order_list_catalog(category: str | None = None) -> list[dict]:
        """Browse the product catalog. ``category`` ∈ plan | device | accessory."""
        return await repos.order.list_catalog(category)

    @mcp.tool()
    async def order_quote(customer_id: str, sku_ids: list[str]) -> dict:
        """Price a cart of SKUs. Non-committing."""
        return await repos.order.quote(customer_id, sku_ids)

    @mcp.tool()
    async def order_get(customer_id: str, order_id: str) -> dict:
        """Get a specific order's status + items."""
        return await repos.order.get(customer_id, order_id)

    @mcp.tool()
    async def order_list(customer_id: str) -> list[dict]:
        """List all orders for the customer."""
        return await repos.order.list(customer_id)

    @mcp.tool()
    async def order_shipment_status(customer_id: str, order_id: str) -> dict:
        """Get tracking + ETA for an order's shipment."""
        return await repos.order.shipment_status(customer_id, order_id)

    @mcp.tool()
    async def order_place(
        customer_id: str,
        conversation_id: str,
        sku_ids: list[str],
        payment_method_id: str,
    ) -> dict:
        """Place an order. Committing."""
        async def _run(key: str) -> dict:
            return await repos.order.place(
                customer_id, sku_ids, payment_method_id, key
            )
        return await _with_idempotency(
            repos, conversation_id, "order_place",
            {"customer_id": customer_id, "sku_ids": sorted(sku_ids),
             "payment_method_id": payment_method_id},
            _run,
        )

    @mcp.tool()
    async def order_cancel(
        customer_id: str, conversation_id: str, order_id: str
    ) -> dict:
        """Cancel an order (only valid before shipment)."""
        async def _run(key: str) -> dict:
            return await repos.order.cancel(customer_id, order_id, key)
        return await _with_idempotency(
            repos, conversation_id, "order_cancel",
            {"customer_id": customer_id, "order_id": order_id},
            _run,
        )

    # ---------- Appointments (read + write) ----------

    @mcp.tool()
    async def appointment_list_slots(
        customer_id: str, topic: str, zip_code: str | None = None
    ) -> list[dict]:
        """List open slots. ``topic`` ∈ install | tech_visit | tv_setup."""
        return await repos.appointment.list_slots(customer_id, topic, zip_code)

    @mcp.tool()
    async def appointment_list(
        customer_id: str, include_past: bool = False
    ) -> list[dict]:
        """List appointments already on this customer's account.

        Defaults to upcoming (future, status='booked'). Set
        ``include_past=True`` to also return cancelled + completed rows.
        """
        return await repos.appointment.list_for_customer(
            customer_id, include_past=include_past
        )

    @mcp.tool()
    async def appointment_book(
        customer_id: str,
        conversation_id: str,
        slot_id: str,
        topic: str,
    ) -> dict:
        """Book a slot. Committing."""
        async def _run(key: str) -> dict:
            return await repos.appointment.book(customer_id, slot_id, topic, key)
        return await _with_idempotency(
            repos, conversation_id, "appointment_book",
            {"customer_id": customer_id, "slot_id": slot_id, "topic": topic},
            _run,
        )

    @mcp.tool()
    async def appointment_cancel(
        customer_id: str, conversation_id: str, appointment_id: str
    ) -> dict:
        """Cancel a booked appointment."""
        async def _run(key: str) -> dict:
            return await repos.appointment.cancel(customer_id, appointment_id, key)
        return await _with_idempotency(
            repos, conversation_id, "appointment_cancel",
            {"customer_id": customer_id, "appointment_id": appointment_id},
            _run,
        )

    @mcp.tool()
    async def appointment_reschedule(
        customer_id: str,
        conversation_id: str,
        appointment_id: str,
        new_slot_id: str,
    ) -> dict:
        """Move an appointment to a new slot."""
        async def _run(key: str) -> dict:
            return await repos.appointment.reschedule(
                customer_id, appointment_id, new_slot_id, key
            )
        return await _with_idempotency(
            repos, conversation_id, "appointment_reschedule",
            {"customer_id": customer_id, "appointment_id": appointment_id,
             "new_slot_id": new_slot_id},
            _run,
        )

    # ---------- Outage (read) ----------

    @mcp.tool()
    async def outage_area_status(
        customer_id: str | None = None, zip_code: str | None = None
    ) -> dict:
        """Check whether there is an active outage in the customer's area.

        Returns ``outage_active: true`` plus cause + eta_resolution when
        there is one; otherwise returns ``{outage_active: false}`` and
        the last resolved incident for context.
        """
        return await repos.outage.area_status(customer_id, zip_code)

    @mcp.tool()
    async def outage_incident_lookup(incident_id: str) -> dict:
        """Look up a specific outage incident by id."""
        return await repos.outage.incident_lookup(incident_id)

    @mcp.tool()
    async def outage_scheduled_maintenance(zip_code: str | None = None) -> list[dict]:
        """Upcoming scheduled maintenance windows for the area."""
        return await repos.outage.scheduled_maintenance(zip_code)

    return mcp
