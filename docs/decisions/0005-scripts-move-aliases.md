# ADR 0005: Terraform publishes versions; scripts move aliases

Status: Accepted by the owner, 2026-09-22 (M3). Extended to the queue consumer 2026-09-24 (M6, decision A). Recorded as an ADR 2026-10-03 (M8).

## Context

Every function is called through a `live` alias, so a rollback is one call: point the alias at the previous version. Until M3, Terraform owned each alias's version. Any move made outside Terraform, by a rollback script or by the agent's `rollback_alias` action, was drift, and the next routine `terraform apply` would have moved the alias forward again without anyone asking. A rollback that the next deploy silently undoes is worse than none.

The agent also needs to answer "what changed just before this started", which needs a record of every move, not only Terraform's state.

## Decision

- Terraform creates each alias and publishes versions, with `ignore_changes` on the alias's `function_version`. It never moves an alias after creating it.
- `scripts/deploy.py` (run by the apply workflow) moves each alias to the version Terraform just published, writes one row per move to the `nightshift-deployments` table (previous and new version, git SHA, time, actor), runs the smoke test, and on any failure moves every alias it touched back and records `auto-rollback`.
- `scripts/rollback.py` moves one service back, requires a reason, records it, and refuses when the table and the alias disagree.
- The same split applies to the queue consumer: Terraform ignores the event source mapping's `enabled`, and `scripts/consumer.py` switches it, so an Actor pause survives the next apply.

## Options considered

| Option | Why not |
|---|---|
| Terraform owns the alias; roll back by changing a variable and applying | Every rollback becomes a full apply through CI; the agent's action would need Terraform. |
| CodeDeploy for Lambda | Another service, traffic shifting this project does not need, and its own alias ownership. |
| Call `$LATEST` | No versions means nothing to roll back to. |
| **Split ownership** | Chosen. |

## Consequences

- After four moves of orders' alias (17, 16, 17, 16, 17), `terraform plan` stayed clean.
- The deployments table is evidence the agent reads, so its rows must never carry answers: chaos rollbacks use a neutral reason, checked by a banned-words test.
- A chaos recovery deletes the version it published, so the injected version is never left as the newest one for the next deploy to ship.
