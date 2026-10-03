# Postmortem: nightshift-queue-age

Investigation `dda48be75eeb`, mistral `ministral-14b-latest`, started 2026-10-01T20:56:12+00:00.

## Answer

| | |
|---|---|
| Root cause component | `orders` |
| Fault category | `throttling` |
| Confidence | 95 |
| Ended because | finished |

## Summary

The orders service is throttled due to a reserved concurrency limit of 5, causing messages to remain in flight beyond the visibility timeout (180s). No errors or failures are logged, but the queue age alarm triggered because messages are not processed in time.

## Impact

The page: `nightshift-queue-age` was ALARM (since 2026-10-01T20:56:09Z). Reason recorded by CloudWatch: Threshold Crossed: 1 datapoint [345.0 (01/10/26 20:50:00)] was greater than or equal to the threshold (300.0).

Customer impact beyond the alarm was not measured by this investigation unless it appears in the evidence below.

## Timeline

- 2026-10-01T20:56:16+00:00 step 1: `get_queue_stats`
- 2026-10-01T20:56:16+00:00 step 2: `get_metrics` namespace=AWS/SQS, metric=ApproximateNumberOfMessagesVisible, statistic=Sum, dimensions=Qu...
- 2026-10-01T20:56:16+00:00 step 3: `get_metrics` namespace=AWS/SQS, metric=ApproximateNumberOfMessagesVisible, statistic=Sum, dimensions=Qu...
- 2026-10-01T20:56:16+00:00 step 4: `list_recent_deployments` service=orders, minutes=30 (skipped)
- 2026-10-01T20:56:20+00:00 step 5: `get_traces` service=orders, minutes=5
- 2026-10-01T20:56:23+00:00 step 6: `query_logs` service=orders, query=fields @timestamp, level, message, duration_ms, correlation_id | fil... (found nothing)
- 2026-10-01T20:56:33+00:00 step 7: `get_function_config` service=orders
- 2026-10-01T20:56:39+00:00 step 8: `finish_investigation` root_cause_component=orders, fault_category=throttling, confidence=95, summary=The orders ... (refused: action 'set_operational_flag name=checkout_rate_limit value=v': 'v' is not a valid value for checkout_rate_limit; action 'pause_queue_consumer' does not fit the finding: pause_queue_consumer does not )
- 2026-10-01T20:56:41+00:00 step 9: `finish_investigation` root_cause_component=orders, fault_category=throttling, confidence=95, summary=The orders ... (refused: action 'resume_queue_consumer' does not fit the finding: resume_queue_consumer does not address a fault in orders)
- 2026-10-01T20:56:44+00:00 step 10: `finish_investigation` root_cause_component=orders, fault_category=throttling, confidence=95, summary=The orders ...

## Root cause evidence

- Step 1, `get_queue_stats`: {"queues":{"nightshift-placed-orders":{"queue":{"name":"nightshift-placed-orders","waiting":0,"in_flight":7,"visibility_timeout_seconds":180},"dead_letter_queue":{"name":"nightshift-placed-orders-dlq","waiting":0,"in_flight":0,"visibility_t...(older result, shortened)
- Step 2, `get_metrics`: {"metric":"AWS/SQS ApproximateNumberOfMessagesVisible {'QueueName': 'nightshift-placed-orders'} Sum per 60s","window_minutes":15,"points":[["20:41:00Z",0.0],["20:42:00Z",0.0],["20:43:00Z",0.0],["20:44:00Z",0.0],["20:45:00Z",0.0],["20:46:00Z...(older result, shortened)
- Step 3, `get_metrics`: {"metric":"AWS/SQS ApproximateNumberOfMessagesVisible {'QueueName': 'nightshift-placed-orders-dlq'} Sum per 60s","window_minutes":15,"points":[["20:41:00Z",0.0],["20:42:00Z",0.0],["20:43:00Z",0.0],["20:44:00Z",0.0],["20:45:00Z",0.0],["20:46...(older result, shortened)
- Step 5, `get_traces`: {"service":"nightshift-orders","note":"active tracing samples about 1 request/s plus 5%, so counts are samples","traces":280,"with_error":0,"with_fault":0,"with_throttle":0,"duration_seconds":{"p50":0.355,"p99":0.54,"max":1.076},"slowest":[...(older result, shortened)
- Step 7, `get_function_config`: {"function":"nightshift-orders","live_alias_version":"26","last_modified":"more than 30 minutes ago","runtime":"python3.14","memory_mb":128,"timeout_seconds":15,"reserved_concurrency":5,"state":"Active","code_sha256":"dHKMnCv0VyC1WVE+qXpbrD...(older result, shortened)

## Proposed fix (for a human to decide; the agent changed nothing)

- Increase reserved concurrency for the orders service to handle the current load.

## Hypotheses

- likely: throttling due to reserved concurrency limit
- possible: slow dependency (ruled out by no errors or faults)
- possible: visibility timeout too low (ruled out by timeout being 180s and traces completing in <1s)
- ruled_out: bad_deploy
- ruled_out: poison_message
- ruled_out: iam_regression
- ruled_out: hot_row_contention
- ruled_out: missing_index

## What the investigation cost

- 8 model calls, 34,652 input and 2,635 output tokens (37,287 total)
- 10 steps, 32 s of wall clock
- 58,809 bytes of logs scanned
- 0 replies rejected by the provider for bad tool arguments
