# History rewrite, 2026-09-27

## What happened

The AWS account ID was committed in commit 66f29db (M5 step 4), inside an SQS queue URL in `results/investigations/416c87aa9650.json`. The pre-commit hook only recognised ARNs at the time, so nothing stopped it. It was found in M7 verification sitting 1, the file was scrubbed, and the hook now also catches queue URLs and `accountId` fields.

Scrubbing the file only fixes the newest version. Every commit from 66f29db on still carried the ID, so the history was rewritten before the repo goes public (owner decision, 2026-09-27).

## How

- `git filter-repo --sensitive-data-removal --replace-text`, replacing the ID with `<ACCOUNT_ID>` in every file version.
- Only the commits from 66f29db onward were rewritten (`--refs 66f29db^..<branch>` for each of the 11 GitHub branches that contained it). Rewriting everything would have changed 192 commits instead of 56, because filter-repo drops GitHub's signatures from merge commits, and every earlier SHA quoted anywhere would have changed with them.
- Dry run on a scratch mirror first. Checked before pushing: no commit on any branch adds or removes the ID, no branch's files contain it, and main's file tree is byte-identical before and after.
- Force pushed with a lease on each branch's old SHA. A fresh clone afterwards found the ID in no branch.

## What is not covered by the push

GitHub makes pull request refs read-only, so the 23 closed pull requests whose branches contained 66f29db still hold the old commits until GitHub Support dereferences them and runs garbage collection. That request is the owner's to make, and must be done before the repo is made public.

## Old and new SHAs

Commit SHAs quoted in docs and results before the rewrite. The results files keep the old values, because they are records of what ran; this table maps them.

| Old | New | Commit |
|---|---|---|
| 66f29db | 988fe7f | M5 step 4: the investigation loop, checkpointed |
| 51b3252 | b0db22f | Chaos runner plans with the trigger enabled when run with --agent (M5 step 7 results) |
| eb8f5df | cc83fc2 | Keep a correct answer that cites unusable steps (M6 step 7 results) |
| 8213358 | 0839358 | Merge pull request #71 (M7 verification sitting 1 results) |
| b8d9407 | b7c278c | Merge pull request #73 (main before the rewrite) |

The `nightshift-deployments` table in AWS also records git SHAs for past deploys. Those rows are history and are left as they are; any SHA from 66f29db onward in them maps through the same rewrite.
