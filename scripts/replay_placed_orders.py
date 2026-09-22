#!/usr/bin/env python3
"""Republish orders stuck in `placed` to the placed-orders queue.

Two things leave an order in `placed` with no message coming for it:

1. Payments degraded mode. While `payments_degraded_mode` is true, the
   fulfillment worker acknowledges messages without charging, so the orders
   they carried wait here. This is the recovery step after the flag is cleared.
2. A crash between commit and publish in orders-service. The order is written,
   the SQS message never is. orders/app.py accepts this gap deliberately and
   names this recovery for it.

Dry run by default: it lists what it would republish and sends nothing. Pass
`--apply` to send.

It refuses to run in two situations, both to avoid charging an order twice:

- If the degraded flag is still on. Replaying then only defers everything
  again.
- If the queue is not empty. A `placed` order may still have its original
  message waiting in the queue, for example with the consumer disabled.
  Replaying would give it a second message, and two messages for one order
  processed at the same time can both see `placed` and both call the payment
  provider. The worker's conditional update stops the second one marking it
  paid, but not the second charge.

Orders younger than `--min-age-minutes` are skipped for the same reason: they
may simply be in flight.

Reading the orders costs one short DSQL query. Publishing costs one SQS
request per ten orders, since messages are sent in batches of ten.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from common import dsql
from common.context import CORRELATION_ATTRIBUTE

REGION = os.environ.get("AWS_REGION", "ca-central-1")
QUEUE_NAME = "nightshift-placed-orders"
DEGRADED_PARAMETER = "/nightshift/flags/payments_degraded_mode"


def stuck_orders(min_age_minutes: int, limit: int) -> list[str]:
    with dsql.connect(autocommit=True) as conn, conn.cursor() as cur:
        # One statement on an autocommit connection: its transaction ends
        # when it returns. See the DSQL cost model in COST.md for why that
        # matters.
        cur.execute(
            """
            SELECT order_id FROM orders
            WHERE status = 'placed'
              AND created_at < now() - %s * interval '1 minute'
            ORDER BY created_at
            LIMIT %s
            """,
            (min_age_minutes, limit),
        )
        return [str(row[0]) for row in cur.fetchall()]


def queue_depth(sqs, queue_url: str) -> int:
    attributes = sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
            "ApproximateNumberOfMessagesDelayed",
        ],
    )["Attributes"]
    return sum(int(v) for v in attributes.values())


def publish(sqs, queue_url: str, order_ids: list[str]) -> int:
    failed = 0
    for start in range(0, len(order_ids), 10):
        batch = order_ids[start : start + 10]
        response = sqs.send_message_batch(
            QueueUrl=queue_url,
            Entries=[
                {
                    "Id": str(i),
                    "MessageBody": json.dumps({"order_id": order_id}),
                    # The original correlation ID is not stored with the
                    # order, so the replay gets one derived from the order ID.
                    # Searching logs for the order ID still finds both halves.
                    "MessageAttributes": {
                        CORRELATION_ATTRIBUTE: {
                            "DataType": "String",
                            "StringValue": f"replay-{order_id}",
                        }
                    },
                }
                for i, order_id in enumerate(batch)
            ],
        )
        for failure in response.get("Failed", []):
            failed += 1
            print(f"  FAILED {batch[int(failure['Id'])]}: {failure.get('Message')}")
    return failed


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="actually publish")
    parser.add_argument("--min-age-minutes", type=int, default=10)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument(
        "--ignore-queue-depth",
        action="store_true",
        help="replay even if the queue is not empty (risks double charging)",
    )
    args = parser.parse_args()

    ssm = boto3.client("ssm", region_name=REGION)
    sqs = boto3.client("sqs", region_name=REGION)

    degraded = ssm.get_parameter(Name=DEGRADED_PARAMETER)["Parameter"]["Value"]
    if degraded.strip().lower() == "true":
        sys.exit("payments_degraded_mode is still true. Clear it first.")

    queue_url = sqs.get_queue_url(QueueName=QUEUE_NAME)["QueueUrl"]
    depth = queue_depth(sqs, queue_url)
    if depth and not args.ignore_queue_depth:
        sys.exit(
            f"{QUEUE_NAME} holds about {depth} messages. Let the consumer drain it "
            "first, or some orders may be charged twice. See the docstring."
        )

    order_ids = stuck_orders(args.min_age_minutes, args.limit)
    print(
        f"{len(order_ids)} orders in 'placed' for more than "
        f"{args.min_age_minutes} minutes"
    )
    for order_id in order_ids:
        print(f"  {order_id}")

    if not order_ids:
        return
    if not args.apply:
        print("Dry run. Pass --apply to republish them.")
        return

    failed = publish(sqs, queue_url, order_ids)
    print(f"Republished {len(order_ids) - failed}, failed {failed}.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
