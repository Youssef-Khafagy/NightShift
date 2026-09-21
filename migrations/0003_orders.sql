-- Orders. status moves placed -> paid, or placed -> failed.
CREATE TABLE IF NOT EXISTS orders (
    order_id    UUID PRIMARY KEY,
    customer_id TEXT NOT NULL,
    status      TEXT NOT NULL,
    total_cents INTEGER NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
