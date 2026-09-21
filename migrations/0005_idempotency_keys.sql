-- Checkout requires an idempotency key, and this table is what enforces it.
--
-- The key is the primary key, deliberately, rather than a UNIQUE constraint
-- on a column of orders. A primary key is the one uniqueness guarantee every
-- distributed SQL engine supports, so the design does not depend on whether
-- DSQL supports unique secondary indexes.
--
-- The mechanism: checkout inserts here inside the same transaction that
-- writes the order. A retry of the same request collides on this primary key
-- and can return the original order instead of creating a second one.
CREATE TABLE IF NOT EXISTS idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    order_id        UUID NOT NULL REFERENCES orders (order_id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
