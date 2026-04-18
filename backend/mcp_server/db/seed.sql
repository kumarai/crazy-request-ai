-- Seed fixtures for MCP server. Customer ids mirror the demo customers
-- used throughout the app so the chat UI works out of the box with
-- X-Customer-Id: cust_001.

INSERT OR REPLACE INTO customers (id, name, plan, zip_code, autopay_enabled, default_payment_method_id) VALUES
    ('cust_001', 'Alex Johnson', 'Ultimate Bundle', '94107', 0, 'pm_001'),
    ('cust_002', 'Priya Patel', 'Gigabit Pro Internet + Mobile', '10001', 1, 'pm_101'),
    ('cust_003', 'Marcus Lee', 'Mobile-only 5G Unlimited', '60601', 0, NULL);

-- Invoices
INSERT OR REPLACE INTO invoices (id, customer_id, date, due_date, total, status, line_items_json) VALUES
    ('inv_001_apr', 'cust_001', '2026-04-01', '2026-04-15', 254.97, 'unpaid',
     '[{"description":"Gigabit Pro Internet","amount":79.99},{"description":"Ultimate Entertainment TV","amount":89.99},{"description":"5G Unlimited Mobile","amount":55.00},{"description":"Unlimited Talk Voice","amount":29.99}]'),
    ('inv_001_mar', 'cust_001', '2026-03-01', '2026-03-15', 254.97, 'paid', '[]'),
    ('inv_002_apr', 'cust_002', '2026-04-01', '2026-04-15', 134.98, 'paid',
     '[{"description":"Gigabit Pro Internet","amount":79.99},{"description":"5G Unlimited Mobile","amount":55.00}]'),
    ('inv_003_apr', 'cust_003', '2026-04-01', '2026-04-15', 55.00, 'unpaid', '[]');

-- Charges
INSERT OR REPLACE INTO charges (id, customer_id, date, description, amount) VALUES
    ('chg_001', 'cust_001', '2026-04-01', 'Monthly service', 254.97),
    ('chg_002', 'cust_001', '2026-03-15', 'Late fee', 10.00),
    ('chg_003', 'cust_001', '2026-03-01', 'Monthly service', 254.97),
    ('chg_004', 'cust_002', '2026-04-01', 'Monthly service', 134.98),
    ('chg_005', 'cust_003', '2026-04-01', 'Monthly service', 55.00);

-- Balances
INSERT OR REPLACE INTO balances (customer_id, current_balance, past_due, next_bill_date) VALUES
    ('cust_001', 264.97, 10.00, '2026-05-01'),
    ('cust_002', 0.00, 0.00, '2026-05-01'),
    ('cust_003', 55.00, 0.00, '2026-05-01');

-- Payment methods
INSERT OR REPLACE INTO payment_methods (id, customer_id, kind, last4, label, is_default) VALUES
    ('pm_001', 'cust_001', 'card', '4242', 'Visa •••• 4242', 1),
    ('pm_002', 'cust_001', 'bank', '9921', 'Chase checking', 0),
    ('pm_101', 'cust_002', 'card', '0005', 'Amex •••• 0005', 1);

-- Catalog (images point at a public placeholder CDN for dev)
INSERT OR REPLACE INTO catalog_items (sku, name, category, price, summary, image_url, in_stock) VALUES
    ('sku_iphone15', 'iPhone 15 Pro 256GB', 'device', 999.00,
     'Titanium body, A17 Pro chip, 48MP camera.',
     'https://images.unsplash.com/photo-1695048133142-1a20484d2569?w=400', 1),
    ('sku_galaxy24', 'Samsung Galaxy S24 256GB', 'device', 899.00,
     'AI-powered Galaxy flagship with Dynamic AMOLED 2X display.',
     'https://images.unsplash.com/photo-1610945415295-d9bbf067e59c?w=400', 1),
    ('sku_pixel9', 'Google Pixel 9 128GB', 'device', 699.00,
     'Gemini AI built-in. Tensor G4 chip.',
     'https://images.unsplash.com/photo-1598300042247-d088f8ab3a91?w=400', 1),
    ('sku_plan_ultimate', 'Ultimate Bundle (Internet + TV + Mobile + Voice)', 'plan', 254.97,
     'Gigabit internet, premium TV, unlimited 5G, unlimited talk.',
     'https://images.unsplash.com/photo-1587614382346-4ec70e388b28?w=400', 1),
    ('sku_plan_gig', 'Gigabit Pro Internet', 'plan', 79.99,
     '1 Gbps down / 500 Mbps up fiber. No data cap.',
     'https://images.unsplash.com/photo-1551808525-51a94da548ce?w=400', 1),
    ('sku_plan_5g', '5G Unlimited Mobile', 'plan', 55.00,
     'Unlimited 5G data, talk, and text. Includes hotspot 30 GB.',
     'https://images.unsplash.com/photo-1512428559087-560fa5ceab42?w=400', 1),
    ('sku_router_ax6', 'Wi-Fi 6 Mesh Router (3-pack)', 'accessory', 299.00,
     'Tri-band Wi-Fi 6 mesh covering up to 5,500 sq ft.',
     'https://images.unsplash.com/photo-1606904825846-647eb07f5be2?w=400', 1);

