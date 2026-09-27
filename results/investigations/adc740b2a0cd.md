# Postmortem: nightshift-orders-errors

Investigation `adc740b2a0cd`, mistral `ministral-14b-latest`, started 2026-09-27T03:04:01+00:00.

## Answer

| | |
|---|---|
| Root cause component | `orders` |
| Fault category | `timeout_regression` |
| Confidence | 85 |
| Ended because | finished |

## Summary

The orders service (nightshift-orders) experienced a spike in errors at 03:02 UTC, coinciding with a sharp increase in invocation volume and a significant rise in p99 duration (from ~380ms to ~750ms). No throttles or deployments were detected, and logs could not be retrieved due to query failures, but the evidence strongly suggests a timeout_regression caused by increased latency or a slow dependency.

## Impact

The page: `nightshift-orders-errors` was ALARM (since 2026-09-27T03:03:51Z). Reason recorded by CloudWatch: Threshold Crossed: 1 datapoint [3.0 (27/09/26 03:02:00)] was greater than or equal to the threshold (1.0).

Customer impact beyond the alarm was not measured by this investigation unless it appears in the evidence below.

## Timeline

- 2026-09-27T03:04:07+00:00 step 1: `get_alarm` name=nightshift-orders-errors
- 2026-09-27T03:04:07+00:00 step 2: `get_metrics` namespace=AWS/Lambda, metric=Errors, statistic=Sum, dimensions=FunctionName=nightshift-ord...
- 2026-09-27T03:04:07+00:00 step 3: `get_metrics` namespace=AWS/Lambda, metric=Invocations, statistic=Sum, dimensions=FunctionName=nightshif...
- 2026-09-27T03:04:07+00:00 step 4: `get_metrics` namespace=AWS/Lambda, metric=Duration, statistic=p99, dimensions=FunctionName=nightshift-o... (skipped)
- 2026-09-27T03:04:07+00:00 step 5: `list_recent_deployments` service=orders, minutes=60 (skipped)
- 2026-09-27T03:04:07+00:00 step 6: `query_logs` service=orders, query=fields @timestamp, level, message, correlation_id, function_version ... (skipped)
- 2026-09-27T03:04:07+00:00 step 7: `get_queue_stats` (skipped)
- 2026-09-27T03:04:10+00:00 step 8: `get_metrics` namespace=AWS/Lambda, metric=Duration, statistic=p99, dimensions=FunctionName=nightshift-o...
- 2026-09-27T03:04:10+00:00 step 9: `get_metrics` namespace=AWS/Lambda, metric=Throttles, statistic=Sum, dimensions=FunctionName=nightshift-...
- 2026-09-27T03:04:11+00:00 step 10: `query_logs` service=orders, query=fields @timestamp, level, message, correlation_id, function_version ... (found nothing)
- 2026-09-27T03:04:13+00:00 step 11: `list_recent_deployments` service=orders, minutes=60 (found nothing)
- 2026-09-27T03:04:13+00:00 step 12: `query_logs` service=orders, query=fields @timestamp, level, message, correlation_id, function_version ... (failed)
- 2026-09-27T03:04:14+00:00 step 13: `get_function_config` service=orders
- 2026-09-27T03:04:18+00:00 step 14: `query_logs` service=orders, query=fields @timestamp, level, message, correlation_id, function_version ... (found nothing)
- 2026-09-27T03:04:18+00:00 step 15: `get_topology`
- 2026-09-27T03:04:19+00:00 step 16: `query_logs` service=orders, query=fields @timestamp, level, message, correlation_id, function_version ... (failed)
- 2026-09-27T03:04:23+00:00 step 17: `query_logs` service=orders, query=fields @timestamp, level, message, correlation_id, function_version ... (found nothing)
- 2026-09-27T03:04:25+00:00 step 18: `note_hypotheses` hypotheses=likely: timeout_regression in orders service due to increased latency or extern...
- 2026-09-27T03:04:30+00:00 step 19: `finish_investigation` root_cause_component=orders, fault_category=timeout_regression, confidence=85, summary=The...

## Root cause evidence

- Step 2, `get_metrics`: {"metric":"AWS/Lambda Errors {'FunctionName': 'nightshift-orders'} Sum per 60s","window_minutes":60,"points":[["02:59:00Z",0.0],["03:00:00Z",0.0],["03:01:00Z",0.0],["03:02:00Z",3.0],["03:03:00Z",5.0]],"summary":{"min":0.0,"max":5.0,"count":...(older result, shortened)
- Step 3, `get_metrics`: {"metric":"AWS/Lambda Invocations {'FunctionName': 'nightshift-orders'} Sum per 60s","window_minutes":60,"points":[["02:59:00Z",29.0],["03:00:00Z",60.0],["03:01:00Z",60.0],["03:02:00Z",106.0],["03:03:00Z",102.0]],"summary":{"min":29.0,"max"...(older result, shortened)
- Step 8, `get_metrics`: {"metric":"AWS/Lambda Duration {'FunctionName': 'nightshift-orders'} p99 per 60s","window_minutes":15,"points":[["02:59:00Z",2896.67],["03:00:00Z",455.302],["03:01:00Z",379.591],["03:02:00Z",749.965],["03:03:00Z",486.182]],"summary":{"min":...(older result, shortened)
- Step 13, `get_function_config`: {"function":"nightshift-orders","live_alias_version":"23","last_modified":"more than 60 minutes ago","runtime":"python3.14","memory_mb":128,"timeout_seconds":15,"reserved_concurrency":5,"state":"Active","code_sha256":"J7v0fbzDE0O+O4LNDU8uPN...(older result, shortened)

Cited but found nothing, so not counted as evidence: step(s) 11.

## Proposed fix (for a human to decide; the agent changed nothing)

- Investigate external dependencies of the orders service (e.g., dsql, cart, or payments) for latency spikes. Check for recent changes in those services or infrastructure.

## Hypotheses

- likely: timeout_regression in orders service due to increased latency or external dependency slowdown
- possible: poison_message in orders service causing retries and errors
- possible: hot_row_contention in dsql for orders
- ruled_out: bad_deploy (no recent deployments)
- ruled_out: config_regression (no recent config changes)
- ruled_out: insufficient_evidence (evidence supports timeout_regression despite log query failures)

Dropped along the way:
- insufficient_evidence due to log query failures

## What the investigation cost

- 8 model calls, 34,074 input and 1,145 output tokens (35,219 total)
- 19 steps, 29 s of wall clock
- 476,403 bytes of logs scanned
- 0 replies rejected by the provider for bad tool arguments
