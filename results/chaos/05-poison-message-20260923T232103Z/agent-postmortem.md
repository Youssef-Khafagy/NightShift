# Postmortem: nightshift-queue-age

Investigation `156c94e06d0b`, mistral `ministral-14b-latest`, started 2026-09-23T23:31:27+00:00.

## Answer

| | |
|---|---|
| Root cause component | `payments` |
| Fault category | `bad_deploy` |
| Confidence | 95 |
| Ended because | finished |

## Summary

The alarm was triggered due to a temporary disruption in the `payments` service caused by the deletion and recreation of its Lambda function at 23:04 UTC. This halted message processing from the `placed-orders` queue, leading to message aging and the alarm.

## Impact

The page: `nightshift-queue-age` was ALARM (since 2026-09-23T23:31:25.678+0000). Reason recorded by CloudWatch: Threshold Crossed: 1 datapoint [316.0 (23/09/26 23:25:00)] was greater than or equal to the threshold (300.0).

Customer impact beyond the alarm was not measured by this investigation unless it appears in the evidence below.

## Timeline

- 2026-09-23T23:31:29+00:00 step 1: `get_alarm` name=nightshift-queue-age
- 2026-09-23T23:31:29+00:00 step 2: `get_metrics` namespace=AWS/SQS, metric=ApproximateAgeOfOldestMessage, statistic=Maximum, dimensions=Que...
- 2026-09-23T23:31:30+00:00 step 3: `get_queue_stats`
- 2026-09-23T23:31:30+00:00 step 4: `get_topology`
- 2026-09-23T23:31:31+00:00 step 5: `get_metrics` namespace=NightShift, metric=CheckoutsPlaced, statistic=Sum, dimensions=, minutes=60, peri...
- 2026-09-23T23:31:31+00:00 step 6: `get_metrics` namespace=NightShift, metric=SerializationRetries, statistic=Sum, dimensions=, minutes=60,...
- 2026-09-23T23:31:32+00:00 step 7: `list_recent_deployments` service=orders, hours=2
- 2026-09-23T23:31:38+00:00 step 8: `get_traces` service=orders, minutes=30
- 2026-09-23T23:31:38+00:00 step 9: `lookup_recent_changes` minutes=60
- 2026-09-23T23:31:47+00:00 step 10: `query_logs` service=payments, query=fields @timestamp, level, message | filter @timestamp >= "2026-09-...
- 2026-09-23T23:31:55+00:00 step 11: `finish_investigation` root_cause_component=payments, fault_category=bad_deploy, confidence=95, summary=The alarm...

## Root cause evidence

- Step 1, `get_alarm`: {"name":"nightshift-queue-age","state":"ALARM","reason":"Threshold Crossed: 1 datapoint [316.0 (23/09/26 23:25:00)] was greater than or equal to the threshold (300.0).","since":"2026-09-23T23:31:25Z","metric":{"namespace":"AWS/SQS","name":"...(older result, shortened)
- Step 4, `get_topology`: "{\"error\": \"skipped: at most 3 calls per reply\"}"
- Step 6, `get_metrics`: {"metric":"NightShift SerializationRetries {} Sum per 60s","window_minutes":60,"points":[],"summary":"no data in this window (an idle metric publishes nothing)"}
- Step 7, `list_recent_deployments`: {"window_hours":2,"moves":[]}
- Step 8, `get_traces`: {"service":"nightshift-orders","note":"active tracing samples about 1 request/s plus 5%, so counts are samples","traces":1688,"with_error":0,"with_fault":0,"with_throttle":0,"duration_seconds":{"p50":0.332,"p99":0.504,"max":0.976},"slowest"...(older result, shortened)

## Proposed fix (for a human to decide; the agent changed nothing)

- 1. Avoid unnecessary Lambda function deletions during critical periods.
- 2. Implement blue-green deployments for critical Lambda functions.
- 3. Monitor queue age and Lambda health during deployments or changes.
- 4. Review CloudTrail logs for similar events to proactively address disruptions.

## Hypotheses

None recorded.

## What the investigation cost

- 5 model calls, 16,660 input and 1,853 output tokens (18,513 total)
- 11 steps, 28 s of wall clock
- 642,928 bytes of logs scanned
- 0 replies rejected by the provider for bad tool arguments
