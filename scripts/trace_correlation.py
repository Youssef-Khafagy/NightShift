#!/usr/bin/env python3
"""Prove a correlation ID survives one order's whole journey.

Places one real order with a fresh correlation ID, waits for it to be paid,
then rebuilds its story from the logs using nothing but that ID. The chain it
expects, in time order:

    cart         cart stored        (this script's PUT)
    cart         cart read          (orders fetching the cart)
    orders       checkout complete
    payments     charge approved
    fulfillment  order paid

The last two happen on the far side of the SQS queue, where HTTP headers do
not reach. The ID only gets there because orders copies it onto the message
as an attribute and fulfillment reads it back off. That hop is the one most
likely to break, and the reason this check exists: in M5 the agent answers
"what happened to this order" by filtering on this ID, and a chain that stops
at the queue would make the second half of every incident invisible.

It also checks the chain from the other side: every log line that mentions
the order ID must carry the same correlation ID. A line with the right order
and a different ID is a break, even if the chain above looks complete.

Needs the queue consumer enabled (terraform apply
-var=queue_consumer_enabled=true); it refuses to start otherwise.

Cost: one order (about 0.21 DPU), a handful of Lambda invocations, and Logs
Insights queries over four small log groups for a few minutes, whose scanned
bytes are printed. Logs Insights bills bytes scanned in the time range, so the
range is this run's window only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid

import boto3

REGION = os.environ.get("AWS_REGION", "ca-central-1")
PROJECT = "nightshift"

KEYBOARD = "11111111-1111-4111-8111-111111111111"

LOG_GROUPS = {
    service: f"/aws/lambda/{PROJECT}-{service}"
    for service in ("cart", "orders", "payments", "fulfillment")
}

# (service, message) in the order they must happen.
EXPECTED_CHAIN = [
    ("cart", "cart stored"),
    ("cart", "cart read"),
    ("orders", "checkout complete"),
    ("payments", "charge approved"),
    ("fulfillment", "order paid"),
]

_lambda = boto3.client("lambda", region_name=REGION)
_logs = boto3.client("logs", region_name=REGION)


# ---------------------------------------------------------------------------
# The check itself. Pure functions, so tests/test_trace_correlation.py can
# prove it fails on a broken chain without touching AWS.
# ---------------------------------------------------------------------------


def check_chain(rows: list[dict[str, str]]) -> list[str]:
    """Problems with a chain of log rows found by correlation ID, or [].

    Each row has `service`, `message` and `timestamp`. Rows are matched to
    EXPECTED_CHAIN in order, so a step that is present but out of order is
    reported as missing.
    """
    rows = sorted(rows, key=lambda r: r["timestamp"])
    problems = []
    position = 0
    for service, message in EXPECTED_CHAIN:
        for index in range(position, len(rows)):
            row = rows[index]
            if row["service"] == service and row["message"] == message:
                position = index + 1
                break
        else:
            problems.append(f"missing or out of order: {service} '{message}'")
    return problems


def check_order_lines(rows: list[dict[str, str]], correlation_id: str) -> list[str]:
    """Every line mentioning the order must carry this correlation ID."""
    return [
        f"{row['service']} '{row['message']}' has correlation_id "
        f"{row.get('correlation_id') or '(none)'}"
        for row in rows
        if row.get("correlation_id") != correlation_id
    ]


# ---------------------------------------------------------------------------
# Talking to AWS
# ---------------------------------------------------------------------------


def invoke(function: str, method: str, path: str, body: dict, headers: dict) -> tuple:
    event = {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "headers": headers,
        "body": json.dumps(body),
    }
    response = _lambda.invoke(
        FunctionName=f"{PROJECT}-{function}:live", Payload=json.dumps(event).encode()
    )
    result = json.loads(response["Payload"].read())
    return int(result.get("statusCode", 500)), json.loads(result.get("body") or "{}")


def consumer_enabled() -> bool:
    mappings = _lambda.list_event_source_mappings(
        FunctionName=f"{PROJECT}-fulfillment:live"
    )["EventSourceMappings"]
    return any(m["State"] == "Enabled" for m in mappings)


def insights(query: str, start: int, end: int) -> tuple[list[dict[str, str]], float]:
    """Run one Logs Insights query over the four log groups.

    Returns the rows and the bytes scanned. Insights bills the bytes in the
    time range across every group named, whether or not they match, which is
    why the range is this run's window and nothing wider.
    """
    query_id = _logs.start_query(
        logGroupNames=list(LOG_GROUPS.values()),
        startTime=start,
        endTime=end,
        queryString=query,
    )["queryId"]
    while True:
        result = _logs.get_query_results(queryId=query_id)
        if result["status"] in ("Complete", "Failed", "Cancelled", "Timeout"):
            break
        time.sleep(1)
    if result["status"] != "Complete":
        sys.exit(f"Logs Insights query ended with status {result['status']}")
    rows = [{f["field"]: f["value"] for f in row} for row in result["results"]]
    # Powertools log lines carry their own `timestamp` field, so the query
    # selects Insights' @timestamp and it is renamed here instead of aliased.
    for row in rows:
        row["timestamp"] = row.pop("@timestamp", "")
    return rows, result["statistics"]["bytesScanned"]


def wait_until_paid(order_id: str, since_ms: int, timeout: int) -> bool:
    """Watch fulfillment's log group for this order being paid.

    FilterLogEvents on one group rather than an Insights query, so waiting
    does not scan four groups over and over.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = _logs.filter_log_events(
            logGroupName=LOG_GROUPS["fulfillment"],
            startTime=since_ms,
            filterPattern=f'"order paid" "{order_id}"',
        )["events"]
        if events:
            return True
        time.sleep(5)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    if not consumer_enabled():
        sys.exit(
            "The queue consumer is disabled, so the order would never be paid.\n"
            "  terraform -chdir=terraform apply -var=queue_consumer_enabled=true"
        )

    correlation_id = f"trace-{uuid.uuid4().hex[:12]}"
    cart_id = str(uuid.uuid4())
    headers = {"x-correlation-id": correlation_id}
    started = int(time.time())
    print(f"Correlation ID {correlation_id}")

    status, _ = invoke(
        "cart",
        "PUT",
        f"/carts/{cart_id}",
        {"items": [{"product_id": KEYBOARD, "quantity": 1}]},
        headers,
    )
    if status != 200:
        sys.exit(f"storing the cart returned {status}")

    status, body = invoke(
        "orders",
        "POST",
        "/checkout",
        {"cart_id": cart_id, "customer_id": "trace-check"},
        {**headers, "idempotency-key": str(uuid.uuid4())},
    )
    if status != 201:
        sys.exit(f"checkout returned {status}: {body}")
    order_id = body["order_id"]
    print(f"Order {order_id} placed; waiting for it to be paid")

    if not wait_until_paid(order_id, started * 1000, args.timeout):
        sys.exit(f"order was not paid within {args.timeout} s")

    # Logs Insights can lag a few seconds behind FilterLogEvents. Retry the
    # chain query a few times rather than failing on the first incomplete one.
    scanned = 0.0
    for attempt in range(4):
        time.sleep(10)
        end = int(time.time()) + 60
        chain, chain_bytes = insights(
            "fields @timestamp, service, message, correlation_id"
            f' | filter correlation_id = "{correlation_id}"'
            " | sort @timestamp asc | limit 100",
            started - 60,
            end,
        )
        order_lines, order_bytes = insights(
            "fields @timestamp, service, message, correlation_id"
            f' | filter order_id = "{order_id}"'
            " | sort @timestamp asc | limit 100",
            started - 60,
            end,
        )
        scanned += chain_bytes + order_bytes
        problems = check_chain(chain) + check_order_lines(order_lines, correlation_id)
        if not problems:
            break
        print(f"  attempt {attempt + 1}: incomplete, retrying")

    print(f"\nFound by correlation ID alone ({len(chain)} lines):")
    for row in chain:
        print(f"  {row['timestamp']}  {row['service']:<12} {row['message']}")
    print(f"\nLines mentioning order {order_id}: {len(order_lines)}")
    print(f"Logs Insights bytes scanned, all queries: {scanned:,.0f}")

    if problems:
        print("\nBROKEN:")
        for problem in problems:
            print(f"  {problem}")
        sys.exit(1)
    print("\nOK: the chain is complete and no line breaks it.")


if __name__ == "__main__":
    main()
