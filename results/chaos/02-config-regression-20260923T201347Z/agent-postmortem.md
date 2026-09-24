# Postmortem: nightshift-cart-errors

Investigation `89a3bd941f23`, mistral `ministral-14b-latest`, started 2026-09-23T20:18:32+00:00.

## Answer

| | |
|---|---|
| Root cause component | `cart` |
| Fault category | `bad_deploy` |
| Confidence | 95 |
| Ended because | finished |

## Summary

The deployment of cart version 17 at 20:16:58 coincided with a spike in Lambda errors (49 errors at 20:17:00) and degraded performance (p99 duration spikes to 300ms+). X-Ray traces confirm 100% errors in sampled requests, indicating a regression in the new version.

## Impact

The page: `nightshift-cart-errors` was ALARM (since 2026-09-23T20:18:30.959+0000). Reason recorded by CloudWatch: Threshold Crossed: 1 datapoint [49.0 (23/09/26 20:17:00)] was greater than or equal to the threshold (1.0).

Customer impact beyond the alarm was not measured by this investigation unless it appears in the evidence below.

## Timeline

- 2026-09-23T20:18:36+00:00 step 1: `get_alarm` name=nightshift-cart-errors
- 2026-09-23T20:18:36+00:00 step 2: `get_metrics` namespace=NightShift, metric=SerializationRetries, statistic=Sum, dimensions=service=cart,...
- 2026-09-23T20:18:36+00:00 step 3: `get_metrics` namespace=NightShift, metric=CheckoutsPlaced, statistic=Sum, dimensions=service=cart, minu...
- 2026-09-23T20:18:36+00:00 step 4: `get_metrics` namespace=AWS/Lambda, metric=Errors, statistic=Sum, dimensions=FunctionName=nightshift-car...
- 2026-09-23T20:18:36+00:00 step 5: `query_logs` service=cart, query=fields @timestamp, level, message, correlation_id | filter level = "ER...
- 2026-09-23T20:18:38+00:00 step 6: `get_metrics` namespace=AWS/Lambda, metric=Duration, statistic=p99, dimensions=FunctionName=nightshift-c...
- 2026-09-23T20:18:40+00:00 step 7: `query_logs` service=cart, query=fields @timestamp, level, message, correlation_id | filter level = "ER...
- 2026-09-23T20:18:40+00:00 step 8: `list_recent_deployments` service=cart, hours=2
- 2026-09-23T20:18:41+00:00 step 9: `note_hypotheses` hypotheses=likely: bad_deploy in cart (version 17)
possible: timeout_regression in cart
ru...
- 2026-09-23T20:18:43+00:00 step 10: `query_logs` service=cart, query=fields @timestamp, level, message, correlation_id, function_version | ...
- 2026-09-23T20:18:44+00:00 step 11: `get_function_config` service=cart
- 2026-09-23T20:18:48+00:00 step 12: `get_traces` service=cart, minutes=5
- 2026-09-23T20:18:52+00:00 step 13: `finish_investigation` root_cause_component=cart, fault_category=bad_deploy, confidence=95, summary=Deploying car...
- 2026-09-23T20:18:54+00:00 step 14: `finish_investigation` root_cause_component=cart, fault_category=bad_deploy, confidence=95, summary=Deploying car...
- 2026-09-23T20:18:57+00:00 step 15: `finish_investigation` root_cause_component=cart, fault_category=bad_deploy, confidence=95, summary=The deploymen...

## Root cause evidence

- Step 1, `get_alarm`: {"name":"nightshift-cart-errors","state":"ALARM","reason":"Threshold Crossed: 1 datapoint [49.0 (23/09/26 20:17:00)] was greater than or equal to the threshold (1.0).","since":"2026-09-23T20:18:30Z","metric":{"namespace":"AWS/Lambda","name"...(older result, shortened)
- Step 5, `query_logs`: "{\"error\": \"skipped: at most 3 calls per reply\"}"
- Step 6, `get_metrics`: {"metric":"AWS/Lambda Duration {'FunctionName': 'nightshift-cart'} p99 per 60s","window_minutes":10,"points":[["20:08:00Z",48.845],["20:09:00Z",42.11],["20:13:00Z",309.571],["20:14:00Z",53.678],["20:15:00Z",52.389],["20:16:00Z",299.303],["2...(older result, shortened)
- Step 8, `list_recent_deployments`: {"window_hours":2,"moves":[{"at":"2026-09-23T20:16:58.024+00:00","service":"cart","kind":"deploy","version":"15 -> 17","git_sha":"51b3252","actor":"youssef","reason":null}]}

## Proposed fix (for a human to decide; the agent changed nothing)

- 1. Roll back cart to version 15.
- 2. Investigate the changes in commit 51b3252 to identify the cause of the timeout/regression.
- 3. Monitor Lambda errors and duration post-rollback.

## Hypotheses

- likely: bad_deploy in cart (version 17)
- possible: timeout_regression in cart
- ruled_out: none

## What the investigation cost

- 8 model calls, 27,891 input and 1,538 output tokens (29,429 total)
- 15 steps, 25 s of wall clock
- 288,275 bytes of logs scanned
- 0 replies rejected by the provider for bad tool arguments
