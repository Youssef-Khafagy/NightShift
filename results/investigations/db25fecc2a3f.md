# Postmortem: nightshift-payment-failures

Investigation `db25fecc2a3f`, mistral `ministral-14b-latest`, started 2026-10-01T22:59:40+00:00.

## Answer

| | |
|---|---|
| Root cause component | `fulfillment` |
| Fault category | `throttling` |
| Confidence | 95 |
| Ended because | finished |

## Summary

The fulfillment service is throttled due to a reserved concurrency limit of 2, causing 67 messages to be in flight and 17 waiting in the placed-orders queue. Downstream throttling in the payments service (5 throttles at 22:58) exacerbates the issue, leading to payment failures.

## Impact

The page: `nightshift-payment-failures` was ALARM (since 2026-10-01T22:59:29Z). Reason recorded by CloudWatch: Threshold Crossed: 1 datapoint [6.0 (01/10/26 22:58:00)] was greater than or equal to the threshold (3.0).

Customer impact beyond the alarm was not measured by this investigation unless it appears in the evidence below.

## Timeline

- 2026-10-01T22:59:43+00:00 step 1: `get_alarm` name=nightshift-payment-failures
- 2026-10-01T22:59:43+00:00 step 2: `get_metrics` namespace=NightShift, metric=PaymentFailures, statistic=Sum, dimensions=service=fulfillmen... (failed)
- 2026-10-01T22:59:43+00:00 step 3: `list_recent_deployments` service=fulfillment, minutes=60 (failed)
- 2026-10-01T22:59:46+00:00 step 4: `get_metrics` namespace=NightShift, metric=PaymentFailures, statistic=Sum, dimensions=service=fulfillmen...
- 2026-10-01T22:59:46+00:00 step 5: `list_recent_deployments` service=fulfillment, minutes=30 (found nothing)
- 2026-10-01T22:59:47+00:00 step 6: `query_logs` service=fulfillment, query=fields @timestamp, @message, level, reason | filter level = "ER... (failed)
- 2026-10-01T22:59:50+00:00 step 7: `query_logs` service=fulfillment, query=fields @timestamp, @message, level, reason | filter level = "ER...
- 2026-10-01T22:59:54+00:00 step 8: `get_function_config` service=fulfillment
- 2026-10-01T22:59:55+00:00 step 9: `get_traces` service=fulfillment, minutes=30
- 2026-10-01T22:59:59+00:00 step 10: `get_queue_stats`
- 2026-10-01T23:00:00+00:00 step 11: `get_metrics` namespace=AWS/Lambda, metric=Throttles, statistic=Sum, dimensions=FunctionName=nightshift-...
- 2026-10-01T23:00:08+00:00 step 12: `finish_investigation` root_cause_component=fulfillment, fault_category=throttling, confidence=95, summary=The fu...

## Root cause evidence

- Step 4, `get_metrics`: {"metric":"NightShift PaymentFailures {'service': 'fulfillment'} Sum per 60s","window_minutes":30,"points":[["22:58:00Z",6.0]],"summary":{"min":6.0,"max":6.0,"count":1}}
- Step 7, `query_logs`: {"status":"Complete","rows":[{"@timestamp":"2026-10-01 22:59:43.339","@message":"{\"level\":\"ERROR\",\"location\":\"handler:170\",\"message\":\"message failed\",\"timestamp\":\"2026-10-01 22:59:43,337+0000\",\"service\":\"fulfillment\",\"c...(older result, shortened)
- Step 10, `get_queue_stats`: {"queues":{"nightshift-placed-orders":{"queue":{"name":"nightshift-placed-orders","waiting":17,"in_flight":67,"visibility_timeout_seconds":180},"dead_letter_queue":{"name":"nightshift-placed-orders-dlq","waiting":0,"in_flight":0,"visibility...(older result, shortened)
- Step 11, `get_metrics`: {"metric":"AWS/Lambda Throttles {'FunctionName': 'nightshift-payments'} Sum per 60s","window_minutes":30,"points":[["22:56:00Z",0.0],["22:57:00Z",0.0],["22:58:00Z",5.0]],"summary":{"min":0.0,"max":5.0,"count":3}}

## Proposed fix (for a human to decide; the agent changed nothing)

Allowlisted actions, each waiting for owner approval:

- `pause_queue_consumer`

- Increase reserved concurrency for fulfillment service to handle the backlog. Investigate and address throttling in the payments service.

## Hypotheses

- likely: throttling in fulfillment due to reserved concurrency limit and downstream throttling
- possible: slow_dependency in payments or DynamoDB
- ruled_out: bad_deploy, config_regression, poison_message

## What the investigation cost

- 6 model calls, 23,616 input and 1,675 output tokens (25,291 total)
- 12 steps, 28 s of wall clock
- 296,547 bytes of logs scanned
- 0 replies rejected by the provider for bad tool arguments
