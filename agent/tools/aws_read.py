"""The ten read-only tools. Each takes checked arguments and a ToolContext and
returns plain data; agent/tools/__init__.py turns that into what the model
sees.

Every AWS call here is a read. The Investigator role could not do anything
else even if a tool tried, but reads have costs too, and each tool bounds
its own: time windows are capped, Logs Insights stops at the per-
investigation scan budget, and CloudWatch uses GetMetricStatistics, never
GetMetricData, which is always billed.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, timedelta
from typing import Any

from agent.tools.context import ToolContext, ToolError

SERVICES = ["cart", "orders", "payments", "fulfillment", "hello"]
SECRET_NAME = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|PRIVATE", re.IGNORECASE)
PERCENTILE = re.compile(r"^p\d{1,2}(\.\d+)?$")


def hhmm(value: Any) -> str:
    """A time of day, always in UTC and marked Z. boto3 hands back datetimes
    in the machine's local zone, so on the laptop they were EDT while every
    log line and deployment row is UTC; the model would compare the two."""
    if not hasattr(value, "astimezone"):
        return str(value)
    return value.astimezone(UTC).strftime("%H:%M:%SZ")


def stamp(value: Any) -> str:
    """A full UTC timestamp, for one-off moments (an alarm's last change, a
    flag's last write) that may be days old. Metric points keep hhmm, since
    they all sit inside one short window and the date would only cost tokens."""
    if not hasattr(value, "astimezone"):
        return str(value)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def full_alarm_name(name: str) -> str:
    return name if name.startswith("nightshift-") else f"nightshift-{name}"


# -- get_alarm -----------------------------------------------------------------


def get_alarm(ctx: ToolContext, name: str | None = None) -> dict:
    cw = ctx.client("cloudwatch")
    if name is None:
        alarms = cw.describe_alarms(AlarmNamePrefix="nightshift-")["MetricAlarms"]
        return {
            "alarms": [
                {
                    "name": a["AlarmName"],
                    "state": a["StateValue"],
                    "since": stamp(a["StateUpdatedTimestamp"]),
                }
                for a in alarms
            ]
        }
    name = full_alarm_name(name)
    found = cw.describe_alarms(AlarmNames=[name])["MetricAlarms"]
    if not found:
        raise ToolError(f"no alarm named {name}")
    a = found[0]
    history = cw.describe_alarm_history(
        AlarmName=name, HistoryItemType="StateUpdate", MaxRecords=10
    )["AlarmHistoryItems"]
    return {
        "name": name,
        "state": a["StateValue"],
        "reason": a.get("StateReason", ""),
        "since": stamp(a["StateUpdatedTimestamp"]),
        "metric": {
            "namespace": a.get("Namespace"),
            "name": a.get("MetricName"),
            "dimensions": {d["Name"]: d["Value"] for d in a.get("Dimensions", [])},
            "statistic": a.get("Statistic") or a.get("ExtendedStatistic"),
            "period_seconds": a.get("Period"),
        },
        "fires_when": f"{a.get('ComparisonOperator')} {a.get('Threshold')} "
        f"for {a.get('EvaluationPeriods')} period(s)",
        "recent_state_changes": [
            {"at": stamp(h["Timestamp"]), "summary": h["HistorySummary"]}
            for h in history
        ],
    }


# -- get_metrics ---------------------------------------------------------------


def parse_dimensions(text: str) -> dict[str, str]:
    """ "FunctionName=nightshift-orders,Resource=x" to a dict. A plain string
    rather than a JSON object, because not every provider accepts an object
    schema with free-form keys."""
    pairs = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        name, sep, value = part.partition("=")
        if not sep or not name.strip() or not value.strip():
            raise ToolError(f"dimension {part!r} is not Name=Value")
        pairs[name.strip()] = value.strip()
    return pairs


def get_metrics(
    ctx: ToolContext,
    namespace: str,
    metric: str,
    statistic: str,
    dimensions: str = "",
    minutes: int = 30,
    period: int = 60,
) -> dict:
    pairs = parse_dimensions(dimensions)
    end = ctx.utcnow()
    kwargs: dict[str, Any] = {
        "Namespace": namespace,
        "MetricName": metric,
        "Dimensions": [{"Name": k, "Value": v} for k, v in pairs.items()],
        "StartTime": end - timedelta(minutes=minutes),
        "EndTime": end,
        "Period": period,
    }
    if PERCENTILE.match(statistic):
        kwargs["ExtendedStatistics"] = [statistic]
    else:
        kwargs["Statistics"] = [statistic]
    points = ctx.client("cloudwatch").get_metric_statistics(**kwargs)["Datapoints"]
    points.sort(key=lambda p: p["Timestamp"])
    values = [
        (
            p["ExtendedStatistics"][statistic]
            if "ExtendedStatistics" in p
            else p[statistic]
        )
        for p in points
    ]
    return {
        "metric": f"{namespace} {metric} {pairs} {statistic} per {period}s",
        "window_minutes": minutes,
        "points": [[hhmm(p["Timestamp"]), round(v, 3)] for p, v in zip(points, values)],
        "summary": (
            {
                "min": round(min(values), 3),
                "max": round(max(values), 3),
                "count": len(values),
            }
            if values
            else "no data in this window (an idle metric publishes nothing)"
        ),
    }


# -- query_logs ----------------------------------------------------------------


def query_logs(
    ctx: ToolContext, service: str, query: str, minutes: int = 15, limit: int = 20
) -> dict:
    cap = ctx.config.log_scan_cap_bytes
    if ctx.log_bytes_scanned >= cap:
        raise ToolError(
            f"log scan budget used up ({ctx.log_bytes_scanned:,} of {cap:,} bytes); "
            "use metrics or the evidence already gathered"
        )
    if minutes > ctx.config.max_log_query_minutes:
        raise ToolError(f"minutes must be at most {ctx.config.max_log_query_minutes}")
    logs = ctx.client("logs")
    end = ctx.utcnow()
    query_id = logs.start_query(
        logGroupName=ctx.service(service)["log_group"],
        startTime=int((end - timedelta(minutes=minutes)).timestamp()),
        endTime=int(end.timestamp()),
        queryString=query,
        limit=limit,
    )["queryId"]
    reply: dict[str, Any] = {}
    for _ in range(30):
        reply = logs.get_query_results(queryId=query_id)
        if reply["status"] in ("Complete", "Failed", "Cancelled", "Timeout"):
            break
        time.sleep(1)
    else:
        logs.stop_query(queryId=query_id)
    scanned = int(reply.get("statistics", {}).get("bytesScanned", 0))
    ctx.log_bytes_scanned += scanned
    if reply.get("status") == "Failed":
        raise ToolError("the query failed; check the Logs Insights syntax")
    rows = [
        {f["field"]: f["value"] for f in row if f["field"] != "@ptr"}
        for row in reply.get("results", [])
    ]
    return {
        "status": reply.get("status", "Timeout"),
        "rows": rows,
        "bytes_scanned": scanned,
        "scan_budget_left_bytes": max(0, cap - ctx.log_bytes_scanned),
    }


# -- get_traces ----------------------------------------------------------------


def get_traces(ctx: ToolContext, service: str, minutes: int = 15) -> dict:
    function = ctx.service(service)["function"]
    end = ctx.utcnow()
    xray = ctx.client("xray")
    summaries: list[dict] = []
    kwargs: dict[str, Any] = {
        "StartTime": end - timedelta(minutes=minutes),
        "EndTime": end,
        "FilterExpression": f'service("{function}")',
    }
    for _ in range(5):  # at most five pages
        page = xray.get_trace_summaries(**kwargs)
        summaries.extend(page.get("TraceSummaries", []))
        if not page.get("NextToken"):
            break
        kwargs["NextToken"] = page["NextToken"]
    durations = sorted(s.get("Duration", 0.0) for s in summaries)

    def pct(q: float) -> float | None:
        return round(durations[int(q * (len(durations) - 1))], 3) if durations else None

    slowest = sorted(summaries, key=lambda s: s.get("Duration", 0.0), reverse=True)[:5]
    return {
        "service": function,
        "note": "active tracing samples about 1 request/s plus 5%, so counts are samples",
        "traces": len(summaries),
        "with_error": sum(1 for s in summaries if s.get("HasError")),
        "with_fault": sum(1 for s in summaries if s.get("HasFault")),
        "with_throttle": sum(1 for s in summaries if s.get("HasThrottle")),
        "duration_seconds": {"p50": pct(0.5), "p99": pct(0.99), "max": pct(1.0)},
        "slowest": [{"id": s["Id"], "seconds": s.get("Duration")} for s in slowest],
    }


# -- list_recent_deployments ---------------------------------------------------


def list_recent_deployments(
    ctx: ToolContext, service: str | None = None, hours: int = 24
) -> dict:
    table = "nightshift-deployments"
    since = (ctx.utcnow() - timedelta(hours=hours)).isoformat(timespec="milliseconds")
    ddb = ctx.client("dynamodb")
    rows = []
    for name in [service] if service else sorted(ctx.topology()["services"]):
        items = ddb.query(
            TableName=table,
            KeyConditionExpression="service = :s AND deployed_at >= :t",
            ExpressionAttributeValues={":s": {"S": name}, ":t": {"S": since}},
            ScanIndexForward=False,
            Limit=20,
        )["Items"]
        rows += [{k: v["S"] for k, v in item.items() if "S" in v} for item in items]
    rows.sort(key=lambda r: r["deployed_at"], reverse=True)
    return {
        "window_hours": hours,
        "moves": [
            {
                "at": r["deployed_at"],
                "service": r["service"],
                "kind": r.get("kind"),
                "version": f"{r.get('previous')} -> {r.get('new')}",
                "git_sha": r.get("git_sha"),
                "actor": r.get("actor"),
                "reason": r.get("reason"),
            }
            for r in rows[:20]
        ],
    }


# -- lookup_recent_changes -------------------------------------------------------


def lookup_recent_changes(ctx: ToolContext, minutes: int = 120) -> dict:
    """Write API calls on this project's resources, from CloudTrail's free
    90-day event history. Events arrive about 5 minutes after the call."""
    end = ctx.utcnow()
    kwargs: dict[str, Any] = {
        "LookupAttributes": [{"AttributeKey": "ReadOnly", "AttributeValue": "false"}],
        "StartTime": end - timedelta(minutes=minutes),
        "EndTime": end,
        "MaxResults": 50,
    }
    trail = ctx.client("cloudtrail")
    events = []
    for _ in range(5):  # LookupEvents allows 2 calls/s per account; stay small
        page = trail.lookup_events(**kwargs)
        for e in page.get("Events", []):
            names = [r.get("ResourceName", "") for r in e.get("Resources", [])]
            if any("nightshift" in n for n in names):
                events.append(
                    {
                        "at": stamp(e["EventTime"]),
                        "event": f"{e.get('EventSource', '').split('.')[0]}:{e['EventName']}",
                        "by": e.get("Username"),
                        "resources": sorted(set(names))[:3],
                    }
                )
        if not page.get("NextToken"):
            break
        kwargs["NextToken"] = page["NextToken"]
    return {
        "window_minutes": minutes,
        "note": "CloudTrail delivers events about 5 minutes after they happen",
        "changes": events[:30],
    }


# -- get_queue_stats -------------------------------------------------------------


def get_queue_stats(ctx: ToolContext) -> dict:
    sqs = ctx.client("sqs")
    lam = ctx.client("lambda")
    out = {}
    for queue, info in ctx.topology()["queues"].items():
        entry: dict[str, Any] = {}
        for name in (queue, info["dlq"]):
            url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
            attrs = sqs.get_queue_attributes(
                QueueUrl=url,
                AttributeNames=[
                    "ApproximateNumberOfMessages",
                    "ApproximateNumberOfMessagesNotVisible",
                    "VisibilityTimeout",
                ],
            )["Attributes"]
            entry["queue" if name == queue else "dead_letter_queue"] = {
                "name": name,
                "waiting": int(attrs["ApproximateNumberOfMessages"]),
                "in_flight": int(attrs["ApproximateNumberOfMessagesNotVisible"]),
                "visibility_timeout_seconds": int(attrs["VisibilityTimeout"]),
            }
        entry["max_receive_count"] = info["max_receive_count"]
        consumer = ctx.service(info["consumer"])["function"]
        mappings = lam.list_event_source_mappings(FunctionName=consumer)[
            "EventSourceMappings"
        ]
        entry["consumer"] = [
            {
                "function": consumer,
                "state": m["State"],
                "batch_size": m.get("BatchSize"),
                "maximum_concurrency": m.get("ScalingConfig", {}).get(
                    "MaximumConcurrency"
                ),
            }
            for m in mappings
        ]
        out[queue] = entry
    return {"queues": out}


# -- get_function_config ---------------------------------------------------------


def get_function_config(ctx: ToolContext, service: str) -> dict:
    info = ctx.service(service)
    function = info["function"]
    lam = ctx.client("lambda")
    version = lam.get_alias(FunctionName=function, Name=info["alias"])[
        "FunctionVersion"
    ]
    c = lam.get_function_configuration(FunctionName=function, Qualifier=version)
    env = c.get("Environment", {}).get("Variables", {})
    concurrency = lam.get_function_concurrency(FunctionName=function)
    return {
        "function": function,
        "live_alias_version": version,
        "last_modified": c.get("LastModified"),
        "runtime": c.get("Runtime"),
        "memory_mb": c.get("MemorySize"),
        "timeout_seconds": c.get("Timeout"),
        "reserved_concurrency": concurrency.get("ReservedConcurrentExecutions"),
        "state": c.get("State"),
        "code_sha256": c.get("CodeSha256"),
        "environment": {
            k: "[redacted]" if SECRET_NAME.search(k) else v
            for k, v in sorted(env.items())
        },
    }


# -- get_topology and get_flag_values --------------------------------------------


def get_topology(ctx: ToolContext) -> dict:
    return ctx.topology()


def get_flag_values(ctx: ToolContext) -> dict:
    names = sorted(
        {
            flag
            for s in ctx.topology()["services"].values()
            for flag in s.get("flags", [])
        }
    )
    if not names:
        return {"flags": {}}
    reply = ctx.client("ssm").get_parameters(Names=names)
    return {
        "flags": {
            p["Name"]: {
                "value": p["Value"],
                "last_modified": stamp(p["LastModifiedDate"]),
            }
            for p in reply["Parameters"]
        },
        "missing": reply.get("InvalidParameters", []),
    }
