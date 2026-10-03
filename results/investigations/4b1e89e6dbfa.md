# Postmortem: nightshift-dlq-depth

Investigation `4b1e89e6dbfa`, mistral `ministral-14b-latest`, started 2026-09-30T18:43:15+00:00.

## Answer

| | |
|---|---|
| Root cause component | `placed-orders` |
| Fault category | `poison_message` |
| Confidence | 85 |
| Ended because | finished |

## Summary

The nightshift-placed-orders-dlq queue has 16 messages, far exceeding the expected threshold of 1. This suggests a poison message or messages are failing repeatedly and accumulating in the dead-letter queue, preventing successful processing.

## Impact

The page: `nightshift-dlq-depth` was ALARM (since 2026-09-30T18:43:03Z). Reason recorded by CloudWatch: Threshold Crossed: 1 datapoint [16.0 (30/09/26 18:37:00)] was greater than or equal to the threshold (1.0).

Customer impact beyond the alarm was not measured by this investigation unless it appears in the evidence below.

## Timeline

- 2026-09-30T18:43:15+00:00 step 1: `get_alarm` name=dlq-depth
- 2026-09-30T18:43:21+00:00 step 2: `finish_investigation` root_cause_component=placed-orders, fault_category=poison_message, confidence=85, summary=...

## Root cause evidence

- Step 1, `get_alarm`: {"name":"nightshift-dlq-depth","state":"ALARM","reason":"Threshold Crossed: 1 datapoint [16.0 (30/09/26 18:37:00)] was greater than or equal to the threshold (1.0).","since":"2026-09-30T18:43:03Z","metric":{"namespace":"AWS/SQS","name":"App...(older result, shortened)

## Proposed fix (for a human to decide; the agent changed nothing)

Allowlisted actions, each waiting for owner approval:

- `redrive_dlq`

- 1. Investigate the messages in the nightshift-placed-orders-dlq to identify the poison message.
- 2. Review placed-orders processing logic for error handling and retries.
- 3. Monitor the queue for further accumulation after attempting a redrive.

## Hypotheses

- likely: poison_message in the placed-orders queue causing repeated failures
- possible: retry_storm due to unhandled exceptions in placed-orders processing
- ruled_out: bad_deploy (no code deploy mentioned)
- ruled_out: config_regression (no config change mentioned)
- ruled_out: timeout_regression (no timeout-related evidence)
- ruled_out: slow_dependency (DLQ depth suggests failure, not slowness)
- ruled_out: iam_regression (no permission-related errors indicated)
- ruled_out: hot_row_contention (no database-related evidence)
- ruled_out: missing_index (no query performance evidence)
- ruled_out: throttling (no concurrency or capacity limit evidence)
- ruled_out: no_fault (DLQ depth is abnormal)

## What the investigation cost

- 1 model calls, 1,205 input and 340 output tokens (1,545 total)
- 2 steps, 6 s of wall clock
- 0 bytes of logs scanned
- 0 replies rejected by the provider for bad tool arguments
