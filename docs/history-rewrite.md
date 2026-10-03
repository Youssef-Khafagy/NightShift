# History rewrites

The repository's history was rewritten twice, both times to remove a value that should not be public. This file says what, how, and maps the commit IDs that results and documents cite.

## Second rewrite, 2026-10-03: the public repository

**What.** The owner's alert email and the DSQL cluster ID, replaced by `<ALERT_EMAIL>` and `<DSQL_CLUSTER_ID>` in every file of every commit (the account ID was included again as a check; none was left). A working-notes file was removed from every commit, and its name from commit messages.

**How.** `git filter-repo --replace-text --replace-message --invert-paths` on a fresh copy of `main` and its two tags, never on the original. 251 commits became 234: the 17 dropped commits had changed only the notes file. A second pass the same day replaced the notes file's name where older versions of other files mentioned it. Checked before publishing: neither value, nor any account-ID pattern, appears in any file of any commit or in any commit message; every commit has the noreply identity; `main`'s files are byte-identical to before.

**Published as a new repository.** A rewrite in place leaves the old commits reachable through every pull request until GitHub Support removes them. The rewritten history was pushed to a new repository instead, which has no old pull requests and no old Actions logs. The old repository was renamed and stays private.

**Commit IDs.** Every ID changed. The ones cited in results and documents were relabelled from filter-repo's own map. Two cited commits were dropped because they changed only the notes file; each maps to its parent, whose files are identical except where the two values were replaced.

| Cited before | Now | Commit |
|---|---|---|
| 5641bb7 | 5085754 | The benchmark pass m7 ran here (tag `m7-freeze`). Dropped: it changed only the notes file; this is its parent, same code |
| dd0d814 | 787e03b | M4, end of the first chaos night (PR #42). Dropped the same way; its parent, same code |
| f7e0ed8 | c4e1a37 | Chaos runner stops when warm-up traffic dies |
| 390fcb3 | 713e6bc | M4 scenarios (PR #40) |
| 66184f1 | 5117a44 | M4 load guard; the step 4 batch ran here (PR #43) |
| 5c067d4 | 3295956 | M2b load generator; its live check ran here (PR #19) |
| 71888ad | 5796d12 | M2b cost check (PR #22) |
| adaa22f | 60ffee2 | M7, product IDs on the retry log line (PR #75) |
| b7c278c | 3570690 | Main before the first rewrite (`b8d9407` originally) |
| 0839358 | 06d3c65 | M7 verification sitting 1 (`8213358` originally) |
| cc83fc2 | be93fc6 | M6 step 7 results (`eb8f5df` originally) |
| b0db22f | c56ad3a | M5 step 7 results (`51b3252` originally) |
| 988fe7f | 45c78cb | M5 step 4 (`66f29db` originally) |

## First rewrite, 2026-09-27: the account ID

### What happened

The AWS account ID was committed in commit 66f29db (M5 step 4), inside an SQS queue URL in `results/investigations/416c87aa9650.json`. The pre-commit hook only recognised ARNs at the time, so nothing stopped it. It was found in M7 verification sitting 1, the file was scrubbed, and the hook now also catches queue URLs and `accountId` fields.

Scrubbing the file only fixes the newest version. Every commit from 66f29db on still carried the ID, so the history was rewritten before the repo goes public (owner decision, 2026-09-27).

### How

- `git filter-repo --sensitive-data-removal --replace-text`, replacing the ID with `<ACCOUNT_ID>` in every file version.
- Only the commits from 66f29db onward were rewritten (`--refs 66f29db^..<branch>` for each of the 11 GitHub branches that contained it). Rewriting everything would have changed 192 commits instead of 56, because filter-repo drops GitHub's signatures from merge commits, and every earlier SHA quoted anywhere would have changed with them.
- Dry run on a scratch mirror first. Checked before pushing: no commit on any branch adds or removes the ID, no branch's files contain it, and main's file tree is byte-identical before and after.
- Force pushed with a lease on each branch's old SHA. A fresh clone afterwards found the ID in no branch.

### What is not covered by the push

GitHub makes pull request refs read-only, so the 23 closed pull requests whose branches contained 66f29db still hold the old commits until GitHub Support dereferences them and runs garbage collection. That request is the owner's to make, and must be done before the repo is made public.

### Old and new SHAs

Commit SHAs quoted in docs and results before the rewrite. The results files keep the old values, because they are records of what ran; this table maps them.

| Old | New | Commit |
|---|---|---|
| 66f29db | 988fe7f | M5 step 4: the investigation loop, checkpointed |
| 51b3252 | b0db22f | Chaos runner plans with the trigger enabled when run with --agent (M5 step 7 results) |
| eb8f5df | cc83fc2 | Keep a correct answer that cites unusable steps (M6 step 7 results) |
| 8213358 | 0839358 | Merge pull request #71 (M7 verification sitting 1 results) |
| b8d9407 | b7c278c | Merge pull request #73 (main before the rewrite) |

The `nightshift-deployments` table in AWS also records git SHAs for past deploys. Those rows are history and are left as they are; any SHA from 66f29db onward in them maps through the same rewrite.
