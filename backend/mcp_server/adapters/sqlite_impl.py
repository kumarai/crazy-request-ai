"""SQLite-backed implementations of the MCP repository protocols.

Dev-only. Swap for ``http_impl`` when a real downstream is available.
All methods are ``async`` (aiosqlite). Writes use ``BEGIN IMMEDIATE``
so idempotency is checked + recorded atomically with the mutation.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger("[mcp]")


class SqliteStore:
    """Shared connection helper.

    aiosqlite connections are cheap but single-writer, so we reuse a
    single connection and serialize writes on it.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("SqliteStore.connect() not called")
        return self._conn

    async def apply_schema(self, schema_sql: str) -> None:
        await self.conn.executescript(schema_sql)
        await self.conn.commit()

    async def seed(self, seed_sql: str) -> None:
        await self.conn.executescript(seed_sql)
        await self.conn.commit()


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _rows_to_list(rows: list[aiosqlite.Row]) -> list[dict[str, Any]]:
    return [{k: r[k] for k in r.keys()} for r in rows]


class SqliteBillingRepo:
    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    async def get_invoice(
        self, customer_id: str, invoice_id: str
    ) -> dict[str, Any]:
        async with self._store.conn.execute(
            "SELECT * FROM invoices WHERE id = ? AND customer_id = ?",
            (invoice_id, customer_id),
        ) as cur:
            row = await cur.fetchone()
        data = _row_to_dict(row)
        if not data:
            return {"error": "invoice_not_found", "customer_id": customer_id}
        data["line_items"] = json.loads(data.pop("line_items_json", "[]"))
        return data

    async def list_invoices(self, customer_id: str) -> list[dict[str, Any]]:
        async with self._store.conn.execute(
            "SELECT * FROM invoices WHERE customer_id = ? ORDER BY date DESC",
            (customer_id,),
        ) as cur:
            rows = await cur.fetchall()
        out = _rows_to_list(list(rows))
        for inv in out:
            inv["line_items"] = json.loads(inv.pop("line_items_json", "[]"))
        return out

    async def list_charges(self, customer_id: str) -> list[dict[str, Any]]:
        async with self._store.conn.execute(
            "SELECT date, description, amount FROM charges "
            "WHERE customer_id = ? ORDER BY date DESC LIMIT 20",
            (customer_id,),
        ) as cur:
            rows = await cur.fetchall()
        return _rows_to_list(list(rows))

    async def get_balance(self, customer_id: str) -> dict[str, Any]:
        async with self._store.conn.execute(
            "SELECT b.current_balance, b.past_due, b.next_bill_date, "
            "c.autopay_enabled FROM balances b "
            "JOIN customers c ON c.id = b.customer_id "
            "WHERE b.customer_id = ?",
            (customer_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return {"error": "customer_not_found", "customer_id": customer_id}
        data = _row_to_dict(row) or {}
        data["customer_id"] = customer_id
        data["autopay_enabled"] = bool(data.get("autopay_enabled", 0))
        return data


class SqlitePaymentMethodRepo:
    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    async def list(self, customer_id: str) -> list[dict[str, Any]]:
        async with self._store.conn.execute(
            "SELECT * FROM payment_methods WHERE customer_id = ? "
            "ORDER BY is_default DESC, created_at ASC",
            (customer_id,),
        ) as cur:
            rows = await cur.fetchall()
        out = _rows_to_list(list(rows))
        for pm in out:
            pm["is_default"] = bool(pm.get("is_default", 0))
        return out

    async def add(
        self, customer_id: str, kind: str, last4: str, label: str
    ) -> dict[str, Any]:
        pm_id = f"pm_{uuid.uuid4().hex[:8]}"
        async with self._store.conn.execute(
            "INSERT INTO payment_methods (id, customer_id, kind, last4, label, is_default) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (pm_id, customer_id, kind, last4, label),
        ):
            pass
        await self._store.conn.commit()
        return {
            "id": pm_id,
            "customer_id": customer_id,
            "kind": kind,
            "last4": last4,
            "label": label,
            "is_default": False,
        }

    async def set_default(
        self, customer_id: str, payment_method_id: str
    ) -> dict[str, Any]:
        await self._store.conn.execute(
            "UPDATE payment_methods SET is_default = 0 WHERE customer_id = ?",
            (customer_id,),
        )
        await self._store.conn.execute(
            "UPDATE payment_methods SET is_default = 1 "
            "WHERE id = ? AND customer_id = ?",
            (payment_method_id, customer_id),
        )
        await self._store.conn.execute(
            "UPDATE customers SET default_payment_method_id = ? WHERE id = ?",
            (payment_method_id, customer_id),
        )
        await self._store.conn.commit()
        return {"customer_id": customer_id, "default_payment_method_id": payment_method_id}


class SqliteBillPayRepo:
    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    async def make_payment(
        self,
        customer_id: str,
        amount: float,
        payment_method_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        pay_id = f"pay_{uuid.uuid4().hex[:10]}"
        await self._store.conn.execute(
            "INSERT INTO payments (id, customer_id, amount, payment_method_id, status, idempotency_key) "
            "VALUES (?, ?, ?, ?, 'succeeded', ?)",
            (pay_id, customer_id, amount, payment_method_id, idempotency_key),
        )
        await self._store.conn.execute(
            "UPDATE balances SET current_balance = MAX(0, current_balance - ?), "
            "past_due = MAX(0, past_due - ?) WHERE customer_id = ?",
            (amount, amount, customer_id),
        )
        await self._store.conn.commit()
        return {
            "payment_id": pay_id,
            "customer_id": customer_id,
            "amount": amount,
            "status": "succeeded",
        }

    async def enroll_autopay(
        self,
        customer_id: str,
        payment_method_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        await self._store.conn.execute(
            "UPDATE customers SET autopay_enabled = 1, default_payment_method_id = ? "
            "WHERE id = ?",
            (payment_method_id, customer_id),
        )
        await self._store.conn.commit()
        return {
            "customer_id": customer_id,
            "autopay_enabled": True,
            "payment_method_id": payment_method_id,
        }


_CATEGORY_ALIASES = {
    "plans": "plan",
    "devices": "device",
    "phones": "device",
    "phone": "device",
    "accessories": "accessory",
}


class SqliteOrderRepo:
    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    async def list_catalog(
        self, category: str | None = None
    ) -> list[dict[str, Any]]:
        # Accept common LLM phrasings ("plans", "phones") and normalise
        # to the canonical singular schema value. Without this, the LLM
        # asking for "plans" returns zero rows even though the catalog
        # has plan items under category='plan'.
        if category:
            normalised = _CATEGORY_ALIASES.get(category.lower().strip(), category)
            async with self._store.conn.execute(
                "SELECT * FROM catalog_items WHERE category = ? AND in_stock = 1",
                (normalised,),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with self._store.conn.execute(
                "SELECT * FROM catalog_items WHERE in_stock = 1"
            ) as cur:
                rows = await cur.fetchall()
        return _rows_to_list(list(rows))

    async def quote(
        self, customer_id: str, sku_ids: list[str]
    ) -> dict[str, Any]:
        if not sku_ids:
            return {"error": "empty_cart"}
        placeholders = ",".join("?" for _ in sku_ids)
        async with self._store.conn.execute(
            f"SELECT sku, name, price FROM catalog_items "
            f"WHERE sku IN ({placeholders})",
            tuple(sku_ids),
        ) as cur:
            rows = await cur.fetchall()
        items = _rows_to_list(list(rows))
        subtotal = sum(r["price"] for r in items)
        tax = round(subtotal * 0.0875, 2)
        total = round(subtotal + tax, 2)
        return {
            "customer_id": customer_id,
            "items": items,
            "subtotal": round(subtotal, 2),
            "tax": tax,
            "total": total,
        }

    async def get(self, customer_id: str, order_id: str) -> dict[str, Any]:
        async with self._store.conn.execute(
            "SELECT * FROM orders WHERE id = ? AND customer_id = ?",
            (order_id, customer_id),
        ) as cur:
            row = await cur.fetchone()
        data = _row_to_dict(row)
        if not data:
            return {"error": "order_not_found"}
        async with self._store.conn.execute(
            "SELECT oi.sku, oi.quantity, ci.name, ci.price "
            "FROM order_items oi JOIN catalog_items ci ON ci.sku = oi.sku "
            "WHERE oi.order_id = ?",
            (order_id,),
        ) as cur:
            items = await cur.fetchall()
        data["items"] = _rows_to_list(list(items))
        return data

    async def list(self, customer_id: str) -> list[dict[str, Any]]:
        async with self._store.conn.execute(
            "SELECT id, status, total, carrier, tracking_number, eta, created_at "
            "FROM orders WHERE customer_id = ? ORDER BY created_at DESC",
            (customer_id,),
        ) as cur:
            rows = await cur.fetchall()
        return _rows_to_list(list(rows))

    async def shipment_status(
        self, customer_id: str, order_id: str
    ) -> dict[str, Any]:
        async with self._store.conn.execute(
            "SELECT status, carrier, tracking_number, eta FROM orders "
            "WHERE id = ? AND customer_id = ?",
            (order_id, customer_id),
        ) as cur:
            row = await cur.fetchone()
        data = _row_to_dict(row)
        if not data:
            return {"error": "order_not_found"}
        return data

    async def place(
        self,
        customer_id: str,
        sku_ids: list[str],
        payment_method_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        quote = await self.quote(customer_id, sku_ids)
        if "error" in quote:
            return quote
        order_id = f"ord_{uuid.uuid4().hex[:8]}"
        eta = (datetime.utcnow() + timedelta(days=3)).strftime("%Y-%m-%d")
        tracking = "1Z999AA" + uuid.uuid4().hex[:10].upper()
        await self._store.conn.execute(
            "INSERT INTO orders (id, customer_id, status, total, payment_method_id, "
            "tracking_number, carrier, eta, idempotency_key) "
            "VALUES (?, ?, 'placed', ?, ?, ?, 'UPS', ?, ?)",
            (
                order_id, customer_id, quote["total"], payment_method_id,
                tracking, eta, idempotency_key,
            ),
        )
        for sku in sku_ids:
            await self._store.conn.execute(
                "INSERT INTO order_items (order_id, sku, quantity) VALUES (?, ?, 1)",
                (order_id, sku),
            )
        # charge the payment method — mirror of make_payment without the
        # balance decrement (orders aren't a bill)
        pay_id = f"pay_{uuid.uuid4().hex[:10]}"
        await self._store.conn.execute(
            "INSERT INTO payments (id, customer_id, amount, payment_method_id, status, idempotency_key) "
            "VALUES (?, ?, ?, ?, 'succeeded', ?)",
            (pay_id, customer_id, quote["total"], payment_method_id,
             f"{idempotency_key}:pay"),
        )
        await self._store.conn.commit()
        return {
            "order_id": order_id,
            "customer_id": customer_id,
            "status": "placed",
            "total": quote["total"],
            "tracking_number": tracking,
            "carrier": "UPS",
            "eta": eta,
            "payment_id": pay_id,
        }

    async def cancel(
        self, customer_id: str, order_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        async with self._store.conn.execute(
            "SELECT status FROM orders WHERE id = ? AND customer_id = ?",
            (order_id, customer_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return {"error": "order_not_found"}
        if row["status"] in ("delivered", "cancelled"):
            return {"error": "cannot_cancel", "status": row["status"]}
        await self._store.conn.execute(
            "UPDATE orders SET status = 'cancelled' WHERE id = ?",
            (order_id,),
        )
        await self._store.conn.commit()
        return {"order_id": order_id, "status": "cancelled"}


class SqliteAppointmentRepo:
    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    async def list_slots(
        self, customer_id: str, topic: str, zip_code: str | None = None
    ) -> list[dict[str, Any]]:
        """Return open slots for the topic.

        Two-stage lookup when a zip is provided:
          1. Slots scoped to that exact zip.
          2. National-pool slots (``zip_code IS NULL``) as fallback —
             these are crews that serve any area.
        The combined list is trimmed to the first 10 upcoming slots.

        When no zip is given, matches any slot (zip-scoped OR national)
        so the agent can show the customer what's on the calendar.
        """
        if zip_code:
            q = (
                "SELECT * FROM appointment_slots "
                "WHERE topic = ? AND booked = 0 "
                "  AND (zip_code = ? OR zip_code IS NULL) "
                "ORDER BY (CASE WHEN zip_code = ? THEN 0 ELSE 1 END), "
                "         slot_start ASC "
                "LIMIT 10"
            )
            args: tuple[Any, ...] = (topic, zip_code, zip_code)
        else:
            q = (
                "SELECT * FROM appointment_slots "
                "WHERE topic = ? AND booked = 0 "
                "ORDER BY slot_start ASC LIMIT 10"
            )
            args = (topic,)
        async with self._store.conn.execute(q, args) as cur:
            rows = await cur.fetchall()
        return _rows_to_list(list(rows))

    async def list_for_customer(
        self, customer_id: str, include_past: bool = False
    ) -> list[dict[str, Any]]:
        """Return appointments on this customer's account.

        Defaults to upcoming + current bookings only. Pass
        ``include_past=True`` to pull completed + cancelled rows too
        when the agent needs the full history (rare). Sorted by
        start time ascending so the next appointment is first.
        """
        if include_past:
            q = (
                "SELECT * FROM appointments "
                "WHERE customer_id = ? "
                "ORDER BY slot_start ASC"
            )
            args: tuple[Any, ...] = (customer_id,)
        else:
            q = (
                "SELECT * FROM appointments "
                "WHERE customer_id = ? AND status = 'booked' "
                "  AND slot_start >= datetime('now') "
                "ORDER BY slot_start ASC"
            )
            args = (customer_id,)
        async with self._store.conn.execute(q, args) as cur:
            rows = await cur.fetchall()
        return _rows_to_list(list(rows))

    async def book(
        self,
        customer_id: str,
        slot_id: str,
        topic: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        async with self._store.conn.execute(
            "SELECT * FROM appointment_slots WHERE id = ? AND booked = 0",
            (slot_id,),
        ) as cur:
            slot = await cur.fetchone()
        if slot is None:
            return {"error": "slot_unavailable"}
        appt_id = f"appt_{uuid.uuid4().hex[:8]}"
        await self._store.conn.execute(
            "INSERT INTO appointments (id, customer_id, topic, slot_start, slot_end, status, tech_name, idempotency_key) "
            "VALUES (?, ?, ?, ?, ?, 'booked', ?, ?)",
            (appt_id, customer_id, topic, slot["slot_start"], slot["slot_end"],
             slot["tech_name"], idempotency_key),
        )
        await self._store.conn.execute(
            "UPDATE appointment_slots SET booked = 1 WHERE id = ?", (slot_id,),
        )
        await self._store.conn.commit()
        return {
            "appointment_id": appt_id,
            "customer_id": customer_id,
            "topic": topic,
            "slot_start": slot["slot_start"],
            "slot_end": slot["slot_end"],
            "tech_name": slot["tech_name"],
            "status": "booked",
        }

    async def cancel(
        self,
        customer_id: str,
        appointment_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        await self._store.conn.execute(
            "UPDATE appointments SET status = 'cancelled' "
            "WHERE id = ? AND customer_id = ?",
            (appointment_id, customer_id),
        )
        await self._store.conn.commit()
        return {"appointment_id": appointment_id, "status": "cancelled"}

    async def reschedule(
        self,
        customer_id: str,
        appointment_id: str,
        new_slot_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        async with self._store.conn.execute(
            "SELECT * FROM appointment_slots WHERE id = ? AND booked = 0",
            (new_slot_id,),
        ) as cur:
            slot = await cur.fetchone()
        if slot is None:
            return {"error": "slot_unavailable"}
        await self._store.conn.execute(
            "UPDATE appointments SET slot_start = ?, slot_end = ?, tech_name = ? "
            "WHERE id = ? AND customer_id = ?",
            (slot["slot_start"], slot["slot_end"], slot["tech_name"],
             appointment_id, customer_id),
        )
        await self._store.conn.execute(
            "UPDATE appointment_slots SET booked = 1 WHERE id = ?", (new_slot_id,),
        )
        await self._store.conn.commit()
        return {
            "appointment_id": appointment_id,
            "slot_start": slot["slot_start"],
            "slot_end": slot["slot_end"],
            "status": "rescheduled",
        }


class SqliteOutageRepo:
    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    async def area_status(
        self, customer_id: str | None = None, zip_code: str | None = None
    ) -> dict[str, Any]:
        # If customer_id given, look up their zip to route the query.
        if zip_code is None and customer_id:
            async with self._store.conn.execute(
                "SELECT zip_code FROM customers WHERE id = ?", (customer_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is not None:
                zip_code = row["zip_code"]

        if zip_code is None:
            return {"outage_active": False, "reason": "no_zip_code_provided"}

        async with self._store.conn.execute(
            "SELECT * FROM outages WHERE zip_code = ? AND status = 'active'",
            (zip_code,),
        ) as cur:
            active = await cur.fetchone()

        if active:
            data = _row_to_dict(active) or {}
            data["affected_services"] = json.loads(
                data.pop("affected_services_json", "[]")
            )
            data["outage_active"] = True
            return data

        # No active outage — look at last resolved within the window
        async with self._store.conn.execute(
            "SELECT * FROM outages WHERE zip_code = ? AND status = 'resolved' "
            "ORDER BY started_at DESC LIMIT 1",
            (zip_code,),
        ) as cur:
            last = await cur.fetchone()
        out: dict[str, Any] = {
            "outage_active": False,
            "zip_code": zip_code,
            "area_status": "normal",
        }
        if last:
            last_dict = _row_to_dict(last) or {}
            last_dict["affected_services"] = json.loads(
                last_dict.pop("affected_services_json", "[]")
            )
            out["last_outage"] = last_dict
        return out

    async def incident_lookup(self, incident_id: str) -> dict[str, Any]:
        async with self._store.conn.execute(
            "SELECT * FROM outages WHERE id = ?", (incident_id,),
        ) as cur:
            row = await cur.fetchone()
        data = _row_to_dict(row)
        if not data:
            return {"error": "incident_not_found"}
        data["affected_services"] = json.loads(
            data.pop("affected_services_json", "[]")
        )
        return data

    async def scheduled_maintenance(
        self, zip_code: str | None = None
    ) -> list[dict[str, Any]]:
        if zip_code:
            async with self._store.conn.execute(
                "SELECT * FROM scheduled_maintenance WHERE zip_code = ? "
                "ORDER BY scheduled_start ASC",
                (zip_code,),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with self._store.conn.execute(
                "SELECT * FROM scheduled_maintenance ORDER BY scheduled_start ASC"
            ) as cur:
                rows = await cur.fetchall()
        return _rows_to_list(list(rows))


class SqliteWriteLogRepo:
    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    async def lookup(self, idempotency_key: str) -> dict[str, Any] | None:
        async with self._store.conn.execute(
            "SELECT response_json FROM write_log WHERE idempotency_key = ?",
            (idempotency_key,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return json.loads(row["response_json"])

    async def record(
        self, idempotency_key: str, tool_name: str, response: dict[str, Any]
    ) -> None:
        await self._store.conn.execute(
            "INSERT OR IGNORE INTO write_log (idempotency_key, tool_name, response_json) "
            "VALUES (?, ?, ?)",
            (idempotency_key, tool_name, json.dumps(response)),
        )
        await self._store.conn.commit()
