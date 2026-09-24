# Postmortem: nightshift-throttles

Investigation `de41a95950fc`, mistral `ministral-14b-latest`, started 2026-09-23T23:55:56+00:00.

## Answer

| | |
|---|---|
| Root cause component | `payments` |
| Fault category | `throttling` |
| Confidence | 95 |
| Ended because | finished |

## Summary

The nightshift-payments Lambda function was throttled due to insufficient reserved concurrency (set to 2). A traffic spike (164 invocations in one minute) exceeded this limit, causing throttling events. No errors or misconfigurations were found in logs or deployments.

## Impact

The page: `nightshift-throttles` was ALARM (since 2026-09-23T23:55:55.394+0000). Reason recorded by CloudWatch: Threshold Crossed: 1 datapoint [4.0 (23/09/26 23:54:00)] was greater than or equal to the threshold (1.0).

Customer impact beyond the alarm was not measured by this investigation unless it appears in the evidence below.

## Timeline

- 2026-09-23T23:55:57+00:00 step 1: `get_alarm` name=nightshift-throttles
- 2026-09-23T23:55:59+00:00 step 2: `get_metrics` namespace=AWS/Lambda, metric=Throttles, statistic=Sum, dimensions=, minutes=60, period=60
- 2026-09-23T23:55:59+00:00 step 3: `get_metrics` namespace=AWS/Lambda, metric=Invocations, statistic=Sum, dimensions=, minutes=60, period=6...
- 2026-09-23T23:55:59+00:00 step 4: `get_metrics` namespace=AWS/Lambda, metric=Duration, statistic=p99, dimensions=, minutes=60, period=60
- 2026-09-23T23:55:59+00:00 step 5: `get_topology`
- 2026-09-23T23:56:01+00:00 step 6: `get_metrics` namespace=AWS/Lambda, metric=Throttles, statistic=Sum, dimensions=FunctionName=nightshift-...
- 2026-09-23T23:56:01+00:00 step 7: `get_metrics` namespace=AWS/Lambda, metric=Throttles, statistic=Sum, dimensions=FunctionName=nightshift-...
- 2026-09-23T23:56:02+00:00 step 8: `get_metrics` namespace=AWS/Lambda, metric=Throttles, statistic=Sum, dimensions=FunctionName=nightshift-...
- 2026-09-23T23:56:04+00:00 step 9: `list_recent_deployments` service=payments, hours=2
- 2026-09-23T23:56:05+00:00 step 10: `get_function_config` service=payments
- 2026-09-23T23:56:09+00:00 step 11: `query_logs` service=payments, query=fields @timestamp, level, message, function_version | filter @time...
- 2026-09-23T23:56:13+00:00 step 12: `get_metrics` namespace=AWS/Lambda, metric=Invocations, statistic=Sum, dimensions=FunctionName=nightshif...
- 2026-09-23T23:56:17+00:00 step 13: `finish_investigation` root_cause_component=payments, fault_category=throttling, confidence=95, summary=The night...

## Root cause evidence

- Step 1, `get_alarm`: {"name":"nightshift-throttles","state":"ALARM","reason":"Threshold Crossed: 1 datapoint [4.0 (23/09/26 23:54:00)] was greater than or equal to the threshold (1.0).","since":"2026-09-23T23:55:55Z","metric":{"namespace":"AWS/Lambda","name":"T...(older result, shortened)
- Step 4, `get_metrics`: {"metric":"AWS/Lambda Duration {} p99 per 60s","window_minutes":60,"points":[["22:55:00Z",1208.474],["22:56:00Z",1464.578],["22:57:00Z",1552.156],["22:58:00Z",1456.227],["22:59:00Z",1251.453],["23:00:00Z",1359.0],["23:01:00Z",1320.063],["23...(older result, shortened)
- Step 7, `get_metrics`: {"metric":"AWS/Lambda Throttles {'FunctionName': 'nightshift-payments'} Sum per 60s","window_minutes":60,"points":[["22:56:00Z",0.0],["22:57:00Z",0.0],["22:58:00Z",0.0],["22:59:00Z",0.0],["23:00:00Z",0.0],["23:01:00Z",0.0],["23:02:00Z",9.0]...(older result, shortened)

## Proposed fix (for a human to decide; the agent changed nothing)

- Increase the reserved concurrency for the nightshift-payments Lambda function to handle expected traffic loads.
- Investigate the cause of the traffic spike to prevent future throttling.

## Hypotheses

None recorded.

## What the investigation cost

- 7 model calls, 28,291 input and 1,537 output tokens (29,828 total)
- 13 steps, 21 s of wall clock
- 870,054 bytes of logs scanned
- 0 replies rejected by the provider for bad tool arguments
