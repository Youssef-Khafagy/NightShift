# ADR 0011: Custom metrics are separate names with one fixed dimension

Status: Accepted by the owner, 2026-09-21 (M2b plan). Recorded as an ADR 2026-10-03 (M8).

## Context

CloudWatch bills a custom metric per unique combination of metric name and dimension values, and 10 are free per account and region. A dimension whose values vary multiplies the count: `CheckoutOutcome` with a `reason` dimension is one billed metric per reason, including reasons nobody anticipated. Powertools also emits a cold-start metric by default, which is one more custom metric per service.

The agent needs business facts AWS cannot see (checkouts placed and rejected, orders paid, payment failures, serialization retries), and the alarms need the AWS-published metrics, which are free.

## Decision

- Five metrics, each a separate name with `service` as the only dimension: `CheckoutsPlaced`, `CheckoutsRejected`, `SerializationRetries` (orders), `OrdersPaid`, `PaymentFailures` (fulfillment). Five of the ten free.
- Reasons go in log lines, where the agent finds them with a bounded query, never in a dimension.
- The Powertools cold-start metric is off.
- The ledger in COST.md is the budget, and `tests/test_metrics.py` enforces it: it drives every path that emits a metric, parses the EMF lines, and fails on an unbudgeted name or dimension, on a cold-start metric, or on a budgeted metric nothing emits. Planting each violation failed the test.
- Every alarm is built on AWS-published metrics or on these five.

## Options considered

| Option | Why not |
|---|---|
| One metric with an outcome dimension | Cardinality decides the bill, and a new outcome is a new charge. |
| A metric per reason | Reasons are open-ended; they belong in logs. |
| **Fixed names, one dimension, a test-enforced ledger** | Chosen. |

## Consequences

- The custom metric count is fixed and known: 5 of 10.
- Emitting through EMF means each metric is also a log line, about 210 bytes, counted in the Logs budget, and log groups must stay in the Standard class, because Infrequent Access does not extract EMF.
- Adding a metric is a deliberate change to COST.md and the test, not a line of code.
