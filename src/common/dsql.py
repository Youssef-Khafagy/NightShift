"""Connecting to Aurora DSQL.

Shared by the migration runner and, from step 6, by the services.

Two things make this different from connecting to ordinary PostgreSQL.

The password is an IAM auth token, not a secret. It is generated locally by
signing a request with the caller's AWS credentials, so nothing is stored
anywhere and there is no password to rotate or leak. Tokens expire after 15
minutes by default, but an established connection survives its token expiring,
so the token only needs to be valid at connect time.

Connections are capped at 60 minutes by DSQL regardless of anything we do, so
nothing here may assume a connection lives forever.
"""

from __future__ import annotations

import os
import random
import time
from collections.abc import Callable
from typing import TypeVar

import boto3
import psycopg

T = TypeVar("T")

# DSQL gives every cluster one database with this name. There is no choice.
DATABASE = "postgres"

# The admin role. Services get their own database roles mapped to their IAM
# roles later; the migration runner needs admin because only admin can issue
# DDL in the public schema.
ADMIN_ROLE = "admin"

# Where to find a CA bundle, most specific first.
#
# sslrootcert="system" is the obvious answer and does not work here: the
# psycopg-binary wheel ships its own OpenSSL, whose compiled-in CA location is
# not where the distribution keeps its certificates, so verification fails
# with "certificate verify failed" that reads like a server problem. Naming
# the file removes the guesswork. The first path is Debian and Ubuntu, the
# second is Amazon Linux, which is what Lambda runs.
CA_BUNDLE_CANDIDATES = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
)


def ca_bundle() -> str:
    """Pick a CA bundle that exists, or let libpq try its own default."""
    override = os.environ.get("DSQL_SSLROOTCERT")
    if override:
        return override
    for candidate in CA_BUNDLE_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return "system"


def endpoint_from_env() -> str:
    """Read the cluster endpoint, which is configuration, not a secret."""
    endpoint = os.environ.get("DSQL_ENDPOINT")
    if not endpoint:
        raise RuntimeError(
            "DSQL_ENDPOINT is not set. Get it with:\n"
            "  terraform -chdir=terraform output -raw dsql_endpoint"
        )
    return endpoint


def auth_token(endpoint: str, region: str, *, admin: bool = False) -> str:
    """Generate an IAM auth token to use as the connection password.

    Generating a token makes no network call: boto3 signs a request with the
    caller's credentials and returns the signed URL. Permission is decided by
    DSQL when the connection is made, against dsql:DbConnectAdmin for the admin
    role or dsql:DbConnect for any other database role.
    """
    client = boto3.client("dsql", region_name=region)
    if admin:
        return client.generate_db_connect_admin_auth_token(
            Hostname=endpoint, Region=region
        )
    return client.generate_db_connect_auth_token(Hostname=endpoint, Region=region)


def is_conflict(exc: BaseException) -> bool:
    """Is this the optimistic concurrency error DSQL raises at commit?

    DSQL does not block conflicting transactions the way lock-based
    PostgreSQL does. Both proceed, and the loser fails at commit with
    SQLSTATE 40001, serialization_failure. Retrying is not an optimisation
    here, it is how the system is meant to be used.
    """
    return getattr(exc, "sqlstate", None) == "40001"


def retry_on_conflict(
    work: Callable[[], T],
    *,
    attempts: int = 5,
    base_delay: float = 0.05,
    on_retry: Callable[[int, float, BaseException], None] | None = None,
) -> T:
    """Run work(), retrying serialization failures with backoff and jitter.

    Jitter is not decoration. Without it, every transaction that lost the same
    conflict retries at the same moment and collides again, which turns one
    contended row into a synchronised stampede. Full jitter, picking uniformly
    from [0, delay), spreads them out.
    """
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return work()
        except Exception as exc:
            if not is_conflict(exc):
                raise
            last = exc
            if attempt == attempts:
                break
            delay = random.uniform(0, base_delay * (2 ** (attempt - 1)))
            if on_retry:
                on_retry(attempt, delay, exc)
            time.sleep(delay)
    assert last is not None
    raise last


def connect(
    endpoint: str | None = None,
    region: str | None = None,
    *,
    role: str = ADMIN_ROLE,
    autocommit: bool = False,
) -> psycopg.Connection:
    """Open a connection to the cluster.

    autocommit matters more here than in most PostgreSQL code. DSQL refuses to
    mix DDL and DML in one transaction and allows only one DDL statement per
    transaction, so the migration runner runs DDL with autocommit on, where
    each statement is its own transaction.
    """
    endpoint = endpoint or endpoint_from_env()
    region = region or os.environ.get("AWS_REGION") or "ca-central-1"

    return psycopg.connect(
        host=endpoint,
        port=5432,
        dbname=DATABASE,
        user=role,
        password=auth_token(endpoint, region, admin=(role == ADMIN_ROLE)),
        # DSQL requires TLS. verify-full also checks the hostname against the
        # certificate, which is what makes it resistant to a redirected DNS
        # record rather than merely encrypted.
        sslmode="verify-full",
        # Without this, libpq looks for a CA bundle at ~/.postgresql/root.crt
        # and fails when it is not there, which reads like a connectivity
        # error rather than a missing file.
        sslrootcert=ca_bundle(),
        autocommit=autocommit,
        connect_timeout=10,
    )
