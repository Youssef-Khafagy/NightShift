# Going public

How this repository became safe to publish. The rule was safety first: anything sensitive is removed everywhere it could be read, not just from the current files.

## What could leak, and where

A public GitHub repository publishes more than its files: every commit on every branch, every pull request (GitHub keeps each one's commits under `refs/pull/`), and every Actions run log for 90 days.

| Value | Risk | Where it was found (2026-10-03) |
|---|---|---|
| AWS account ID | A target for anyone probing AWS accounts | Removed from history on 2026-09-27 (`history-rewrite.md`), but 23 pull requests still held the old commits. Masked in every Actions log. |
| DSQL cluster ID | The database's hostname. Connecting needs an IAM-signed token, so it opens nothing, but it is not needed in public | 4 result files, 4 commits, 25 Actions logs (the apply workflow printed it) |
| The owner's alert email | Spam and phishing | 5 current files, 3 commits from the first milestone, 4 Actions logs |

## What was done

1. **The values left the current files.** The cluster ID was scrubbed from the four result files; the Terraform outputs that print it are sensitive, so logs show `<sensitive>`. The alert email moved to a Terraform variable with no default, set from a GitHub secret in CI and a git-ignored `terraform.tfvars` locally; the budget files hold a placeholder. A missing value stops the plan instead of replacing the subscription.
2. **A fresh public repository with cleaned history.** Rather than rewriting the old repository in place, which leaves old commits reachable through its pull requests until GitHub Support removes them, the history was rewritten in a fresh copy and pushed to a new repository. The new one has no pull requests and no old Actions logs, so nothing old is reachable from it at all. The old repository was renamed and stays private.
3. **Every cited commit ID was relabelled.** A rewrite changes every commit's ID. The ones the results and documents cite (the benchmark's `5641bb7`, for one) were mapped to their new IDs; `history-rewrite.md` has the table.
4. **Checked before publishing:** no account-ID pattern, cluster ID or email in any file or commit of the new history; every cited commit ID exists; all tests pass.
5. **Settings after publishing:** outside contributors' workflows need approval before they run (a fork's pull request also cannot get an AWS token: its GitHub token is read-only); secret scanning and push protection on; a ruleset on `main` that blocks force pushes and deletion.
6. **The apply gate became a GitHub environment** with the owner as required reviewer, which GitHub offers free only on public repositories. The CI roles' trust policies name the new repository's ID and the `production` environment.
