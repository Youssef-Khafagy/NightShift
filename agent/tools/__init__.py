"""The agent's read-only toolset: what each tool is called, what the model is
told about it, the JSON Schema of its arguments, and one entry point.

    text = run_tool("get_alarm", {"name": "orders-errors"}, ctx)

run_tool never raises for anything the model or AWS can cause. Bad
arguments, an unknown tool, an AWS error: each comes back as a result the
model can read and correct, because an investigation that crashes on a typo
has learned nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from agent.llm.base import ToolSpec
from agent.tools import aws_read
from agent.tools.args import problems
from agent.tools.context import ToolContext, ToolError
from agent.tools.output import render

SERVICE = {"type": "string", "enum": aws_read.SERVICES, "description": "Service name."}
NAMESPACES = ["AWS/Lambda", "AWS/SQS", "AWS/DynamoDB", "AWS/AuroraDSQL", "NightShift"]


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    run: Callable[..., Any]


def _tool(
    name: str, description: str, properties: dict, required: list[str], run
) -> Tool:
    schema = {"type": "object", "properties": properties, "required": required}
    return Tool(ToolSpec(name, description, schema), run)


TOOLS: dict[str, Tool] = {
    t.spec.name: t
    for t in [
        _tool(
            "get_alarm",
            "Read one CloudWatch alarm: state, reason, the metric it watches and its "
            "recent state changes. Without a name, list every project alarm and its state.",
            {
                "name": {
                    "type": "string",
                    "description": "Alarm name, e.g. orders-errors.",
                }
            },
            [],
            aws_read.get_alarm,
        ),
        _tool(
            "get_metrics",
            "Read one CloudWatch metric over a recent window with GetMetricStatistics. "
            "Useful: AWS/Lambda Errors, Invocations, Duration, Throttles by FunctionName; "
            "AWS/SQS ApproximateAgeOfOldestMessage, ApproximateNumberOfMessagesVisible by "
            "QueueName; NightShift CheckoutsPlaced, CheckoutsRejected, SerializationRetries, "
            "OrdersPaid, PaymentFailures by service.",
            {
                "namespace": {"type": "string", "enum": NAMESPACES},
                "metric": {"type": "string"},
                "statistic": {
                    "type": "string",
                    "enum": [
                        "Sum",
                        "Average",
                        "Maximum",
                        "Minimum",
                        "SampleCount",
                        "p50",
                        "p90",
                        "p99",
                    ],
                },
                "dimensions": {
                    "type": "string",
                    "description": "Name=Value pairs separated by commas, e.g. "
                    "FunctionName=nightshift-orders",
                },
                "minutes": {"type": "integer", "minimum": 5, "maximum": 180},
                "period": {"type": "integer", "minimum": 60, "maximum": 3600},
            },
            ["namespace", "metric", "statistic"],
            aws_read.get_metrics,
        ),
        _tool(
            "query_logs",
            "Run a CloudWatch Logs Insights query on one service's logs. Logs are JSON; "
            "useful fields include level, message, correlation_id, function_version, "
            "error. Every query spends a shared scan budget, so keep windows short.",
            {
                "service": SERVICE,
                "query": {
                    "type": "string",
                    "description": "Logs Insights query, e.g. 'fields @timestamp, message "
                    '| filter level = "ERROR" | sort @timestamp desc\'',
                },
                "minutes": {"type": "integer", "minimum": 1, "maximum": 60},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            ["service", "query"],
            aws_read.query_logs,
        ),
        _tool(
            "get_traces",
            "Summarise X-Ray traces for one service: sampled count, errors, faults, "
            "throttles and duration percentiles.",
            {
                "service": SERVICE,
                "minutes": {"type": "integer", "minimum": 1, "maximum": 60},
            },
            ["service"],
            aws_read.get_traces,
        ),
        _tool(
            "list_recent_deployments",
            "List recent alias moves (deploys, rollbacks) from the deployments table, "
            "newest first: which version went live, when, from which commit, by whom.",
            {
                "service": SERVICE,
                "hours": {"type": "integer", "minimum": 1, "maximum": 168},
            },
            [],
            aws_read.list_recent_deployments,
        ),
        _tool(
            "lookup_recent_changes",
            "List write API calls on project resources from CloudTrail event history "
            "(configuration changes, permission changes, deploys). About 5 minutes behind.",
            {"minutes": {"type": "integer", "minimum": 5, "maximum": 720}},
            [],
            aws_read.lookup_recent_changes,
        ),
        _tool(
            "get_queue_stats",
            "Read the placed-orders queue and its dead-letter queue: messages waiting "
            "and in flight, visibility timeout, max receive count, and the consumer's state.",
            {},
            [],
            aws_read.get_queue_stats,
        ),
        _tool(
            "get_function_config",
            "Read the live configuration of one service's function: version behind the "
            "live alias, timeout, memory, reserved concurrency, environment (secrets redacted).",
            {"service": SERVICE},
            ["service"],
            aws_read.get_function_config,
        ),
        _tool(
            "get_topology",
            "Read how the system fits together: services, what each calls, stores, "
            "queues, flags and log groups.",
            {},
            [],
            aws_read.get_topology,
        ),
        _tool(
            "get_flag_values",
            "Read the current values of the operational feature flags.",
            {},
            [],
            aws_read.get_flag_values,
        ),
    ]
}


def tool_specs() -> list[ToolSpec]:
    return [t.spec for t in TOOLS.values()]


def run_tool(name: str, args: dict[str, Any], ctx: ToolContext) -> str:
    limit = ctx.config.max_tool_output_chars
    tool = TOOLS.get(name)
    if tool is None:
        return render(
            name, {"error": f"no tool named {name!r}; have {sorted(TOOLS)}"}, limit
        )
    found = problems(tool.spec.parameters, args)
    if found:
        return render(name, {"error": "invalid arguments", "problems": found}, limit)
    try:
        data = tool.run(ctx, **args)
    except ToolError as error:
        data = {"error": str(error)}
    except ClientError as error:
        e = error.response.get("Error", {})
        data = {"error": f"AWS {e.get('Code')}: {e.get('Message', '')}"}
    except BotoCoreError as error:
        data = {"error": f"AWS client error: {error}"}
    return render(name, data, limit)
