# Going public

The repository is private. This is everything that has to be true before it goes public, what is already done, and the steps left, in order. Steps marked **owner** need the owner's GitHub account or decision.

## Already done (checked 2026-10-03)

- **AWS account ID.** Removed from history on 2026-09-27 (`docs/history-rewrite.md`). No line ever added on any branch matches the account-ID patterns the pre-commit hook blocks. GitHub masks it in Actions logs as a secret: 0 of 142 run logs contain it.
- **Commit identities.** Every commit is authored with the GitHub noreply address; merges were committed by GitHub (`noreply@github.com`).
- **DSQL cluster ID.** Scrubbed from the four committed result files that held it. The Terraform outputs that print it are now sensitive, so apply logs show `<sensitive>`.
- **The dashboard's public data** is built by `scripts/build_replay.py`, which refuses to write anything shaped like an account ID, cluster ID or email.

## What is still exposed

| Value | Current files | Commits in history | Actions run logs |
|---|---|---|---|
| DSQL cluster ID | 0 | 4 (results committed in M2a, M5, M6) | 25 (apply and plan runs that printed it, 2026-09-21 to 2026-10-03) |
| The owner's `+nightshift` Gmail alias | 5 (the project notes, COST.md, two budget JSON files, `terraform/alerts.tf`) | 3 (from M0 on) | 4 |

Neither is a credential. The cluster ID names a hostname that accepts only connections signed with this account's IAM credentials. The alias is a contact address that delivers to the owner's inbox.

## Decisions (owner)

1. **Rewrite history again?** Recommended: **no.** The email is in the very first commits, so a rewrite changes every commit's SHA, including `5641bb7`, which labels every published benchmark number, the README and the dashboard. Every label would then point at a commit that no longer exists, for two values that grant nothing. If yes: `git filter-repo --sensitive-data-removal --replace-text`, force push every branch, map old SHAs to new as in `docs/history-rewrite.md`, relabel the results, and add the new pull requests to the Support request below.
2. **Remove the email from the current files?** It would then be visible only in history. Needs a GitHub secret `ALERT_EMAIL`, the workflows passing it as `TF_VAR_alert_email`, a gitignored `terraform.tfvars` locally, and the budget JSON files templated. Recommended only if the address matters to you; the SNS subscription does not change either way.
3. **Delete the 28 run logs** that contain either value? Recommended: **yes.** It deletes the logs only; the runs and their results stay. Command, run by the owner or by the maintainer after a yes:

```bash
for id in 35547891236 35556244146 35556360448 35557245628 35558946658 35559069294 \
          35559171032 35559562644 35559636138 35559733630 35648221426 35784945381 \
          35785162302 35786000963 35790880790 35793177437 35797052200 35798799639 \
          35800987322 35803956785 35804074064 35804719486 35804833515 35806659456 \
          35809338963 36285256245 36369392425 37086639281; do
  gh api -X DELETE "repos/Youssef-Khafagy/NightShift/actions/runs/$id/logs"
done
```

## Steps, in order

1. **owner: GitHub Support request** for the pull request refs that still hold the pre-rewrite commits (outstanding since 2026-09-28). Paste at support.github.com:
   > Repository: Youssef-Khafagy/NightShift (private). I removed sensitive data (an AWS account ID) from history with git-filter-repo `--sensitive-data-removal` and force pushed all branches. Please dereference the affected pull requests #51 to #73 (23 PRs), run garbage collection, and remove cached views. First changed commits reported by git-filter-repo: b0b38ae5632fefc964a31be25bea6e76fc9f355f and 66f29db1bd97e792ddbaf99bbc5239f563e20caa. No LFS objects.

   Wait for GitHub's confirmation before step 4: until then the old commits are reachable through those pull requests.
2. **Decide** the three questions above, and carry out what was decided.
3. **Check again** right before the switch (the maintainer runs it): the history scan, `git grep` for both values, and the pre-commit hooks on all files.
4. **owner: make it public.** Settings, General, Danger Zone, Change visibility.
5. **owner, immediately after:**
   - Settings, Actions, General, "Fork pull request workflows from outside collaborators": **Require approval for all outside collaborators.** A fork's pull request cannot get an OIDC token by default (its token is read-only unless "Send write tokens to workflows from pull requests" is on, which must stay off), but approval also stops it from using the free runners for anything else.
   - Settings, Code security: turn on **secret scanning** and **push protection** (free on public repositories).
   - Settings, Rules, new branch ruleset on `main`: **block force pushes** and **restrict deletions**. Not "require a pull request": work goes straight to main.
6. **The apply gate moves to a GitHub environment** (free on public repositories; the project notes has planned this since M1):
   1. owner: Settings, Environments, New environment `production`: required reviewer Youssef-Khafagy; deployment branches: `main` only; leave "Prevent self-review" **off** (one maintainer).
   2. the maintainer: `apply.yml`'s job gains `environment: production`. That changes the OIDC subject GitHub sends from `...:ref:refs/heads/main` to `...:environment:production`, so the apply role's trust policy in `terraform/ci_oidc.tf` changes with it. The CI apply role is denied changing its own trust, so this is a local `terraform apply` (expected 0 to add, 1 to change), plan shown first. Then push the workflow change.
   3. Test: trigger Apply; it waits for the reviewer; approve; it runs.
7. **Update** the README's status line and the project notes.
