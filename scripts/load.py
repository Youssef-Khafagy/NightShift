#!/usr/bin/env python3
"""Rate-capped traffic generator for the store. Dry run by default.

Each order is what a customer does: store a cart of one to three products,
then check out. Traffic goes through the Lambda API, the same path every
internal caller and the smoke test use.

    scripts/load.py --rate 2 --duration 1200          # projection only
    scripts/load.py --rate 2 --duration 1200 --run    # send it

Before sending anything it prints what the run will cost against the free
allowances, and it refuses if any limit would be crossed:

- Per run: at most MAX_RATE orders/s, MAX_DURATION seconds, MAX_ORDERS orders.
- Per month: DSQL DPU and Lambda invocations, month to date plus this run,
  must stay under half of each free allowance. Both are read live from
  CloudWatch with GetMetricStatistics, which is free, unlike GetMetricData.
- Stock: the run must not empty any product. An out-of-stock 409 storm looks
  exactly like a fault, so the generator refuses and says to restock with
  scripts/seed_catalogue.py rather than quietly contaminating an incident.
- The queue consumer must be enabled, unless --checkout-only, otherwise the
  orders pile up in the queue unpaid.

**Open loop.** Orders are sent on a fixed schedule whether or not the store
keeps up. A generator that waits for each response before sending the next
slows down exactly when the system does, and hides the slowdown it was
supposed to reveal. In-flight requests are capped at MAX_IN_FLIGHT; a tick
that finds the cap reached is counted as dropped by the generator, so an
overloaded store shows up in the numbers instead of as a quietly lower rate.

Every order carries correlation ID `load-<run>-<n>` and customer ID
`load-<run>`, so a run's orders can be found, graded and pruned later.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from seed_catalogue import CATALOGUE

REGION = os.environ.get("AWS_REGION", "ca-central-1")
PROJECT = "nightshift"

# Per-order costs, from measurements taken in M2a and M2b (see COST.md).
LAMBDA_PER_ORDER = 4.1  # cart PUT, orders, cart GET, payments, 1/10 fulfillment batch
SQS_PER_ORDER = 1.3  # send, plus the consumer's share of receive and delete
DPU_PER_ORDER = 0.2134  # checkout 0.1366 + fulfilment 0.0768, measured 2026-09-21
LOG_BYTES_PER_ORDER = 2013  # measured 2026-09-22, EMF lines included

# Refusal limits, approved 2026-09-22.
MAX_RATE = 5.0
MAX_DURATION = 1800
MAX_ORDERS = 3600
MONTH_DPU_CAP = 50_000  # half of the 100,000 free DPU
MONTH_LAMBDA_CAP = 500_000  # half of the 1M free requests
MAX_IN_FLIGHT = 8


# ---------------------------------------------------------------------------
# Planning. Pure functions, tested in tests/test_load.py.
# ---------------------------------------------------------------------------


def plan_carts(orders: int, seed: int) -> list[list[dict[str, Any]]]:
    """Every cart the run will send, decided up front.

    Seeded, so a run can be repeated exactly, and computed before sending so
    the stock check knows precisely how many units each product needs.
    """
    rng = random.Random(seed)
    product_ids = [product[0] for product in CATALOGUE]
    carts = []
    for _ in range(orders):
        chosen = rng.sample(product_ids, rng.randint(1, 3))
        carts.append(
            [{"product_id": pid, "quantity": rng.randint(1, 2)} for pid in chosen]
        )
    return carts


def units_needed(carts: list[list[dict[str, Any]]]) -> Counter:
    needed: Counter = Counter()
    for cart in carts:
        for item in cart:
            needed[item["product_id"]] += item["quantity"]
    return needed


def projection(rate: float, duration: int) -> dict[str, float]:
    orders = int(rate * duration)
    return {
        "orders": orders,
        "lambda_invocations": orders * LAMBDA_PER_ORDER,
        "sqs_requests": orders * SQS_PER_ORDER,
        "dsql_dpu": orders * DPU_PER_ORDER,
        "log_mb": orders * LOG_BYTES_PER_ORDER / 1e6,
    }


def refusals(
    rate: float,
    duration: int,
    projected: dict[str, float],
    month_dpu: float,
    month_invocations: float,
    stock: dict[str, int],
    needed: Counter,
    consumer_on: bool,
    checkout_only: bool,
) -> list[str]:
    """Every reason not to run. An empty list means go."""
    reasons = []
    if rate <= 0 or rate > MAX_RATE:
        reasons.append(f"rate {rate}/s is outside (0, {MAX_RATE}]")
    if duration <= 0 or duration > MAX_DURATION:
        reasons.append(f"duration {duration}s is outside (0, {MAX_DURATION}]")
    if projected["orders"] > MAX_ORDERS:
        reasons.append(f"{projected['orders']} orders exceeds {MAX_ORDERS} per run")
    if month_dpu + projected["dsql_dpu"] > MONTH_DPU_CAP:
        reasons.append(
            f"DSQL: {month_dpu:,.0f} DPU used this month + {projected['dsql_dpu']:,.0f}"
            f" projected exceeds the {MONTH_DPU_CAP:,} cap"
        )
    if month_invocations + projected["lambda_invocations"] > MONTH_LAMBDA_CAP:
        reasons.append(
            f"Lambda: {month_invocations:,.0f} invocations this month + "
            f"{projected['lambda_invocations']:,.0f} projected exceeds the "
            f"{MONTH_LAMBDA_CAP:,} cap"
        )
    short = {pid: units for pid, units in needed.items() if units > stock.get(pid, 0)}
    for product_id, units in sorted(short.items()):
        reasons.append(
            f"stock: {product_id} needs {units} units, has {stock.get(product_id, 0)}"
        )
    if short:
        # --restock sets every product to one quantity, so suggest one that
        # covers the largest need, with room for a second run.
        reasons.append(
            "restock with: scripts/seed_catalogue.py --apply --restock "
            f"--quantity {max(needed.values()) + 1000}"
        )
    if not consumer_on and not checkout_only:
        reasons.append(
            "the queue consumer is disabled, so orders would pile up unpaid. "
            "Enable it, or pass --checkout-only"
        )
    return reasons


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile. None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    # Nearest rank: the smallest value with at least p% of values at or below
    # it. ceil, not round: round(99.5) is 100 in Python (half to even).
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


class Pacer:
    """Open-loop scheduling with a cap on requests in flight.

    `submit(i, done)` starts order i and must call `done()` when it finishes.
    The clock and sleep are parameters so tests can run a schedule instantly.
    """

    def __init__(
        self,
        rate: float,
        total: int,
        max_in_flight: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rate = rate
        self.total = total
        self.max_in_flight = max_in_flight
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self.in_flight = 0
        self.sent = 0
        self.dropped = 0

    def _done(self) -> None:
        with self._lock:
            self.in_flight -= 1

    def run(self, submit: Callable[[int, Callable[[], None]], None]) -> None:
        start = self._clock()
        for i in range(self.total):
            # Order i is due at start + i/rate, regardless of how long earlier
            # orders took. That is what makes it open loop.
            wait = start + i / self.rate - self._clock()
            if wait > 0:
                self._sleep(wait)
            with self._lock:
                if self.in_flight >= self.max_in_flight:
                    self.dropped += 1
                    continue
                self.in_flight += 1
                self.sent += 1
            submit(i, self._done)


# ---------------------------------------------------------------------------
# Reading the account. All free: GetMetricStatistics, a short DSQL read.
# ---------------------------------------------------------------------------

_lambda = boto3.client(
    "lambda",
    region_name=REGION,
    # No client-side retries: a throttle is a result to count, not to hide.
    config=Config(retries={"total_max_attempts": 1}, max_pool_connections=16),
)
_cloudwatch = boto3.client("cloudwatch", region_name=REGION)


def month_to_date(namespace: str, metric: str, dimensions: list[dict]) -> float:
    now = datetime.now(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    points = _cloudwatch.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric,
        Dimensions=dimensions,
        StartTime=start,
        EndTime=now,
        Period=86400,
        Statistics=["Sum"],
    )["Datapoints"]
    return sum(p["Sum"] for p in points)


def dsql_cluster_id() -> str:
    output = subprocess.run(
        [
            "terraform",
            f"-chdir={REPO_ROOT / 'terraform'}",
            "output",
            "-raw",
            "dsql_cluster_identifier",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return output.stdout.strip()


def current_stock() -> dict[str, int]:
    from common import dsql

    with dsql.connect(autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT product_id, quantity FROM inventory")
        return {str(pid): int(qty) for pid, qty in cur.fetchall()}


def consumer_enabled() -> bool:
    mappings = _lambda.list_event_source_mappings(
        FunctionName=f"{PROJECT}-fulfillment:live"
    )["EventSourceMappings"]
    return any(m["State"] == "Enabled" for m in mappings)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def invoke(function: str, method: str, path: str, body: dict, headers: dict) -> int:
    event = {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "headers": headers,
        "body": json.dumps(body),
    }
    response = _lambda.invoke(
        FunctionName=f"{PROJECT}-{function}:live", Payload=json.dumps(event).encode()
    )
    if response.get("FunctionError"):
        return 500
    return int(json.loads(response["Payload"].read()).get("statusCode", 500))


def one_order(run_id: str, i: int, cart: list[dict]) -> tuple[str, float]:
    """Store a cart and check out. Returns (outcome, checkout seconds)."""
    correlation_id = f"load-{run_id}-{i}"
    headers = {"x-correlation-id": correlation_id}
    cart_id = str(uuid.uuid4())
    try:
        status = invoke("cart", "PUT", f"/carts/{cart_id}", {"items": cart}, headers)
        if status != 200:
            return f"cart_{status}", 0.0
        started = time.monotonic()
        status = invoke(
            "orders",
            "POST",
            "/checkout",
            {"cart_id": cart_id, "customer_id": f"load-{run_id}"},
            {**headers, "idempotency-key": str(uuid.uuid4())},
        )
        return str(status), time.monotonic() - started
    except _lambda.exceptions.TooManyRequestsException:
        return "lambda_throttled", 0.0
    except Exception as exc:  # noqa: BLE001
        return f"error_{type(exc).__name__}", 0.0


def run(run_id: str, rate: float, carts: list[list[dict]]) -> dict[str, Any]:
    outcomes: Counter = Counter()
    latencies: list[float] = []
    lock = threading.Lock()
    pacer = Pacer(rate, len(carts), MAX_IN_FLIGHT)

    with ThreadPoolExecutor(max_workers=MAX_IN_FLIGHT) as pool:

        def submit(i: int, done: Callable[[], None]) -> None:
            def task() -> None:
                try:
                    outcome, seconds = one_order(run_id, i, carts[i])
                    with lock:
                        outcomes[outcome] += 1
                        if outcome in ("201", "409", "429"):
                            latencies.append(seconds)
                finally:
                    done()

            pool.submit(task)

        started = time.monotonic()
        pacer.run(submit)
    elapsed = time.monotonic() - started

    return {
        "sent": pacer.sent,
        "dropped_by_generator": pacer.dropped,
        "outcomes": dict(sorted(outcomes.items())),
        "elapsed_seconds": round(elapsed, 1),
        "achieved_rate": round(pacer.sent / elapsed, 3) if elapsed else 0,
        "checkout_latency_ms": {
            name: (round(v * 1000, 1) if v is not None else None)
            for name, v in (
                ("p50", percentile(latencies, 50)),
                ("p95", percentile(latencies, 95)),
                ("p99", percentile(latencies, 99)),
            )
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--rate", type=float, default=2.0, help="orders per second")
    parser.add_argument("--duration", type=int, default=60, help="seconds")
    parser.add_argument("--run", action="store_true", help="send traffic")
    parser.add_argument("--checkout-only", action="store_true")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--out", default=None, help="write the summary JSON here")
    args = parser.parse_args()

    run_id = uuid.uuid4().hex[:8]
    seed = args.seed if args.seed is not None else int(run_id, 16)
    projected = projection(args.rate, args.duration)
    carts = plan_carts(int(projected["orders"]), seed)
    needed = units_needed(carts)

    month_dpu = month_to_date(
        "AWS/AuroraDSQL",
        "TotalDPU",
        [{"Name": "ClusterId", "Value": dsql_cluster_id()}],
    )
    month_invocations = month_to_date("AWS/Lambda", "Invocations", [])
    stock = current_stock()
    consumer_on = consumer_enabled()

    print(f"Run {run_id}: {args.rate}/s for {args.duration}s, seed {seed}")
    print(f"  orders              {projected['orders']:>10,}")
    print(
        f"  Lambda invocations  {projected['lambda_invocations']:>10,.0f}"
        f"   month to date {month_invocations:>10,.0f}   cap {MONTH_LAMBDA_CAP:,}"
    )
    print(f"  SQS requests        {projected['sqs_requests']:>10,.0f}")
    print(
        f"  DSQL DPU            {projected['dsql_dpu']:>10,.1f}"
        f"   month to date {month_dpu:>10,.1f}   cap {MONTH_DPU_CAP:,}"
    )
    print(f"  log volume          {projected['log_mb']:>10,.1f} MB")
    print(f"  queue consumer      {'enabled' if consumer_on else 'disabled'}")

    reasons = refusals(
        args.rate,
        args.duration,
        projected,
        month_dpu,
        month_invocations,
        stock,
        needed,
        consumer_on,
        args.checkout_only,
    )
    if reasons:
        print("\nREFUSED:")
        for reason in reasons:
            print(f"  {reason}")
        sys.exit(1)
    if not args.run:
        print("\nDry run. Pass --run to send it.")
        return

    print("\nSending...")
    summary = run(run_id, args.rate, carts)
    commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    result = {
        "run_id": run_id,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": commit,
        "rate": args.rate,
        "duration": args.duration,
        "seed": seed,
        "projected": projected,
        **summary,
    }
    print(json.dumps(result, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
