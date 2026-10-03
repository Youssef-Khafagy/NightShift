# Postmortem: nightshift-orders-errors

Investigation `c8c7a7bc9afc`, mistral `ministral-14b-latest`, started 2026-10-03T00:36:10+00:00.

## Answer

| | |
|---|---|
| Root cause component | `dsql` |
| Fault category | `slow_dependency` |
| Confidence | 85 |
| Ended because | finished |

## Summary

The `orders` service is experiencing a spike in errors and timeouts, likely due to slow or failing queries in the `dsql` layer. No errors are logged in downstream services (`payments`, `fulfillment`), but the `orders` service's traces show high error rates and long durations (p99: 2.641s, max: 4.361s), suggesting a dependency issue. The `dsql` layer is the most probable cause of these timeouts.

## Impact

The page: `nightshift-orders-errors` was ALARM (since 2026-10-03T00:35:55Z). Reason recorded by CloudWatch: Threshold Crossed: 1 datapoint [53.0 (03/10/26 00:34:00)] was greater than or equal to the threshold (1.0).

Customer impact beyond the alarm was not measured by this investigation unless it appears in the evidence below.

## Timeline

- 2026-10-03T00:36:12+00:00 step 1: `get_alarm` name=nightshift-orders-errors
- 2026-10-03T00:36:12+00:00 step 2: `get_metrics` namespace=AWS/Lambda, metric=Errors, statistic=Sum, dimensions=FunctionName=nightshift-ord... (failed)
- 2026-10-03T00:36:12+00:00 step 3: `list_recent_deployments` service=orders, minutes=60 (failed)
- 2026-10-03T00:36:13+00:00 step 4: `get_metrics` namespace=AWS/Lambda, metric=Errors, statistic=Sum, dimensions=FunctionName=nightshift-ord...
- 2026-10-03T00:36:14+00:00 step 5: `list_recent_deployments` service=orders, minutes=30 (found nothing)
- 2026-10-03T00:36:15+00:00 step 6: `query_logs` service=orders, query=fields @timestamp, @message, level | filter level = "ERROR" | sort @... (found nothing)
- 2026-10-03T00:36:20+00:00 step 7: `get_traces` service=orders, minutes=30
- 2026-10-03T00:36:26+00:00 step 8: `query_logs` service=payments, query=fields @timestamp, @message, level | filter level = "ERROR" | sort... (found nothing)
- 2026-10-03T00:36:26+00:00 step 9: `get_metrics` namespace=AWS/DynamoDB, metric=ThrottledRequests, statistic=Sum, dimensions=TableName=plac... (found nothing)
- 2026-10-03T00:36:31+00:00 step 10: `query_logs` service=fulfillment, query=fields @timestamp, @message, level | filter level = "ERROR" | s... (found nothing)
- 2026-10-03T00:36:31+00:00 step 11: `get_metrics` namespace=AWS/AuroraDSQL, metric=CPUUtilization, statistic=Average, dimensions=DBInstanceI... (found nothing)
- 2026-10-03T00:36:36+00:00 step 12: `get_traces` service=orders, minutes=30
- 2026-10-03T00:36:37+00:00 step 13: `query_logs` service=orders, query=fields @timestamp, @message, correlation_id, duration_ms, status | f... (found nothing)
- 2026-10-03T00:36:41+00:00 step 14: `finish_investigation` root_cause_component=dsql, fault_category=slow_dependency, confidence=85, summary=The `ord...

## Root cause evidence

- Step 1, `get_alarm`: {"name":"nightshift-orders-errors","state":"ALARM","reason":"Threshold Crossed: 1 datapoint [53.0 (03/10/26 00:34:00)] was greater than or equal to the threshold (1.0).","since":"2026-10-03T00:35:55Z","metric":{"namespace":"AWS/Lambda","nam...(older result, shortened)
- Step 4, `get_metrics`: {"metric":"AWS/Lambda Errors {'FunctionName': 'nightshift-orders'} Sum per 60s","window_minutes":30,"points":[["00:31:00Z",0.0],["00:32:00Z",0.0],["00:33:00Z",0.0],["00:34:00Z",53.0],["00:35:00Z",44.0]],"summary":{"min":0.0,"max":53.0,"coun...(older result, shortened)
- Step 7, `get_traces`: {"service":"nightshift-orders","note":"active tracing samples about 1 request/s plus 5%, so counts are samples","traces":290,"with_error":120,"with_fault":0,"with_throttle":0,"duration_seconds":{"p50":0.307,"p99":2.872,"max":4.361},"slowest...(older result, shortened)
- Step 12, `get_traces`: {"service":"nightshift-orders","note":"active tracing samples about 1 request/s plus 5%, so counts are samples","traces":306,"with_error":136,"with_fault":0,"with_throttle":0,"duration_seconds":{"p50":0.307,"p99":2.641,"max":4.361},"slowest...(older result, shortened)

## Proposed fix (for a human to decide; the agent changed nothing)

- Check Aurora DSQL query performance for slow or failing queries, especially around the time of the error spike. Review recent DSQL schema changes or query optimizations.

## Hypotheses

- likely: slow_dependency (dsql)
- possible: timeout_regression (orders)
- ruled_out: bad_deploy, config_regression, poison_message, iam_regression, hot_row_contention, throttling, retry_storm, no_fault

## What the investigation cost

- 7 model calls, 28,840 input and 2,057 output tokens (30,897 total)
- 14 steps, 30 s of wall clock
- 284,236 bytes of logs scanned
- 0 replies rejected by the provider for bad tool arguments
