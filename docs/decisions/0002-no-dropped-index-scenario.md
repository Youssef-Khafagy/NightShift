# ADR 0002: No dropped-index scenario (scenario 8)

Status: Accepted by the owner, 2026-09-26 (M7 step 6).

## Context

The benchmark plan listed scenario 8, "slow query from dropped index (propose-only)". No secondary index exists in the store: every query uses a primary key. The M7 plan proposed adding a customer order-history read backed by an index, so there would be an index to drop.

Before building it, the symptom was measured, because the store is already in the "dropped" state: there is no index on `orders.customer_id` today. On 2026-09-26, from the laptop as admin:

- `orders` holds 16,276 rows for 22 customers; the largest customer has 1,800 orders.
- `SELECT ... FROM orders WHERE customer_id = %s ORDER BY created_at DESC LIMIT 20` plans as a full scan and takes 45 to 80 ms, including the network round trip.
- The alarm that would page for a slow query, `checkout-latency`, fires on orders' p99 duration above 2,000 ms for three minutes.

The unindexed query is about 40 times too fast to page anyone. A missing index only hurts once the table is far larger than a synthetic store of this size.

## Decision

Drop scenario 8 from the benchmark. The pass is 13 scenarios and 39 incidents.

## Options considered

| Option | Why not |
|---|---|
| Add the index and the endpoint anyway | The alarm would never fire, so the agent would never be paged. A scenario nothing detects measures nothing. |
| Bulk-load about 600K synthetic orders so a full scan crosses 2 s | Unmeasured whether it would; about 1,000 DPU per incident in scans; storage and pruning to manage; and the store would exist to make one scenario work. |
| A dedicated alarm on the query's own duration | All 10 free alarm metrics are in use. |
| **Drop it** | Chosen. The finding is recorded; the fault category `missing_index` stays in the fixed vocabulary, since removing it would change the enum the agent answers in. |

## Consequences

- the project notes's scenario list keeps 8 with a pointer here, so the numbering of the other scenarios is unchanged.
- COST.md's pass projection of 42 incidents is now an over-estimate by three incidents; left as the conservative figure.
