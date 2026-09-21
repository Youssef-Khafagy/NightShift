#!/usr/bin/env python3
"""Apply SQL migrations to the Aurora DSQL cluster.

Usage:

    scripts/migrate.py            show what would run, change nothing
    scripts/migrate.py --apply    run the pending migrations
    scripts/migrate.py --status   show what has been applied

Why this is not just `psql -f schema.sql`.

DSQL will not mix DDL and DML in one transaction, and allows exactly one DDL
statement per transaction. That single rule removes the thing every migration
tool relies on: you cannot change the schema and record that you changed it
atomically. The write that says "0003 is applied" is necessarily a separate
transaction from the statement that applied it, so a crash in between leaves
a schema that is ahead of its own bookkeeping.

There is no way to close that window, so the design accepts it and makes
re-running harmless instead. Every migration is written to be idempotent with
IF NOT EXISTS, and each file holds exactly one statement, which the runner
enforces rather than trusts. One statement per file also means a file can
never be half applied.

Migrations are checksummed. Editing a file that has already run is the most
common way a team's databases silently diverge, so the runner refuses rather
than letting it pass.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"

sys.path.insert(0, str(REPO_ROOT / "src"))

from common import dsql

# Tracked separately from the migrations themselves. The runner has to be able
# to read its own bookkeeping before any migration has run.
BOOKKEEPING_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    checksum   TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

COMMENT_RE = re.compile(r"--[^\n]*")


def statements_in(sql: str) -> list[str]:
    """Split a migration into statements, ignoring comments and blank lines."""
    without_comments = COMMENT_RE.sub("", sql)
    return [s.strip() for s in without_comments.split(";") if s.strip()]


def checksum(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Migration:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.version = path.stem
        self.sql = path.read_text()
        self.checksum = checksum(self.sql)
        self.statements = statements_in(self.sql)

    def validate(self) -> None:
        if len(self.statements) != 1:
            raise SystemExit(
                f"{self.path.name} contains {len(self.statements)} statements.\n"
                "DSQL allows one DDL statement per transaction, so each migration "
                "file must hold exactly one. Split it."
            )


def load_migrations() -> list[Migration]:
    migrations = [Migration(p) for p in sorted(MIGRATIONS_DIR.glob("*.sql"))]
    for migration in migrations:
        migration.validate()
    return migrations


def bookkeeping_exists(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.schema_migrations')")
        return cur.fetchone()[0] is not None


def applied_versions(conn) -> dict[str, str]:
    """Read the bookkeeping, tolerating it not existing yet.

    This has to work before the table is created, because showing what would
    run must not create anything. An earlier version of this script called
    ensure_bookkeeping() unconditionally, which meant the mode advertised as
    changing nothing created a table on a fresh cluster.
    """
    if not bookkeeping_exists(conn):
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT version, checksum FROM schema_migrations")
        return dict(cur.fetchall())


def ensure_bookkeeping(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(BOOKKEEPING_DDL)


def check_for_edits(migrations: list[Migration], applied: dict[str, str]) -> None:
    """Refuse to continue if an already-applied file has changed."""
    drifted = [
        m.version
        for m in migrations
        if m.version in applied and applied[m.version] != m.checksum
    ]
    if drifted:
        raise SystemExit(
            "These migrations have already been applied but their files have "
            f"changed since: {', '.join(drifted)}\n"
            "Applied migrations are history. Add a new migration instead of "
            "editing one that has run."
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply", action="store_true", help="actually run pending migrations"
    )
    parser.add_argument(
        "--status", action="store_true", help="list applied migrations and exit"
    )
    args = parser.parse_args()

    migrations = load_migrations()

    endpoint = dsql.endpoint_from_env()
    region = os.environ.get("AWS_REGION", "ca-central-1")
    print(f"Cluster: {endpoint}")
    print(f"CA bundle: {dsql.ca_bundle()}\n")

    # autocommit is required, not a convenience: every statement below has to
    # be its own transaction.
    with dsql.connect(endpoint, region, autocommit=True) as conn:
        # Only --apply may create anything, including the runner's own table.
        if args.apply:
            ensure_bookkeeping(conn)
        applied = applied_versions(conn)
        check_for_edits(migrations, applied)

        if args.status:
            if not applied:
                print("Nothing applied yet.")
            for version in sorted(applied):
                print(f"  applied  {version}")
            return

        pending = [m for m in migrations if m.version not in applied]
        for migration in migrations:
            mark = (
                "pending"
                if migration.version in {m.version for m in pending}
                else "applied"
            )
            print(f"  {mark:8} {migration.version}")

        if not pending:
            print("\nNothing to do.")
            return

        if not args.apply:
            print(f"\n{len(pending)} migration(s) would run. Re-run with --apply.")
            return

        print()
        for migration in pending:
            print(f"applying {migration.version} ...", end=" ", flush=True)

            def run_ddl(sql: str = migration.statements[0]) -> None:
                with conn.cursor() as cur:
                    cur.execute(sql)

            dsql.retry_on_conflict(
                run_ddl,
                on_retry=lambda attempt, delay, exc: print(
                    f"\n  conflict on attempt {attempt}, retrying in {delay * 1000:.0f} ms",
                    end="",
                ),
            )

            # A separate transaction, because DSQL will not take DML and DDL
            # together. This is the window the module docstring describes.
            def record(m: Migration = migration) -> None:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                        (m.version, m.checksum),
                    )

            dsql.retry_on_conflict(record)
            print("ok")

        print(f"\nApplied {len(pending)} migration(s).")


if __name__ == "__main__":
    main()
