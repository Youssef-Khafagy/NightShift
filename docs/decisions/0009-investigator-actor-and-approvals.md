# ADR 0009: A read-only Investigator, a separate Actor, and approvals bound to one action

Status: Accepted by the owner, 2026-09-23 (M5 step 3) and 2026-09-24 (M6, decision B). Recorded as an ADR 2026-10-03 (M8).

## Context

The agent reads logs, alarm reasons and order notes, and all of it can contain text written by someone else. Scenario 13 plants an order note telling the on-call engineer to roll orders back. Whatever the model decides, it must not be able to act on what it read, and it must not be able to talk a human into approving something different from what they were shown.

## Decision

- **Investigator role.** Every tool runs as a role with reads on project resources and explicit denies on IAM, role chaining, the Terraform state, every write, invoking functions and receiving queue messages, plus a permissions boundary so even an admin policy attached by mistake grants only reads. The agent's own Lambda role holds the API keys and checkpoint writes, and assumes the Investigator for tools.
- **Five allowlisted actions, proposals only.** `rollback_alias`, `set_operational_flag` (two flags, values limited by their SSM patterns), `pause_queue_consumer`, `resume_queue_consumer`, `redrive_dlq`. An action must fit the diagnosis: a rollback targets the component found at fault, queue actions need a queue-side cause, and a redrive is refused while the cause is a poison message or a retry storm.
- **A separate Actor** is the only thing that can perform them, on exact ARNs, with explicit denies and a permissions boundary. It has no function URL and no resource policy, and neither the agent's role, the Investigator nor the CI roles can invoke it.
- **Approvals** are records bound to a hash of investigation, item and action, expiring after 15 minutes, consumed by one conditional update. The owner approves with `scripts/approve.py` under their own IAM login by typing `approve`. Never by GET request or email link, which a mail scanner can open.
- **The Actor re-checks everything** against the saved report, as if the agent were the attacker, acts once, watches the alarm for up to 10 minutes, reports recovered, not recovered or inconclusive, and writes an append-only audit record. One action at a time, at most three an hour.

## Options considered

| Option | Why not |
|---|---|
| One role, with approval enforced in code | A bug or an injected instruction is one code path away from a write. |
| Let the agent act on its own above a confidence | Confidence is a number the model writes; in pass m7 wrong answers were often at 95. |
| Approve by email link | A GET that changes state; mail scanners follow links. |
| **Separate identities, hash-bound approvals** | Chosen. |

## Consequences

- Proven live in M6: a rollback approved through `approve.py`, executed by the Actor and verified recovered; an approval used after expiry was refused with nothing done.
- The benchmark grades proposals and never executes them; the Actor's invocation count is checked to be zero after every incident.
- The fit check did not stop a correct poison-message diagnosis from proposing a redrive; the redrive rule was added after the pass. Human approval is the last safeguard by design.