-- Orders
INSERT OR REPLACE INTO orders (id, customer_id, status, total, payment_method_id, tracking_number, carrier, eta, idempotency_key) VALUES
    ('ord_1001', 'cust_001', 'shipped', 999.00, 'pm_001', '1Z999AA10123456784', 'UPS', '2026-04-19', 'seed_ord_1001'),
    ('ord_1002', 'cust_002', 'delivered', 299.00, 'pm_101', '1Z999AA10123456792', 'UPS', '2026-04-10', 'seed_ord_1002');

INSERT OR REPLACE INTO order_items (order_id, sku, quantity) VALUES
    ('ord_1001', 'sku_iphone15', 1),
    ('ord_1002', 'sku_router_ax6', 1);

-- Appointment slots. Three flavours so demos on any zip work:
--   • zip-scoped slots (94107, 10001, 60601, 80015) — a technician
--     with regional coverage
--   • NULL-zip "national pool" slots — install crews that serve any
--     area. Used as the fallback when a specific zip has nothing.
--   • ``tv_setup`` + ``tech_visit`` variants for more realistic demos.
INSERT OR REPLACE INTO appointment_slots (id, topic, zip_code, slot_start, slot_end, tech_name, booked) VALUES
    -- Zip-scoped
    ('slot_001', 'install', '94107', '2026-04-19 09:00', '2026-04-19 11:00', 'Jamie Smith', 0),
    ('slot_002', 'install', '94107', '2026-04-19 13:00', '2026-04-19 15:00', 'Jamie Smith', 0),
    ('slot_003', 'tech_visit', '94107', '2026-04-20 10:00', '2026-04-20 12:00', 'Dana Kim', 0),
    ('slot_004', 'tech_visit', '10001', '2026-04-20 14:00', '2026-04-20 16:00', 'Priya Singh', 0),
    ('slot_005', 'install', '60601', '2026-04-21 09:00', '2026-04-21 11:00', 'Leo Garcia', 0),
    ('slot_006', 'install', '80015', '2026-04-20 09:00', '2026-04-20 11:00', 'Morgan Reed', 0),
    ('slot_007', 'install', '80015', '2026-04-20 13:00', '2026-04-20 15:00', 'Morgan Reed', 0),
    ('slot_008', 'tech_visit', '80015', '2026-04-22 10:00', '2026-04-22 12:00', 'Sky Patel', 0),
    -- National pool (zip_code NULL) — always available as fallback
    ('slot_np_001', 'install', NULL, '2026-04-21 09:00', '2026-04-21 11:00', 'National install crew', 0),
    ('slot_np_002', 'install', NULL, '2026-04-21 13:00', '2026-04-21 15:00', 'National install crew', 0),
    ('slot_np_003', 'install', NULL, '2026-04-22 09:00', '2026-04-22 11:00', 'National install crew', 0),
    ('slot_np_004', 'tech_visit', NULL, '2026-04-23 10:00', '2026-04-23 12:00', 'Remote diagnostics', 0),
    ('slot_np_005', 'tech_visit', NULL, '2026-04-23 14:00', '2026-04-23 16:00', 'Remote diagnostics', 0),
    ('slot_np_006', 'tv_setup', NULL, '2026-04-24 11:00', '2026-04-24 13:00', 'TV setup crew', 0),
    ('slot_np_007', 'tv_setup', NULL, '2026-04-25 11:00', '2026-04-25 13:00', 'TV setup crew', 0);

-- Outages
INSERT OR REPLACE INTO outages (id, zip_code, status, cause, started_at, eta_resolution, affected_services_json) VALUES
    ('out_001', '60601', 'active', 'fiber cut near substation 7; crew dispatched',
     '2026-04-17 08:20', '2026-04-17 14:00', '["internet","tv"]'),
    ('out_002', '94107', 'resolved', 'planned maintenance',
     '2026-04-10 02:00', '2026-04-10 04:30', '["internet"]');

UPDATE outages SET resolved_at = '2026-04-10 04:15' WHERE id = 'out_002';

INSERT OR REPLACE INTO scheduled_maintenance (id, zip_code, summary, scheduled_start, scheduled_end) VALUES
    ('maint_001', '94107', 'Core router firmware upgrade — expect 5-min reconnect',
     '2026-04-22 02:00', '2026-04-22 04:00'),
    ('maint_002', '10001', 'Fiber terminal replacement — service interruption window',
     '2026-04-25 01:00', '2026-04-25 05:00');
