#!/usr/bin/env python3
"""Create database roles and map them to Lambda execution roles.

    scripts/grant_db_roles.py           show what would run
    scripts/grant_db_roles.py --apply   run it

Why this is not a migration file.

Mapping a database role to an IAM role means naming that role's ARN, and an
ARN contains the AWS account ID. This repository keeps account IDs out of
version control, so the ARN has to be read at run time from Terraform outputs
rather than written into a .sql file.

Why a database role at all, instead of connecting as admin.

Connecting as admin would work and would mean orders-service could drop every
table in the database. The IAM action dsql:DbConnectAdmin and the database
role `admin` are a pair: granting the first is granting the second. A separate
database role with SELECT, INSERT and UPDATE on the five tables it actually
touches is the difference between a compromised checkout function being a
problem and being a catastrophe.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from common import dsql

# What each service may do, expressed once. Every grant here should be
# justifiable by pointing at a line of the service's code.
ROLE_GRANTS: dict[str, list[str]] = {
    "orders_service": [
        "GRANT USAGE ON SCHEMA public TO orders_service",
        # Reads the catalogue to price the order.
        "GRANT SELECT ON products TO orders_service",
        # Reads stock and takes it. No DELETE: checkout never removes stock
        # rows, it only decrements them.
        "GRANT SELECT, UPDATE ON inventory TO orders_service",
        # Writes the order and its lines. SELECT so a replayed idempotency
        # key can return the original order.
        "GRANT SELECT, INSERT ON orders TO orders_service",
        "GRANT INSERT ON order_items TO orders_service",
        "GRANT SELECT, INSERT ON idempotency_keys TO orders_service",
    ],
}


def terraform_output(name: str) -> str:
    result = subprocess.run(
        ["terraform", f"-chdir={REPO_ROOT / 'terraform'}", "output", "-raw", name],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def role_exists(conn, role: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
        return cur.fetchone() is not None


def mapped_arns(conn, role: str) -> set[str]:
    """Which IAM ARNs are already mapped to this database role."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT arn FROM sys.iam_pg_role_mappings WHERE pg_role_name = %s",
            (role,),
        )
        return {r[0] for r in cur.fetchall()}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply", action="store_true", help="actually run the statements"
    )
    args = parser.parse_args()

    # Which execution role each database role is mapped to.
    role_to_arn = {"orders_service": terraform_output("orders_execution_role_arn")}

    endpoint = os.environ.get("DSQL_ENDPOINT") or terraform_output("dsql_endpoint")
    region = os.environ.get("AWS_REGION", "ca-central-1")
    print(f"Cluster: {endpoint}")
    print(f"Mode:    {'APPLY' if args.apply else 'DRY RUN'}\n")

    # admin, because only admin may create roles and grant on public schema
    # objects. This script is the one place that needs it.
    with dsql.connect(endpoint, region, role=dsql.ADMIN_ROLE, autocommit=True) as conn:
        for db_role, grants in ROLE_GRANTS.items():
            arn = role_to_arn[db_role]
            statements: list[str] = []

            if not role_exists(conn, db_role):
                # No IF NOT EXISTS for roles, and no PL/pgSQL in DSQL to wrap
                # it in, so the check is a query.
                statements.append(f"CREATE ROLE {db_role} WITH LOGIN")

            if arn not in mapped_arns(conn, db_role):
                statements.append(f"AWS IAM GRANT {db_role} TO '{arn}'")

            # Grants are idempotent in PostgreSQL, so they are always re-run.
            statements.extend(grants)

            print(f"{db_role}  ->  {arn}")
            for statement in statements:
                print(f"  {statement}")
                if args.apply:
                    with conn.cursor() as cur:
                        cur.execute(statement)
            print()

    if args.apply:
        print("Applied.")
    else:
        print("Dry run only. Re-run with --apply.")


if __name__ == "__main__":
    main()
