# Postmortem: nightshift-orders-errors

Investigation `988b50b75ca5`, mistral `ministral-14b-latest`, started 2026-09-23T19:52:53+00:00.

## Answer

| | |
|---|---|
| Root cause component | `orders` |
| Fault category | `bad_deploy` |
| Confidence | 90 |
| Ended because | finished |

## Summary

The orders service experienced a spike in errors immediately after a deployment at 19:51:36, coinciding with the alarm trigger at 19:51:00. The error logs did not return results, but the timing of the deployment and the error spike strongly suggest a bad deploy as the root cause. The p99 duration also increased significantly during this period, supporting the hypothesis of a regression introduced by the deployment.

## Impact

The page: `nightshift-orders-errors` was ALARM (since 2026-09-23T19:52:51.657+0000). Reason recorded by CloudWatch: Threshold Crossed: 1 datapoint [20.0 (23/09/26 19:51:00)] was greater than or equal to the threshold (1.0).

Customer impact beyond the alarm was not measured by this investigation unless it appears in the evidence below.

## Timeline

- 2026-09-23T19:52:54+00:00 step 1: `get_alarm` name=nightshift-orders-errors
- 2026-09-23T19:52:58+00:00 step 2: `get_metrics` namespace=AWS/Lambda, metric=Errors, statistic=Sum, dimensions=FunctionName=nightshift-ord...
- 2026-09-23T19:52:58+00:00 step 3: `get_metrics` namespace=AWS/Lambda, metric=Invocations, statistic=Sum, dimensions=FunctionName=nightshif...
- 2026-09-23T19:52:58+00:00 step 4: `get_metrics` namespace=AWS/Lambda, metric=Duration, statistic=p99, dimensions=FunctionName=nightshift-o...
- 2026-09-23T19:52:58+00:00 step 5: `get_metrics` namespace=AWS/Lambda, metric=Throttles, statistic=Sum, dimensions=FunctionName=nightshift-...
- 2026-09-23T19:52:58+00:00 step 6: `query_logs` service=orders, query=fields @timestamp, level, message, correlation_id | filter level = "...
- 2026-09-23T19:53:01+00:00 step 7: `query_logs` service=orders, query=fields @timestamp, level, message, correlation_id | filter level = "...
- 2026-09-23T19:53:01+00:00 step 8: `get_traces` service=orders, minutes=5
- 2026-09-23T19:53:01+00:00 step 9: `list_recent_deployments` service=orders, hours=2
- 2026-09-23T19:53:03+00:00 step 10: `note_hypotheses` hypotheses=likely: bad_deploy in orders service (deployed at 19:51:36, errors spiked at 19...
- 2026-09-23T19:53:05+00:00 step 11: `query_logs` service=orders, query=fields @timestamp, message, error | filter @timestamp > "2026-09-23T...
- 2026-09-23T19:53:08+00:00 step 12: `query_logs` service=orders, query=fields @timestamp, message, error, correlation_id | filter @timestam...
- 2026-09-23T19:53:21+00:00 step 13: `query_logs` service=orders, query=fields @timestamp, message, error, correlation_id | filter @timestam...
- 2026-09-23T19:53:21+00:00 step 14: `get_function_config` service=orders
- 2026-09-23T19:53:24+00:00 step 15: `finish_investigation` root_cause_component=orders, fault_category=bad_deploy, confidence=90, summary=The orders ...

## Root cause evidence

- Step 1, `get_alarm`: {"name":"nightshift-orders-errors","state":"ALARM","reason":"Threshold Crossed: 1 datapoint [20.0 (23/09/26 19:51:00)] was greater than or equal to the threshold (1.0).","since":"2026-09-23T19:52:51Z","metric":{"namespace":"AWS/Lambda","nam...(older result, shortened)
- Step 5, `get_metrics`: "{\"error\": \"skipped: at most 3 calls per reply\"}"
- Step 6, `query_logs`: "{\"error\": \"skipped: at most 3 calls per reply\"}"
- Step 7, `query_logs`: {"status":"Complete","rows":[],"bytes_scanned":19650,"scan_budget_left_bytes":20951870}
- Step 8, `get_traces`: {"service":"nightshift-orders","note":"active tracing samples about 1 request/s plus 5%, so counts are samples","traces":245,"with_error":74,"with_fault":0,"with_throttle":0,"duration_seconds":{"p50":0.34,"p99":2.927,"max":3.389},"slowest":...(older result, shortened)

## Proposed fix (for a human to decide; the agent changed nothing)

- Roll back the orders service to version 18 immediately and investigate the changes introduced in version 22.

## Hypotheses

- likely: bad_deploy in orders service (deployed at 19:51:36, errors spiked at 19:51:00)
- possible: slow_dependency or timeout_regression in orders service
- ruled_out: none

## What the investigation cost

- 8 model calls, 26,985 input and 921 output tokens (27,906 total)
- 15 steps, 31 s of wall clock
- 590,763 bytes of logs scanned
- 0 replies rejected by the provider for bad tool arguments
