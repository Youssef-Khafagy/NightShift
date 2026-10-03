# ADR 0004: Free plan account, IAM user with MFA, no access keys

Status: Accepted by the owner, 2026-09-18 (M0). Recorded as an ADR 2026-10-03 (M8).

## Context

The project's first rule is that it costs $0. AWS has two account plans. On the Free plan the account cannot be charged at all; usage beyond the Always Free allowances draws on credits, and the account closes after six months unless upgraded. On the Paid plan the same Always Free allowances apply, but anything beyond them is billed.

The usual way to give a developer access is IAM Identity Center, which issues short-lived credentials. Identity Center needs AWS Organizations, and creating or joining an organization upgrades a Free plan account to Paid automatically.

## Decision

- Sign up on the Free plan through the standard flow, and upgrade deliberately before month six (hard deadline 2027-02-15; plan in COST.md).
- Root is locked away: MFA, no access keys, never used day to day.
- Daily access is an IAM user, `youssef-admin`, with MFA and no access keys, permissions only through a group. The CLI authenticates with `aws login`, which issues short-lived credentials from a browser sign-in.
- CI uses GitHub OIDC (no stored keys), and every Lambda uses its execution role.

## Options considered

| Option | Why not |
|---|---|
| Identity Center | Needs Organizations, which forces the Paid plan while still learning. |
| IAM user with access keys | Long-lived secrets on disk; the thing this design exists to avoid. |
| Paid plan from day one | Can be charged by any mistake; the Free plan makes the first months safe by construction. |
| **Free plan, IAM user with MFA and `aws login`** | Chosen. |

## Consequences

- No access key exists anywhere: not on the laptop, not in CI, not in the repository.
- `aws login` sessions refresh for at most 12 hours, so long benchmark sittings start with a fresh login (decision 2026-09-24).
- The Free plan ends in February 2027. The budgets exclude credits, so they report real charges on both plans.
- `aws login` credentials need `botocore[crt]`, which plain boto3 lacks; it is in `requirements/dev.txt`.
