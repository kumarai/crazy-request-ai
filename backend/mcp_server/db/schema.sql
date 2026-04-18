-- MCP server schema. SQLite 3. Mirrors the shape of a downstream
-- telecom platform so the adapter layer can swap in HTTP calls later
-- without rewriting tools.

CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    plan TEXT NOT NULL,
    zip_code TEXT,
    autopay_enabled INTEGER NOT NULL DEFAULT 0,
    default_payment_method_id TEXT
);

CREATE TABLE IF NOT EXISTS invoices (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    date TEXT NOT NULL,
    due_date TEXT NOT NULL,
    total REAL NOT NULL,
    status TEXT NOT NULL, -- paid | unpaid | partial
    line_items_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_invoices_customer ON invoices(customer_id);

CREATE TABLE IF NOT EXISTS charges (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    amount REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_charges_customer ON charges(customer_id);

CREATE TABLE IF NOT EXISTS balances (
    customer_id TEXT PRIMARY KEY REFERENCES customers(id),
    current_balance REAL NOT NULL,
    past_due REAL NOT NULL DEFAULT 0,
    next_bill_date TEXT
);

CREATE TABLE IF NOT EXISTS payment_methods (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    kind TEXT NOT NULL, -- card | bank
    last4 TEXT NOT NULL,
    label TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pm_customer ON payment_methods(customer_id);

CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    amount REAL NOT NULL,
    payment_method_id TEXT NOT NULL REFERENCES payment_methods(id),
    status TEXT NOT NULL, -- succeeded | failed | pending
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    idempotency_key TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS catalog_items (
    sku TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL, -- plan | device | accessory
    price REAL NOT NULL,
    summary TEXT NOT NULL,
    image_url TEXT,
    in_stock INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    status TEXT NOT NULL, -- placed | shipped | delivered | cancelled
    total REAL NOT NULL,
    payment_method_id TEXT REFERENCES payment_methods(id),
    tracking_number TEXT,
    carrier TEXT,
    eta TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    idempotency_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);

CREATE TABLE IF NOT EXISTS order_items (
    order_id TEXT NOT NULL REFERENCES orders(id),
    sku TEXT NOT NULL REFERENCES catalog_items(sku),
    quantity INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (order_id, sku)
);

CREATE TABLE IF NOT EXISTS appointments (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    topic TEXT NOT NULL, -- install | tech_visit | tv_setup
    slot_start TEXT NOT NULL,
    slot_end TEXT NOT NULL,
    status TEXT NOT NULL, -- booked | cancelled | completed
    tech_name TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    idempotency_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_appointments_customer ON appointments(customer_id);

CREATE TABLE IF NOT EXISTS appointment_slots (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    zip_code TEXT,
    slot_start TEXT NOT NULL,
    slot_end TEXT NOT NULL,
    tech_name TEXT,
    booked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS outages (
    id TEXT PRIMARY KEY,
    zip_code TEXT NOT NULL,
    status TEXT NOT NULL, -- active | resolved
    cause TEXT,
    started_at TEXT NOT NULL,
    eta_resolution TEXT,
    resolved_at TEXT,
    affected_services_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_outages_zip ON outages(zip_code);

CREATE TABLE IF NOT EXISTS scheduled_maintenance (
    id TEXT PRIMARY KEY,
    zip_code TEXT,
    summary TEXT NOT NULL,
    scheduled_start TEXT NOT NULL,
    scheduled_end TEXT NOT NULL
);

-- Idempotency: every write tool computes a key from
-- ``sha256(conversation_id + tool + inputs_json)`` and looks it up here
-- before mutating. A hit replays the stored response unchanged.
CREATE TABLE IF NOT EXISTS write_log (
    idempotency_key TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
