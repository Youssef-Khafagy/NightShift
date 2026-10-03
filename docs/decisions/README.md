# Decision records

One page per decision that shaped the system: the context, what was decided, what else was considered and why not, and what followed. The full decisions log, with every smaller call, is [log.md](log.md).

| ADR | Decision | Date |
|---|---|---|
| [0001](0001-no-in-code-tracing.md) | No in-code tracing; Lambda active tracing only | 2026-09-22 |
| [0002](0002-no-dropped-index-scenario.md) | No slow-query scenario from a dropped index | 2026-09-26 |
| [0003](0003-no-hot-row-scenario.md) | No hot-row contention scenario | 2026-09-28 |
| [0004](0004-free-plan-and-iam-user.md) | Free plan account, IAM user with MFA, no access keys | 2026-09-18 |
| [0005](0005-scripts-move-aliases.md) | Terraform publishes versions; scripts move aliases | 2026-09-22 |
| [0006](0006-internal-calls-use-invoke.md) | Internal calls use the Lambda Invoke API, not function URLs | 2026-09-21 |
| [0007](0007-dsql-autocommit-by-default.md) | DSQL connections autocommit by default; transactions are explicit | 2026-09-21 |
| [0008](0008-agent-from-scratch.md) | The agent is built from scratch, with no framework and no SDKs | 2026-09-23 |
| [0009](0009-investigator-actor-and-approvals.md) | A read-only Investigator, a separate Actor, approvals bound to one action | 2026-09-24 |
| [0010](0010-benchmark-design.md) | Every configuration answers the same incidents, kept apart in time, compared in pairs | 2026-09-27 |
| [0011](0011-custom-metrics-by-name.md) | Custom metrics are separate names with one fixed dimension | 2026-09-21 |
| [0012](0012-static-public-dashboard.md) | The public dashboard is a static replay; live views sit behind a login | 2026-10-03 |

0001 to 0003 were written when the decision was made. 0004 to 0011 were written in M8 from the decisions log, to record decisions made earlier; they add no new decisions.
