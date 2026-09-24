#!/usr/bin/env python3
"""Move one service's live alias back, record it, and check the store still works.

    scripts/rollback.py --service orders --reason "checkout 5xx after deploy"
    scripts/rollback.py --service orders --to 15 --reason "..."

Without `--to`, it undoes the service's last recorded move: the alias goes to
that row's `previous` version. It refuses to guess in three cases, each of
which needs an explicit `--to`:

- **No history.** Nothing has been recorded for this service yet.
- **The alias is not where the table says.** Something moved it outside the
  recorded path, so the table's idea of "previous" cannot be trusted.
- **The last move was already a rollback.** Undoing a rollback re-deploys
  the version that was just rolled back, which is almost never what someone
  reaching for a rollback wants. This is the guard that matters most once
  the agent can call it.

Every rollback is recorded (kind `rollback`, with the reason), and then the
checkout smoke test runs. If the smoke test fails, the script says so and
exits 1, but does not move anything again: automatically reverting a
rollback would put back the version someone just decided was bad.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from ops import deployments
from ops.deployments import Refused, choose_target

REGION = os.environ.get("AWS_REGION", "ca-central-1")


def rollback(
    lambda_client: Any,
    table: Any,
    service: str,
    *,
    to: str | None,
    reason: str,
    actor: str,
    version_exists: Callable[[str, str], bool],
    smoke: Callable[[], bool],
) -> int:
    if service not in deployments.SERVICES:
        raise Refused(f"unknown service {service}")
    current = deployments.current_version(lambda_client, service)
    target = choose_target(current, deployments.history(table, service, limit=1), to)
    if not version_exists(service, target):
        raise Refused(f"{service} has no published version {target}")

    deployments.move_alias(lambda_client, service, target)
    deployments.record(
        table,
        service=service,
        previous=current,
        new=target,
        kind="rollback",
        actor=actor,
        reason=reason,
    )
    # flush: the smoke test is a subprocess writing straight to the terminal,
    # and without this its output overtakes ours when stdout is a pipe (CI).
    print(f"rolled back {service}: {current} -> {target}", flush=True)

    print("Smoke test:", flush=True)
    if smoke():
        return 0
    print(
        f"\nSMOKE TEST FAILED after rolling {service} back to {target}. "
        "Nothing was moved again; investigate before deciding the next step.",
        file=sys.stderr,
    )
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--service", required=True, choices=deployments.SERVICES)
    parser.add_argument("--to", default=None, help="explicit version to move to")
    parser.add_argument("--reason", required=True, help="why, for the record")
    parser.add_argument("--actor", default=os.environ.get("USER", "unknown"))
    args = parser.parse_args()

    lambda_client = boto3.client("lambda", region_name=REGION)
    table = boto3.resource("dynamodb", region_name=REGION).Table(deployments.TABLE)

    def version_exists(service: str, version: str) -> bool:
        try:
            lambda_client.get_function_configuration(
                FunctionName=deployments.function_name(service), Qualifier=version
            )
            return True
        except lambda_client.exceptions.ResourceNotFoundException:
            return False

    def smoke() -> bool:
        script = REPO_ROOT / "scripts" / "smoke_checkout.py"
        return (
            subprocess.run([sys.executable, str(script)], check=False).returncode == 0
        )

    try:
        code = rollback(
            lambda_client,
            table,
            args.service,
            to=args.to,
            reason=args.reason,
            actor=args.actor,
            version_exists=version_exists,
            smoke=smoke,
        )
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(2)
    sys.exit(code)


if __name__ == "__main__":
    main()
