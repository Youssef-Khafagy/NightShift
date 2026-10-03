# ADR 0007: DSQL connections autocommit by default; transactions are explicit

Status: Accepted by the owner, 2026-09-21 (M2a step 7). Recorded as an ADR 2026-10-03 (M8).

## Context

Before measuring what a checkout costs in Aurora DSQL, the cluster's existing DPU history showed four minutes each billed at almost exactly 315 DPU, for read-only transactions that had read 104 bytes. A controlled experiment (the same `SELECT 1`, committed at once against held open for 60 seconds) measured **one DPU per second that a transaction stays open**, within 0.1%. DSQL bills open transaction time, not work: the free 100,000 DPU a month is about 27.8 hours of open transactions.

psycopg's default is `autocommit=False`: the first statement opens a transaction that stays open until something commits. Four code paths never committed. Lambda then froze the environment with the transaction open on the server, and DSQL billed it until its 5-minute limit ended it: 315 DPU each time, 1,590 DPU in all. One path held a transaction open across the call to the payment provider, so a slow provider would have become a database bill.

There was no functional symptom: every response was right and nothing was slow.

## Decision

- `common/dsql.py` connects with `autocommit=True`, so a lone statement is its own transaction and ends immediately.
- Code that needs several statements to be atomic says so with `with conn.transaction():`, which checkout does.
- Tests assert on the connection, not the response: after a handler returns, no transaction may be open, and none may be open while the payment provider is called. The fake connection models psycopg's real state machine, and two tests prove the fake can tell open from closed.

## Options considered

| Option | Why not |
|---|---|
| Patch the four paths with commits | The fifth path would leak again; the default was the bug. |
| A connection wrapper that commits in `finally` | Hides where transactions begin and end, and still leaves the default unsafe. |
| **Safe default, explicit transactions** | Chosen. |

## Consequences

- Measured after the fix: a checkout costs 0.1366 DPU and fulfilment 0.0768, about 0.21 DPU per order. A benchmark pass is about 25% of the monthly allowance.
- The deployed smoke test cost 1.12 DPU across 13 transactions, against about 316 DPU for the same test before the fix.
- Reverting the fix fails 9 of the 15 transaction tests.
