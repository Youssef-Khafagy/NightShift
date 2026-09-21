#!/usr/bin/env python3
"""Measure what a checkout actually costs in Aurora DSQL DPUs.

COST.md carried an estimate of roughly 20,000 DPUs per busy month against a
100,000 DPU free allowance, marked weak. The benchmark plan depends on it, and
DSQL is not covered by AWS free tier usage alerts, so nothing external will
warn us if the estimate is wrong. This replaces it with a measurement.

Method
------
Run several batches of different sizes against an otherwise idle cluster,
separated by gaps so their one-minute metric buckets cannot blend, then read
the DPU metrics for each batch window and fit a line through the points. The
slope is the marginal cost of one checkout; the intercept is whatever the
batch costs before any checkout happens. One batch size could not tell those
apart, and the difference matters: a fixed per-batch cost is amortised over a
benchmark run, a per-checkout cost is not.

Metrics are read with GetMetricStatistics, never GetMetricData, which AWS
charges for under all circumstances including the free tier.

Usage
-----
  python scripts/measure_dpu.py --batch 5 --batch 25
  python scripts/measure_dpu.py --batch 5 --batch 25 --settle 420
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from smoke_checkout import KEYBOARD, MOUSE, call

REGION = "ca-central-1"
NAMESPACE = "AWS/AuroraDSQL"

# Read and write DPU are the I/O halves; compute DPU is transaction time.
# Reading them apart is the point: a number that is all compute means we are
# paying for how long transactions stay open, not for the work they do.
METRICS = (
    "TotalDPU",
    "ReadDPU",
    "WriteDPU",
    "ComputeDPU",
    "TotalTransactions",
    "OccConflicts",
)

# Each checkout takes 2 keyboards and 1 mouse.
UNITS_PER_CHECKOUT = {"keyboard": 2, "mouse": 1}

_cw = boto3.client("cloudwatch", region_name=REGION)


def now() -> datetime:
    return datetime.now(UTC)


def read_metric(name: str, start: datetime, end: datetime, cluster_id: str) -> float:
    """Sum one metric over a window, padded to whole minutes.

    CloudWatch buckets by the minute, so a window of 16:49:03 to 16:49:41 has
    to be asked for as 16:49:00 to 16:50:00 or the datapoint that contains it
    is missed entirely.

    The end is padded by two minutes, not one. DSQL reports a transaction's
    compute in the bucket after the one it finished in: measured 2026-09-21, a
    transaction that ended at 16:52:53 was reported in the bucket starting
    16:53:00. One minute of padding would have cleared that by seven seconds,
    which is not a margin worth trusting.
    """
    start = start.replace(second=0, microsecond=0)
    end = (end + timedelta(minutes=2)).replace(second=0, microsecond=0)
    response = _cw.get_metric_statistics(
        Namespace=NAMESPACE,
        MetricName=name,
        Dimensions=[{"Name": "ClusterId", "Value": cluster_id}],
        StartTime=start,
        EndTime=end,
        Period=60,
        Statistics=["Sum"],
    )
    return sum(d["Sum"] for d in response["Datapoints"])


def one_checkout(cart: str, orders: str) -> None:
    """A single successful checkout. Raises if the store does not cooperate."""
    cart_id = str(uuid.uuid4())
    key = str(uuid.uuid4())
    correlation_id = f"dpu-{cart_id[:8]}"

    status, body = call(
        cart,
        "PUT",
        f"/carts/{cart_id}",
        {
            "items": [
                {"product_id": KEYBOARD, "quantity": UNITS_PER_CHECKOUT["keyboard"]},
                {"product_id": MOUSE, "quantity": UNITS_PER_CHECKOUT["mouse"]},
            ]
        },
        {"x-correlation-id": correlation_id},
    )
    if status != 200:
        raise RuntimeError(f"cart store failed: {status} {body[:200]}")

    status, body = call(
        orders,
        "POST",
        "/checkout",
        {"cart_id": cart_id, "customer_id": "dpu-measurement"},
        {"x-correlation-id": correlation_id, "idempotency-key": key},
    )
    if status != 201:
        raise RuntimeError(f"checkout failed: {status} {body[:200]}")


def run_batch(size: int, cart: str, orders: str) -> dict:
    """Run one batch and return the window it occupied."""
    start = now()
    began = time.monotonic()
    for i in range(size):
        one_checkout(cart, orders)
        print(f"    {i + 1}/{size}", end="\r", flush=True)
    elapsed = time.monotonic() - began
    end = now()
    print(
        f"    {size} checkouts in {elapsed:.1f}s ({elapsed / size * 1000:.0f} ms each)"
    )
    return {"size": size, "start": start, "end": end, "seconds": elapsed}


def fit(points: list[tuple[int, float]]) -> tuple[float, float]:
    """Least squares slope and intercept for y = slope * x + intercept."""
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    if denominator == 0:
        return 0.0, mean_y
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator
    return slope, mean_y - slope * mean_x


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--batch",
        type=int,
        action="append",
        required=True,
        help="batch size, repeat for several",
    )
    parser.add_argument(
        "--gap",
        type=int,
        default=300,
        help="seconds between batches so buckets do not blend",
    )
    parser.add_argument(
        "--settle",
        type=int,
        default=300,
        help="seconds to wait for CloudWatch to publish",
    )
    parser.add_argument("--cluster-id", default=None)
    parser.add_argument("--cart-function", default="nightshift-cart:live")
    parser.add_argument("--orders-function", default="nightshift-orders:live")
    parser.add_argument("--out", default=None, help="write results as JSON here")
    args = parser.parse_args()

    cluster_id = args.cluster_id
    if not cluster_id:
        raise SystemExit(
            "--cluster-id is required. Get it with:\n"
            "  terraform -chdir=terraform output -raw dsql_cluster_identifier"
        )

    total_checkouts = sum(args.batch)
    print(f"Measuring {len(args.batch)} batches, {total_checkouts} checkouts total.")
    print(
        f"That consumes {total_checkouts * UNITS_PER_CHECKOUT['keyboard']} keyboards "
        f"and {total_checkouts * UNITS_PER_CHECKOUT['mouse']} mice of stock.\n"
    )

    # A quiet window before anything runs. On an idle cluster this should be
    # zero; anything else means something is still talking to the database and
    # the measurement would be attributing its cost to us.
    baseline_start = now() - timedelta(minutes=10)
    baseline = read_metric("TotalDPU", baseline_start, now(), cluster_id)
    print(f"Baseline TotalDPU over the last 10 minutes: {baseline:.3f}")
    if baseline > 1.0:
        print("  WARNING: the cluster is not idle. The result will be contaminated.")
    print()

    batches = []
    for index, size in enumerate(args.batch):
        print(f"  batch {index + 1}: {size} checkouts")
        batches.append(run_batch(size, args.cart_function, args.orders_function))
        if index < len(args.batch) - 1:
            print(f"    gap of {args.gap}s")
            time.sleep(args.gap)

    print(f"\nWaiting {args.settle}s for CloudWatch to publish.")
    time.sleep(args.settle)

    print(
        f"\n{'batch':>7} {'DPU':>10} {'read':>9} {'write':>9} {'compute':>9} "
        f"{'txns':>7} {'conflicts':>10} {'DPU each':>10}"
    )
    for batch in batches:
        for metric in METRICS:
            batch[metric] = read_metric(
                metric, batch["start"], batch["end"], cluster_id
            )
        print(
            f"{batch['size']:>7} {batch['TotalDPU']:>10.3f} "
            f"{batch['ReadDPU']:>9.3f} {batch['WriteDPU']:>9.3f} "
            f"{batch['ComputeDPU']:>9.3f} {batch['TotalTransactions']:>7.0f} "
            f"{batch['OccConflicts']:>10.0f} "
            f"{batch['TotalDPU'] / batch['size']:>10.3f}"
        )

    slope, intercept = fit([(b["size"], b["TotalDPU"]) for b in batches])
    print(
        f"\nLinear fit: {slope:.4f} DPU per checkout, "
        f"{intercept:.3f} DPU fixed per batch."
    )
    print(
        f"At that rate the 100,000 DPU monthly free allowance buys "
        f"{int(100_000 / slope):,} checkouts."
    )

    if args.out:
        payload = {
            "measured_at": now().isoformat(),
            "cluster_id": cluster_id,
            "baseline_total_dpu": baseline,
            "dpu_per_checkout": slope,
            "dpu_fixed_per_batch": intercept,
            "batches": [
                {
                    k: (v.isoformat() if isinstance(v, datetime) else v)
                    for k, v in b.items()
                }
                for b in batches
            ],
        }
        Path(args.out).write_text(json.dumps(payload, indent=2) + "\n")
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
