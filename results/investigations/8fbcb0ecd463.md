# Postmortem: nightshift-queue-age

Investigation `8fbcb0ecd463`, gemini `gemini-3.5-flash-lite`, started 2026-10-01T13:35:09+00:00.

## Answer

| | |
|---|---|
| Root cause component | `placed-orders` |
| Fault category | `poison_message` |
| Confidence | 95 |
| Ended because | finished |

## Summary

A malformed message lacking 'order_id' is repeatedly failing processing in the fulfillment Lambda function. Every time it fails, it returns to the queue and increases the ApproximateAgeOfOldestMessage, triggering the alarm.

## Impact

The page: `nightshift-queue-age` was ALARM (since 2026-10-01T13:34:58Z). Reason recorded by CloudWatch: Threshold Crossed: 1 datapoint [310.0 (01/10/26 13:28:00)] was greater than or equal to the threshold (300.0).

Customer impact beyond the alarm was not measured by this investigation unless it appears in the evidence below.

## Timeline

- 2026-10-01T13:35:10+00:00 step 1: `get_alarm`
- 2026-10-01T13:35:11+00:00 step 2: `get_queue_stats`
- 2026-10-01T13:35:11+00:00 step 3: `list_recent_deployments` minutes=30 (found nothing)
- 2026-10-01T13:35:12+00:00 step 4: `note_hypotheses` hypotheses=likely: slow_dependency or timeout in fulfillment consumer processing SQS messa...
- 2026-10-01T13:35:13+00:00 step 5: `get_traces` minutes=15, service=fulfillment
- 2026-10-01T13:35:14+00:00 step 6: `query_logs` query=fields @timestamp, @message, level | sort @timestamp desc | limit 20, minutes=15, se...
- 2026-10-01T13:35:16+00:00 step 7: `get_function_config` service=fulfillment
- 2026-10-01T13:35:17+00:00 step 8: `query_logs` service=fulfillment, minutes=15, query=fields @timestamp, @message | filter level = "ERROR...
- 2026-10-01T13:35:17+00:00 step 9: `get_metrics` statistic=Maximum, metric=Duration, minutes=15, namespace=AWS/Lambda
- 2026-10-01T13:35:19+00:00 step 10: `note_hypotheses` hypotheses=likely: poison_message causing repeated failures and message retries (retry sto...
- 2026-10-01T13:35:19+00:00 step 11: `finish_investigation` proposed_actions=Inspect the malformed message in the queue or check dead-letter queue set...

## Root cause evidence

- Step 2, `get_queue_stats`: {"queues":{"nightshift-placed-orders":{"queue":{"name":"nightshift-placed-orders","waiting":0,"in_flight":8,"visibility_timeout_seconds":180},"dead_letter_queue":{"name":"nightshift-placed-orders-dlq","waiting":0,"in_flight":0,"visibility_t...(older result, shortened)
- Step 8, `query_logs`: {"status":"Complete","rows":[{"@timestamp":"2026-10-01 13:33:18.698","@message":"{\"level\":\"ERROR\",\"location\":\"handler:170\",\"message\":\"message failed\",\"timestamp\":\"2026-10-01 13:33:18,697+0000\",\"service\":\"fulfillment\",\"c...(older result, shortened)

## Proposed fix (for a human to decide; the agent changed nothing)

Allowlisted actions, each waiting for owner approval:

- `pause_queue_consumer`

- Inspect the malformed message in the queue or check dead-letter queue settings.

## Hypotheses

- likely: poison_message causing repeated failures and message retries (retry storm / visibility timeout holds) for message_id e3e1b203-111d-4766-9118-0e540742784c
- ruled_out: slow_dependency or timeout in fulfillment consumer processing SQS messages
- ruled_out: queue consumer is stuck or processing very slowly
- ruled_out: bad_deploy (no recent deployments)

## What the investigation cost

- 4 model calls, 15,215 input and 632 output tokens (15,847 total)
- 11 steps, 10 s of wall clock
- 820,575 bytes of logs scanned
- 0 replies rejected by the provider for bad tool arguments
