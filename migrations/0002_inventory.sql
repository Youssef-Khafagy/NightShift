-- Stock levels, split from products because they have opposite access
-- patterns: a product row is read often and written almost never, while an
-- inventory row is written on every checkout.
--
-- Keeping them in one table would mean every checkout rewrites the catalogue
-- row too, which under optimistic concurrency turns unrelated purchases of
-- the same product into commit conflicts.
CREATE TABLE IF NOT EXISTS inventory (
    product_id UUID PRIMARY KEY REFERENCES products (product_id),
    quantity   INTEGER NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
