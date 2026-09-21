#!/usr/bin/env python3
"""Seed products and inventory with synthetic data.

    scripts/seed_catalogue.py              show what would happen
    scripts/seed_catalogue.py --apply      write it
    scripts/seed_catalogue.py --apply --restock   reset quantities only

Product IDs are fixed UUIDs rather than random ones, so re-running does not
pile up duplicate catalogues and so tests and the traffic generator can refer
to a product by a known ID.

Everything here is invented. No real product, price or customer data exists
anywhere in this project, which is also why the cost rules allow synthetic
rows to be deleted and regenerated freely.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from common import dsql

DEFAULT_QUANTITY = 1000

CATALOGUE = [
    (
        "11111111-1111-4111-8111-111111111111",
        "NS-KEEB-01",
        "Mechanical keyboard",
        12900,
    ),
    ("22222222-2222-4222-8222-222222222222", "NS-MOUS-01", "Wireless mouse", 4900),
    ("33333333-3333-4333-8333-333333333333", "NS-MNTR-01", "27 inch monitor", 34900),
    ("44444444-4444-4444-8444-444444444444", "NS-CABL-01", "USB-C cable", 1200),
    ("55555555-5555-4555-8555-555555555555", "NS-DOCK-01", "Laptop dock", 21900),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--restock",
        action="store_true",
        help=f"only reset inventory to {DEFAULT_QUANTITY}, leaving the catalogue alone",
    )
    parser.add_argument("--quantity", type=int, default=DEFAULT_QUANTITY)
    args = parser.parse_args()

    endpoint = os.environ.get("DSQL_ENDPOINT")
    region = os.environ.get("AWS_REGION", "ca-central-1")

    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    for product_id, sku, name, price in CATALOGUE:
        action = "restock" if args.restock else "upsert"
        print(
            f"  {action:8} {sku:12} {name:22} {price / 100:8.2f}  qty {args.quantity}"
        )

    if not args.apply:
        print("\nDry run only. Re-run with --apply.")
        return

    with dsql.connect(endpoint, region, autocommit=True) as conn, conn.cursor() as cur:
        for product_id, sku, name, price in CATALOGUE:
            if not args.restock:
                # ON CONFLICT keeps this idempotent, so seeding twice is safe.
                cur.execute(
                    """
                    INSERT INTO products (product_id, sku, name, price_cents)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (product_id) DO UPDATE
                    SET sku = EXCLUDED.sku, name = EXCLUDED.name,
                        price_cents = EXCLUDED.price_cents
                    """,
                    (product_id, sku, name, price),
                )
            cur.execute(
                """
                INSERT INTO inventory (product_id, quantity)
                VALUES (%s, %s)
                ON CONFLICT (product_id) DO UPDATE
                SET quantity = EXCLUDED.quantity, updated_at = now()
                """,
                (product_id, args.quantity),
            )

    print(f"\nSeeded {len(CATALOGUE)} products.")


if __name__ == "__main__":
    main()
