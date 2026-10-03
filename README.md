# NightShift

An AI on-call engineer for AWS. It gets paged, investigates, finds the root cause, proposes allowlisted fixes (and runs them with approval), verifies recovery, and writes a postmortem. A benchmark harness measures how often it is right against baselines.

Status: M0 to M7 complete. The dashboard, demo and a full README come in M8.

After this pass, the fault category definitions were rewritten and the agent now refuses to propose a dead-letter redrive while the cause is a poison message or retry storm. Neither change has been benchmarked; the results below are for the code at `5641bb7`.

## Benchmark results

Pass `m7`, 36 staged incidents (12 fault scenarios, three runs each), run 2026-09-30 to 2026-10-03 at commit `5641bb7`. Each incident is answered by five configurations at once. The two LLM-backed configurations run on Gemini `gemini-3.5-flash-lite` and Mistral `ministral-14b-latest`, with a budget of 100K tokens per investigation.

| | Agent, Gemini | Agent, Mistral | Alarm text only, Gemini | Alarm text only, Mistral | Scripted runbook |
|---|---|---|---|---|---|
| Root cause right (95% interval) | 18/36, 50% (34 to 66) | 5/36, 14% (6 to 29) | 4/36, 11% (4 to 25) | 3/36, 8% (3 to 22) | 21/36, 58% (42 to 73) |
| Hedged (insufficient evidence) | 5 | 2 | 18 | 27 | 9 |
| Action proposed with no fault present | 0 of 3 | 0 of 3 | 0 of 3 | 0 of 3 | 0 of 3 |
| Unsafe proposals (target 0) | 4 | 7 | 10 | 2 | 0 |
| Mean tokens per investigation | 21.8K | 28.9K | 2.1K | 1.8K | 0 |

- The Gemini agent and the scripted runbook are not distinguishable at this size: on the incidents only one of them got right, 5 against 8 (McNemar exact test, p = 0.58). They are right in different scenarios.
- The Gemini agent beats the same model shown only the alarm, 14 incidents to 0 (p = 0.0001). On Mistral, investigating made no measurable difference.
- Proposals are graded, never executed: the approval-gated Actor was invoked 0 times during the pass.
- Prompt injection resistance is untested: the planted note sits in a log field no investigation read.

Per-scenario results, the reasoning behind each finding, and what went wrong are in [LEARNING.md](LEARNING.md), section 20. Raw results are in `results/bench/m7/`.

## More

- Cost plan and free tier limits: [COST.md](COST.md)
- How it works and why: [LEARNING.md](LEARNING.md)
- Design decisions: [docs/decisions/](docs/decisions/)

Every number in this README comes from a real run, labelled with date, commit SHA, and model.
