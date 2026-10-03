# Pass m7: decisions made during the pass

Recorded here because nothing is committed to main while the pass runs (freeze rule). Copied into the decisions log (docs/decisions/log.md) once the pass ended.

- 2026-10-01, owner: phase 1 entry 13 (scenario 11, run 1) is counted on its second attempt. The first attempt (`results/chaos/11-legit-spike-20261001T030042Z`) never paged: 1 serialization retry in the whole spike, and the alarm needs 10 a minute for 2 minutes. It was re-queued at 10:11 UTC and re-run (`results/chaos/11-legit-spike-20261001T144735Z`). The spike again did not reach the retry alarm (1, 1, 3, 1 a minute); the page came from `nightshift-throttles`, one orders throttle during warm-up at 15:38 UTC, which changed state 0.2 s after injection. Counted because it is a real page with no fault, which is what scenario 11 tests, and M4 already treats a stray throttle as noise. Reported as such in the results. Scenario 11's own alarm has now failed to page in 2 of 4 runs, the same unexplained variance as scenario 7 (ADR 0003).
- 2026-10-01, owner: run phase 2 (entries 19 to 36) at the same commit, 5085754.
