# Postmortem: nightshift-cart-errors

Investigation `7e5d0d0fed4f`, runbook `scripted`, started 2026-10-02T22:30:55+00:00.

## Answer

| | |
|---|---|
| Root cause component | `cart` |
| Fault category | `config_regression` |
| Confidence | 70 |
| Ended because | finished |

## Summary

cart was deployed at 2026-10-02T22:29:07.117+00:00 (settings (env CART_TABLE_NAME) changed) before cart-errors fired. Roll it back.

## Impact

The page: `nightshift-cart-errors` was ALARM (since 2026-10-02T22:30:51Z). Reason recorded by CloudWatch: Threshold Crossed: 1 datapoint [51.0 (02/10/26 22:29:00)] was greater than or equal to the threshold (1.0).

Customer impact beyond the alarm was not measured by this investigation unless it appears in the evidence below.

## Timeline

- 2026-10-02T22:30:55+00:00 step 1: `get_alarm` name=cart-errors
- 2026-10-02T22:30:56+00:00 step 2: `list_recent_deployments` minutes=30, service=cart

## Root cause evidence

- Step 1, `get_alarm`: {"name":"nightshift-cart-errors","state":"ALARM","reason":"Threshold Crossed: 1 datapoint [51.0 (02/10/26 22:29:00)] was greater than or equal to the threshold (1.0).","since":"2026-10-02T22:30:51Z","metric":{"namespace":"AWS/Lambda","name"...(older result, shortened)
- Step 2, `list_recent_deployments`: {"window_minutes":30,"moves":[{"at":"2026-10-02T22:29:07.117+00:00","service":"cart","kind":"deploy","version":"18 -> 25","git_sha":"5085754","actor":"youssef","reason":null,"changed":{"code_changed":false,"settings_changed":["env CART_TABL...(older result, shortened)

## Proposed fix (for a human to decide; the agent changed nothing)

Allowlisted actions, each waiting for owner approval:

- `rollback_alias service=cart`


## Hypotheses

- likely: cart was deployed at 2026-10-02T22:29:07.117+00:00 (settings (env CART_TABLE_NAME) changed) before cart-errors fired. Roll it back.

## What the investigation cost

- 0 model calls, 0 input and 0 output tokens (0 total)
- 2 steps, 0 s of wall clock
- 0 bytes of logs scanned
- 0 replies rejected by the provider for bad tool arguments
