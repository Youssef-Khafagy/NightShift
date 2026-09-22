#!/usr/bin/env python3
"""The monthly cost check: every free allowance this project uses, in one table.

Run on the 1st of each month (COST.md, "Calendar reminders") and before any
large run. Reads only, and only free APIs:

- The Free Tier API (`freetier:GetFreeTierUsage`), which is billing's own
  view of every tracked Always Free allowance. Authoritative, but it lags by
  about a day.
- CloudWatch `GetMetricStatistics` for a live month-to-date view of the same
  things, and for Aurora DSQL, which the Free Tier API does not track at all.
  Never GetMetricData, which is billed even inside the free tier.
- `ListMetrics` and `DescribeAlarms` for the custom metric and alarm counts.

Each row is marked OK, WATCH (50% or more of the allowance) or ALERT (85% or
more, the same line AWS's own free tier alerts use). The script exits 1 if any
row is ALERT, so it can gate a benchmark run.

It also lists any service the Free Tier API reports that this project does
not expect. Usage that nobody planned is the first sign of a cost surprise,
however small.

Not covered: Logs Insights bytes scanned (no metric exists; scripts that
query print their own `bytesScanned`), and DSQL storage.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
REGION = os.environ.get("AWS_REGION", "ca-central-1")
PROJECT = "nightshift"

WATCH = 0.50
ALERT = 0.85

# Services this project is expected to show up under in the Free Tier API.
EXPECTED_SERVICES = {
    "AWS Lambda",
    "Amazon Simple Queue Service",
    "AWS X-Ray",
    "AmazonCloudWatch",
    "Amazon DynamoDB",
    "Amazon Simple Notification Service",
    "AWS Key Management Service",
}

# Allowances that are not in the Free Tier API, or are checked live here.
DSQL_DPU = 100_000
LAMBDA_REQUESTS = 1_000_000
SQS_REQUESTS = 1_000_000
LOGS_BYTES = 5 * 1000**3
CUSTOM_METRICS = 10
ALARM_METRICS = 10


# ---------------------------------------------------------------------------
# Pure logic, tested in tests/test_cost_check.py
# ---------------------------------------------------------------------------


def status(used: float, limit: float) -> str:
    if limit <= 0:
        return "?"
    share = used / limit
    if share >= ALERT:
        return "ALERT"
    if share >= WATCH:
        return "WATCH"
    return "OK"


def row(source: str, name: str, used: float, limit: float, note: str = "") -> dict:
    return {
        "source": source,
        "name": name,
        "used": used,
        "limit": limit,
        "percent": round(100 * used / limit, 3) if limit else None,
        "status": status(used, limit),
        "note": note,
    }


def free_tier_rows(usages: list[dict[str, Any]]) -> list[dict]:
    return [
        row(
            "billing",
            f"{u['service']}: {u['usageType']}",
            float(u["actualUsageAmount"]),
            float(u["limit"]),
            f"{u['unit']}; forecast {float(u.get('forecastedUsageAmount', 0)):,.4g}",
        )
        for u in usages
    ]


def unexpected_services(usages: list[dict[str, Any]]) -> list[str]:
    return sorted({u["service"] for u in usages} - EXPECTED_SERVICES)


def sqs_requests_upper_bound(
    sent: float, received: float, deleted: float, empty: float
) -> float:
    """Every SQS metric counted as if each were its own request.

    Sends, empty receives and deletes can be batched, so this over-counts.
    That is the safe direction for a cost check.
    """
    return sent + received + deleted + empty


# ---------------------------------------------------------------------------
# Reading the account
# ---------------------------------------------------------------------------

_cloudwatch = boto3.client("cloudwatch", region_name=REGION)
_logs = boto3.client("logs", region_name=REGION)
_sqs = boto3.client("sqs", region_name=REGION)
_freetier = boto3.client("freetier", region_name="us-east-1")


def month_start() -> datetime:
    return datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def mtd(namespace: str, metric: str, dimensions: list[dict]) -> float:
    points = _cloudwatch.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric,
        Dimensions=dimensions,
        StartTime=month_start(),
        EndTime=datetime.now(UTC),
        Period=86400,
        Statistics=["Sum"],
    )["Datapoints"]
    return sum(p["Sum"] for p in points)


def free_tier_usages() -> list[dict[str, Any]]:
    usages, token = [], None
    while True:
        kwargs = {"maxResults": 100}
        if token:
            kwargs["nextToken"] = token
        page = _freetier.get_free_tier_usage(**kwargs)
        usages += page["freeTierUsages"]
        token = page.get("nextToken")
        if not token:
            return usages


def dsql_cluster_id() -> str:
    return subprocess.run(
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
    ).stdout.strip()


def project_queues() -> list[str]:
    urls = _sqs.list_queues(QueueNamePrefix=PROJECT).get("QueueUrls", [])
    return [url.rsplit("/", 1)[-1] for url in urls]


def project_log_groups() -> list[str]:
    paginator = _logs.get_paginator("describe_log_groups")
    return [
        group["logGroupName"]
        for page in paginator.paginate(logGroupNamePrefix=f"/aws/lambda/{PROJECT}-")
        for group in page["logGroups"]
    ]


def custom_metric_count() -> int:
    paginator = _cloudwatch.get_paginator("list_metrics")
    return sum(
        1
        for page in paginator.paginate()
        for metric in page["Metrics"]
        if not metric["Namespace"].startswith("AWS/")
    )


def alarm_metric_count() -> int:
    """Metrics watched by alarms: one per standard alarm, one per metric in a
    metric math alarm, which is how the free allowance counts them."""
    total = 0
    paginator = _cloudwatch.get_paginator("describe_alarms")
    for page in paginator.paginate(AlarmTypes=["MetricAlarm"]):
        for alarm in page["MetricAlarms"]:
            if alarm.get("Metrics"):
                total += sum(1 for m in alarm["Metrics"] if "MetricStat" in m)
            else:
                total += 1
    return total


def live_rows() -> list[dict]:
    rows = [
        row(
            "live",
            "Aurora DSQL: TotalDPU",
            mtd(
                "AWS/AuroraDSQL",
                "TotalDPU",
                [{"Name": "ClusterId", "Value": dsql_cluster_id()}],
            ),
            DSQL_DPU,
            "not tracked by the Free Tier API or its alerts",
        ),
        row(
            "live",
            "Lambda: invocations, all functions",
            mtd("AWS/Lambda", "Invocations", []),
            LAMBDA_REQUESTS,
        ),
    ]

    sqs_total = 0.0
    for queue in project_queues():
        dims = [{"Name": "QueueName", "Value": queue}]
        sqs_total += sqs_requests_upper_bound(
            mtd("AWS/SQS", "NumberOfMessagesSent", dims),
            mtd("AWS/SQS", "NumberOfMessagesReceived", dims),
            mtd("AWS/SQS", "NumberOfMessagesDeleted", dims),
            mtd("AWS/SQS", "NumberOfEmptyReceives", dims),
        )
    rows.append(
        row(
            "live",
            "SQS: requests, project queues",
            sqs_total,
            SQS_REQUESTS,
            "upper bound: batched calls counted per message",
        )
    )

    ingested = sum(
        mtd("AWS/Logs", "IncomingBytes", [{"Name": "LogGroupName", "Value": g}])
        for g in project_log_groups()
    )
    rows.append(
        row(
            "live",
            "CloudWatch Logs: bytes ingested, project groups",
            ingested,
            LOGS_BYTES,
            "ingestion only; the 5 GB also covers storage and Insights scans",
        )
    )

    rows.append(
        row(
            "live",
            "CloudWatch: custom metrics",
            custom_metric_count(),
            CUSTOM_METRICS,
            "ledger in COST.md",
        )
    )
    rows.append(
        row("live", "CloudWatch: alarm metrics", alarm_metric_count(), ALARM_METRICS)
    )
    return rows


def print_table(rows: list[dict]) -> None:
    width = max(len(r["name"]) for r in rows)
    for source in ("billing", "live"):
        section = [r for r in rows if r["source"] == source]
        title = (
            "Billing view (Free Tier API, lags about a day)"
            if source == "billing"
            else "Live view (CloudWatch, month to date)"
        )
        print(f"\n{title}")
        for r in section:
            pct = f"{r['percent']:.2f}%" if r["percent"] is not None else "?"
            print(
                f"  {r['status']:5}  {r['name']:<{width}}  {r['used']:>14,.2f} "
                f"/ {r['limit']:>12,.0f}  {pct:>8}  {r['note']}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", default=None, help="write the rows as JSON here")
    args = parser.parse_args()

    usages = free_tier_usages()
    rows = free_tier_rows(usages) + live_rows()

    print(f"NightShift cost check, {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")
    print_table(rows)

    unexpected = unexpected_services(usages)
    if unexpected:
        print("\nUsage from services this project does not use (find out why):")
        for service in unexpected:
            print(f"  {service}")

    alerts = [r for r in rows if r["status"] == "ALERT"]
    print(
        f"\n{len(alerts)} ALERT, "
        f"{sum(r['status'] == 'WATCH' for r in rows)} WATCH, "
        f"{sum(r['status'] == 'OK' for r in rows)} OK"
    )

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {
                    "date": datetime.now(UTC).isoformat(timespec="seconds"),
                    "rows": rows,
                    "unexpected_services": unexpected,
                },
                indent=2,
            )
            + "\n"
        )
    if alerts:
        sys.exit(1)


if __name__ == "__main__":
    main()
