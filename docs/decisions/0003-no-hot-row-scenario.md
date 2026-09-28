# ADR 0003: No hot-row contention scenario (scenario 7)

Status: Accepted by the owner, 2026-09-28 (M7 step 6).

## Context

Scenario 7 was a flash sale: normal traffic at 1 checkout a second plus extra checkouts that all buy one product, so every checkout updates the same inventory row and Aurora DSQL's optimistic concurrency aborts and retries the losers. It is meant to fire `serialization-retries` (10 or more retries a minute for 2 minutes), the same alarm a legitimate spike fires (scenario 11), and the agent has to tell the two apart.

It was measured three times, each time with base traffic at 1 a second and the queue consumer on:

| Run | Hot load | Serialization retries per minute | Paged |
|---|---|---|---|
| Verification sitting 1, 2026-09-27 | 2 a second | 3, 1, 1 over 13 minutes (report) | no |
| 2026-09-28 01:33 UTC | 3 a second, then 4 | **22, 30, 25, 26, 37** at 3 a second; 31, 38, 41, 29, 30 at 4 | yes, 134 s after the hot load began |
| 2026-09-28 02:22 UTC, after a deploy | 3 a second | **3, 2, 4, 1** | no |

The second and third runs used the same hot load, the same product and the same throughput (about 236 checkouts a minute). Raw data: `results/scenario7-hot-rate-2026-09-28.json` and `results/scenario7-rerun-2026-09-28.json`.

Other free metrics for the two 3-a-second windows, read afterwards with GetMetricStatistics:

| Per minute | 01:33 run | 02:22 run |
|---|---|---|
| DSQL `OccConflicts` | 11, 39, 24, 25, 41 | 5, 2, 3 |
| DSQL `TotalTransactions` | 310, 711, 762, 724, 726 | 565, 545, 686 |
| DSQL `CommitLatency`, average ms | 3.8 to 4.1 | 4.0 to 4.3 |
| orders duration p50, ms | 305 to 316 | 314 to 316 |
| orders duration p99, ms | 444 to 718 | 430 to 521 |
| orders concurrent executions, max | 3 | 3 |
| fulfillment `OrdersPaid` | 144, 230, 232, 199, 227 | 178, 171, 220 |

DSQL's own conflict count moved with ours, so the retry metric is not the problem; the conflicts themselves differed. Latency and concurrency did not.

A second problem came first. At the same total rate, one hot product made about as many retries as scenario 11's spread traffic (about 30 a minute at 4 a second), because with five products, ordinary traffic already shares rows. So the only evidence separating scenario 7 from scenario 11 is that the retries involve one product. The retry log line did not name products; it does now (`product_ids`, orders version 26), and in the 02:22 run 9 of 10 retry lines named the hot product.

## The cause is unknown

The same load produced about ten times more conflicts in one run than in the other, and nothing measured so far explains it. Differences between the two runs, none shown to matter:

- The 01:33 run began shortly after a restock rewrote all five inventory rows (same half hour; the restock time was not recorded). The 02:22 run began a few minutes after a deploy replaced every orders execution environment (version 23, then 26; the only code change was a log field).
- DSQL ran about 150 more transactions a minute in the first run at the same checkout rate, and fulfillment paid slightly more orders a minute.

## What would find out

1. **Repeat the same load several times** (at least five 5-minute runs at 3 a second), alternating fresh environments after a deploy with warm ones, to get a distribution instead of two points.
2. **Log the full DSQL error on each retry.** The retry line records only SQLSTATE 40001. Check whether DSQL's error detail says what kind of conflict it was (a data conflict or a schema or catalog one); the two runs may not even have had the same kind.
3. **Isolate fulfillment.** The same checkout load with the consumer off, to see whether fulfillment's writes to `orders` change the conflict rate.
4. **Time since the last write to the hot row.** Run the load immediately after a restock and an hour after one.
5. **Checkout transaction duration inside DSQL**, not only the Lambda's total: a longer transaction is a wider window for a conflict. `CommitLatency` was the same, but it covers only the commit.

Each of these is a DSQL behaviour question, not a question about the agent.

## Decision

Drop scenario 7 from the benchmark. A scenario whose alarm fires in one run and not in the next cannot be verified, and the batch stops at the first incident that does not page. The pass is 12 scenarios: phase 1 is 18 incidents (every scenario once, scenarios 1, 4 and 11 three times), extendable to 36.

## Options considered

| Option | Why not |
|---|---|
| Keep it at 3 a second | Paged in one of two runs at that load, and the batch stops at the first incident that does not page. |
| Raise the hot rate to 4 a second | The same unexplained variance may apply; it would be one more single measurement treated as a property of the system. load.py's cap is 5 a second. |
| Lower the alarm threshold | Every other scenario and scenario 11 depend on this alarm; changing it moves them all, and the alarm is budgeted in COST.md. |
| Investigate first (the list above) | Worth doing, but it is DSQL research with no fixed end, and phase 1 does not need it. Left open. |
| **Drop it** | Chosen. |

## Consequences

- the project notes's scenario list keeps 7 with a pointer here, so the numbering of the others is unchanged. The fault category `hot_row_contention` stays in the fixed vocabulary, as `missing_index` did for scenario 8.
- `serialization-retries` still fires in scenario 11, which is why that scenario exists as a no-fault page.
- The `product_ids` field on the retry line and `load.py --hot-product` stay: both are useful and cost nothing when unused.
- COST.md's pass projection is now an over-estimate by one more scenario; left as the conservative figure.
