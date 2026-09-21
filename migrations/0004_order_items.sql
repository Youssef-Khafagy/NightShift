-- Line items. The composite primary key means a product appears at most once
-- per order, enforced by the database rather than by the checkout code
-- remembering to.
--
-- unit_price_cents is copied from the product rather than joined at read
-- time, because an order records what was charged, not what the item costs
-- today.
CREATE TABLE IF NOT EXISTS order_items (
    order_id         UUID NOT NULL REFERENCES orders (order_id),
    product_id       UUID NOT NULL REFERENCES products (product_id),
    quantity         INTEGER NOT NULL,
    unit_price_cents INTEGER NOT NULL,
    PRIMARY KEY (order_id, product_id)
);
