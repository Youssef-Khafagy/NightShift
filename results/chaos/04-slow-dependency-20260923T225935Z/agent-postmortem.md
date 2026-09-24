# Postmortem: nightshift-throttles

Investigation `e16af51abccb`, mistral `ministral-14b-latest`, started 2026-09-23T23:03:06+00:00.

## Answer

| | |
|---|---|
| Root cause component | `placed-orders` |
| Fault category | `retry_storm` |
| Confidence | 90 |
| Ended because | finished |

## Summary

The `nightshift-throttles` alarm is caused by a retry storm in the `placed-orders` queue. The `fulfillment` service is repeatedly failing to process messages, leading to a high number of messages in flight (52) and repeated errors. This suggests a poison message or a downstream issue causing timeouts or failures.

## Impact

The page: `nightshift-throttles` was ALARM (since 2026-09-23T23:03:04.359+0000). Reason recorded by CloudWatch: Threshold Crossed: 1 datapoint [1.0 (23/09/26 23:02:00)] was greater than or equal to the threshold (1.0).

Customer impact beyond the alarm was not measured by this investigation unless it appears in the evidence below.

## Timeline

- 2026-09-23T23:03:07+00:00 step 1: `get_alarm` name=nightshift-throttles
- 2026-09-23T23:03:10+00:00 step 2: `get_topology`
- 2026-09-23T23:03:15+00:00 step 3: `get_metrics` namespace=AWS/Lambda, metric=Throttles, statistic=Sum, dimensions=FunctionName=nightshift-...
- 2026-09-23T23:03:15+00:00 step 4: `get_metrics` namespace=AWS/Lambda, metric=Throttles, statistic=Sum, dimensions=FunctionName=nightshift-...
- 2026-09-23T23:03:15+00:00 step 5: `get_metrics` namespace=AWS/Lambda, metric=Throttles, statistic=Sum, dimensions=FunctionName=nightshift-...
- 2026-09-23T23:03:15+00:00 step 6: `get_metrics` namespace=AWS/Lambda, metric=Throttles, statistic=Sum, dimensions=FunctionName=nightshift-...
- 2026-09-23T23:03:53+00:00 step 7: `get_metrics` namespace=NightShift, metric=Throttles, statistic=Sum, dimensions=, minutes=60, period=60
- 2026-09-23T23:03:54+00:00 step 8: `list_recent_deployments` service=orders, hours=2
- 2026-09-23T23:03:54+00:00 step 9: `list_recent_deployments` service=fulfillment, hours=2
- 2026-09-23T23:03:54+00:00 step 10: `list_recent_deployments` service=cart, hours=2
- 2026-09-23T23:03:56+00:00 step 11: `get_queue_stats`
- 2026-09-23T23:04:00+00:00 step 12: `query_logs` service=fulfillment, query=fields @timestamp, message, correlation_id, error | filter leve...
- 2026-09-23T23:04:04+00:00 step 13: `get_traces` service=fulfillment, minutes=10
- 2026-09-23T23:04:07+00:00 step 14: `finish_investigation` root_cause_component=placed-orders, fault_category=retry_storm, confidence=90, summary=The...

## Root cause evidence

- Step 1, `get_alarm`: {"name":"nightshift-throttles","state":"ALARM","reason":"Threshold Crossed: 1 datapoint [1.0 (23/09/26 23:02:00)] was greater than or equal to the threshold (1.0).","since":"2026-09-23T23:03:04Z","metric":{"namespace":"AWS/Lambda","name":"T...(older result, shortened)
- Step 5, `get_metrics`: {"metric":"AWS/Lambda Throttles {'FunctionName': 'nightshift-fulfillment'} Sum per 60s","window_minutes":60,"points":[["22:54:00Z",0.0],["22:55:00Z",0.0],["22:56:00Z",0.0],["22:57:00Z",0.0],["22:58:00Z",0.0],["22:59:00Z",0.0],["23:00:00Z",0...(older result, shortened)
- Step 6, `get_metrics`: "{\"error\": \"skipped: at most 3 calls per reply\"}"
- Step 7, `get_metrics`: {"metric":"NightShift Throttles {} Sum per 60s","window_minutes":60,"points":[],"summary":"no data in this window (an idle metric publishes nothing)"}

## Proposed fix (for a human to decide; the agent changed nothing)

- Investigate the poison message in the `placed-orders` queue and remove or fix it.
- Review downstream dependencies (e.g., DynamoDB, Aurora) for throttling or timeouts.
- Adjust retry logic or visibility timeout in the queue if needed.
- Monitor the queue and logs for further failures.

## Hypotheses

None recorded.

## What the investigation cost

- 8 model calls, 31,094 input and 1,635 output tokens (32,729 total)
- 14 steps, 61 s of wall clock
- 448,283 bytes of logs scanned
- 0 replies rejected by the provider for bad tool arguments
