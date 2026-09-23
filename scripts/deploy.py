#!/usr/bin/env python3
"""Move each live alias to the version Terraform just published, then prove it.

Runs in apply.yml after `terraform apply`. For every function whose alias is
behind the version this apply published:

1. move the alias,
2. record the move in the deployments table,

then run the checkout smoke test. If the smoke test fails, or any step before
it fails, every alias this run moved is put back where it was, each of those
is recorded as an `auto-rollback`, and the script exits 1 so the job fails.

A failed deploy therefore leaves customers on the previous version, and the
table shows both the deploy and its reversal.

    terraform -chdir=terraform output -json function_versions > versions.json
    scripts/deploy.py --versions versions.json --sha "$GITHUB_SHA" --actor "$GITHUB_ACTOR"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import deployments

REGION = os.environ.get("AWS_REGION", "ca-central-1")


def plan_moves(
    current: dict[str, str], target: dict[str, str]
) -> list[tuple[str, str, str]]:
    """(service, from, to) for every alias that is not already on its target.

    In SERVICES order, leaves first. A service missing from either side is an
    error, not a skip: silently not deploying something is worse than failing.
    """
    services = set(deployments.SERVICES)
    missing = (services - set(current)) | (services - set(target))
    if missing:
        raise ValueError(f"no version known for: {', '.join(sorted(missing))}")
    return [
        (service, current[service], target[service])
        for service in deployments.SERVICES
        if current[service] != target[service]
    ]


def deploy(
    lambda_client: Any,
    table: Any,
    target: dict[str, str],
    *,
    sha: str | None,
    actor: str,
    smoke: Callable[[], bool],
) -> int:
    """The whole deploy. Returns the process exit code."""
    current = {
        s: deployments.current_version(lambda_client, s) for s in deployments.SERVICES
    }
    moves = plan_moves(current, target)

    if not moves:
        print("Every alias is already on its target version.")
    moved: list[tuple[str, str, str]] = []
    failure = None
    try:
        for service, old, new in moves:
            deployments.move_alias(lambda_client, service, new)
            moved.append((service, old, new))
            deployments.record(
                table,
                service=service,
                previous=old,
                new=new,
                kind="deploy",
                actor=actor,
                git_sha=sha,
            )
            # flush: the smoke test subprocess would otherwise overtake it.
            print(f"  deployed  {service:12} {old} -> {new}", flush=True)
    except Exception as exc:  # noqa: BLE001
        failure = f"deploy step failed: {exc}"

    if failure is None:
        print("Smoke test:", flush=True)
        if not smoke():
            failure = "smoke test failed"

    if failure is None:
        return 0

    print(f"\n{failure.upper()}. Rolling back {len(moved)} alias(es).", file=sys.stderr)
    # Every alias gets its attempt even if an earlier one fails: stopping at
    # the first error would leave the rest on the new version, unreported.
    stuck = []
    for service, old, new in reversed(moved):
        try:
            deployments.move_alias(lambda_client, service, old)
            deployments.record(
                table,
                service=service,
                previous=new,
                new=old,
                kind="auto-rollback",
                actor=actor,
                git_sha=sha,
                reason=failure,
            )
            print(f"  rolled back {service:12} {new} -> {old}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            stuck.append(service)
            print(f"  ROLLBACK FAILED {service}: {exc}", file=sys.stderr)
    if stuck:
        print(
            f"\nStill on the new version: {', '.join(stuck)}. Roll back by hand.",
            file=sys.stderr,
        )
    return 1


def run_smoke_test() -> bool:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "smoke_checkout.py")], check=False
    )
    return result.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--versions", required=True, help="terraform output -json function_versions"
    )
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA"))
    parser.add_argument(
        "--actor",
        default=os.environ.get("GITHUB_ACTOR") or os.environ.get("USER", "unknown"),
    )
    args = parser.parse_args()

    target = {k: str(v) for k, v in json.loads(Path(args.versions).read_text()).items()}
    lambda_client = boto3.client("lambda", region_name=REGION)
    table = boto3.resource("dynamodb", region_name=REGION).Table(deployments.TABLE)
    sys.exit(
        deploy(
            lambda_client,
            table,
            target,
            sha=args.sha,
            actor=args.actor,
            smoke=run_smoke_test,
        )
    )


if __name__ == "__main__":
    main()
