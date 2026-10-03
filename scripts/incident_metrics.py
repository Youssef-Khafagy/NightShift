#!/usr/bin/env python3
"""Save one staged incident's per-minute requests and errors for a function.

    python scripts/incident_metrics.py results/chaos/<run> nightshift-orders

Reads the run's start and finish from its result.json, then asks CloudWatch
for the function's Invocations and Errors per minute across that window, and
writes them to <run>/metrics.json. The website's Demo page draws its chart
from that file.

Uses GetMetricStatistics, which is free (GetMetricData is always billed).
CloudWatch keeps one-minute data for 15 days, so this has to run within 15
days of the incident; after that only five-minute averages remain.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3

REGION = "ca-central-1"
PERIOD = 60


def window(result: dict) -> tuple[datetime, datetime]:
    """Whole minutes covering the run, from its start to its finish."""
    start = datetime.fromisoformat(result["started"]).astimezone(UTC)
    end = datetime.fromisoformat(result["finished"]).astimezone(UTC)
    start = start.replace(second=0, microsecond=0)
    end = end.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return start, end


def per_minute(
    cw, function: str, metric: str, start: datetime, end: datetime
) -> dict[str, float]:
    response = cw.get_metric_statistics(
        Namespace="AWS/Lambda",
        MetricName=metric,
        Dimensions=[{"Name": "FunctionName", "Value": function}],
        StartTime=start,
        EndTime=end,
        Period=PERIOD,
        Statistics=["Sum"],
    )
    return {
        p["Timestamp"].astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"): p["Sum"]
        for p in response["Datapoints"]
    }


def collect(cw, result: dict, function: str) -> dict:
    start, end = window(result)
    invocations = per_minute(cw, function, "Invocations", start, end)
    errors = per_minute(cw, function, "Errors", start, end)
    points = []
    minute = start
    while minute < end:
        key = minute.strftime("%Y-%m-%dT%H:%M:%SZ")
        # A minute with no invocations publishes no datapoint at all.
        points.append(
            {
                "minute": key,
                "invocations": int(invocations.get(key, 0)),
                "errors": int(errors.get(key, 0)),
            }
        )
        minute += timedelta(minutes=1)
    return {
        "source": "CloudWatch GetMetricStatistics, AWS/Lambda, Sum per 60 s",
        "function": function,
        "run_id": result["run_id"],
        "points": points,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "run", type=Path, help="the run's directory under results/chaos/"
    )
    parser.add_argument(
        "function", help="the Lambda function, such as nightshift-orders"
    )
    parser.add_argument("--profile", default="nightshift-admin")
    args = parser.parse_args()

    result = json.loads((args.run / "result.json").read_text())
    cw = boto3.Session(profile_name=args.profile, region_name=REGION).client(
        "cloudwatch"
    )
    data = collect(cw, result, args.function)
    data["fetched_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = args.run / "metrics.json"
    out.write_text(json.dumps(data, indent=2) + "\n")
    total = sum(p["invocations"] for p in data["points"])
    failed = sum(p["errors"] for p in data["points"])
    print(f"{out}: {len(data['points'])} minutes, {total} invocations, {failed} errors")


if __name__ == "__main__":
    main()
