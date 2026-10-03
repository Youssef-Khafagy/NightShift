# Going public

How this repository became safe to publish. The rule was safety first: anything sensitive is removed everywhere it could be read, not just from the current files.

## What could leak, and where

A public GitHub repository publishes more than its files: every commit on every branch, every pull request (GitHub keeps each one's commits under `refs/pull/`), and every Actions run log for 90 days.

| Value | Risk | Where it was found (2026-10-03) |
|---|---|---|
| AWS account ID | A target for anyone probing AWS accounts | Removed from history on 2026-09-27 (`history-rewrite.md`), but 23 pull requests still held the old commits. Masked in every Actions log. |
| DSQL cluster ID | The database's hostname. Connecting needs an IAM-signed token, so it opens nothing, but it is not needed in public | 4 result files, 4 commits, 25 Actions logs (the apply workflow printed it) |
| The owner's alert email | Spam and phishing | 5 current files, 3 commits from the first milestone, 4 Actions logs |

**It had been public all along.** The working notes said the repository was private from its creation on 2026-09-19. During this migration `gh repo view` reported it public, and GitHub's event log showed it had been made public on its first day, at 04:47 UTC. Everything in the table above was therefore readable for 14 days: the account ID (in history until 2026-09-27, and in 23 pull requests after that), the cluster ID, the alert email, the IAM user name. The old repository was made private the moment this was seen. Then, to find out whether anything worse had been exposed:

- gitleaks over all 305 commits, including the 92 pull-request refs: no API keys, tokens or private keys, ever.
- Forks 0, stars 0, watchers 0; Software Heritage held no copy.
- None of the exposed values is a credential. The AWS user has MFA and no access keys; the root user has MFA and no access keys; the LLM keys were only ever in a git-ignored `.env` and in SSM.

## What was done

1. **The values left the current files.** The cluster ID was scrubbed from the four result files; the Terraform outputs that print it are sensitive, so logs show `<sensitive>`. The alert email moved to a Terraform variable with no default, set from a GitHub secret in CI and a git-ignored `terraform.tfvars` locally; the budget files hold a placeholder. A missing value stops the plan instead of replacing the subscription.
2. **A fresh public repository with cleaned history.** Rather than rewriting the old repository in place, which leaves old commits reachable through its pull requests until GitHub Support removes them, the history was rewritten in a fresh copy and pushed to a new repository. The new one has no pull requests and no old Actions logs, so nothing old is reachable from it at all. The old repository was renamed and stays private.
3. **Every cited commit ID was relabelled.** A rewrite changes every commit's ID. The ones the results and documents cite were mapped to their new IDs: the benchmark's commit, `5641bb7` before, is now `5085754`. `history-rewrite.md` has the table.
4. **Checked before publishing:** no account-ID pattern, cluster ID or email in any file or commit of the new history; every cited commit ID exists; all tests pass.
5. **Settings after publishing:** outside contributors' workflows need approval before they run (a fork's pull request also cannot get an AWS token: its GitHub token is read-only); secret scanning and push protection on; a ruleset on `main` that blocks force pushes and deletion.
6. **The apply gate became a GitHub environment** with the owner as required reviewer, which GitHub offers free only on public repositories. The CI roles' trust policies name the new repository's ID and the `production` environment. Proven on 2026-10-03: the first Apply run in the new repository waited for review, ran only after the owner approved it, assumed the apply role through the environment's token, changed nothing (0 added, 0 changed, 0 destroyed) and passed its smoke test.
