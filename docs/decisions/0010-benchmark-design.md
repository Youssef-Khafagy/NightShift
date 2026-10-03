# ADR 0010: Every configuration answers the same incidents, kept apart in time, compared in pairs

Status: Accepted by the owner, 2026-09-26 and 2026-09-27 (M7 steps 1 and 2), paired testing 2026-10-03. Recorded as an ADR 2026-10-03 (M8).

## Context

The benchmark compares five configurations: the agent on two models, the same two models shown only the alarm, and a scripted runbook. Three problems had to be solved.

- **Cost.** Injecting a separate incident for every configuration multiplies traffic by five; for 42 incidents that was about 1.5M Lambda requests, over the 1M free.
- **Fairness.** Two configurations given two different incidents of the same scenario are not answering the same question; load, timing and noise differ.
- **Leftovers.** In the M5 live check, two of five wrong answers were built from the previous scenario's leftovers. The tools could look back up to 7 days, so a gap longer than the lookback was impossible.

## Decision

- **One incident, five investigations.** On the first alarm, all five configurations start at the same moment as local processes. Each holds only the Investigator role's temporary credentials, with AWS config and credential files pointed at `/dev/null`, and refuses to run with any other identity. Proposals are graded, never approved, and the Actor's invocation count must stay zero.
- **Incidents kept apart.** Every tool's lookback is capped at 30 minutes, in its schema and at runtime. The runner waits 45 quiet minutes before injecting, checked three ways (its own marker, zero Lambda invocations, no CloudTrail writes on project resources). Afterwards every tool result is scanned for a timestamp from before the previous incident ended; a hit means a re-run, not a grade.
- **Fixed in advance.** One commit for the whole pass, an order shuffled from a recorded seed, and the size of both phases written in the plan before any result existed.
- **Compared in pairs.** Each rate carries a 95% Wilson interval, and two configurations are compared with McNemar's exact test on the incidents exactly one of them got right.

## Options considered

| Option | Why not |
|---|---|
| A separate incident per configuration | Five times the traffic; not the same question. |
| Run the agent in Lambda for the benchmark | GB-seconds for nothing the benchmark needs, and harder to give the baselines identical conditions. |
| A 60-minute cap and 75-minute gap | The first version. Measured at about 100 minutes per incident, seven sittings for the pass; 30 minutes covers the longest incident (28.6 minutes). |
| Compare overlapping intervals | Throws away the pairing; most incidents are easy or hard for everyone. |
| **Shared incidents, capped lookback, paired tests** | Chosen. |

## Consequences

- About 63 minutes per incident; pass m7's 36 incidents took four sittings.
- No run was contaminated, and the Actor was invoked 0 times.
- At 36 incidents the Gemini agent against the runbook was 5 to 8 (p = 0.58): not distinguishable, and the design says so instead of ranking them.
- Hiding the exact age of state older than the lookback removes information a real incident would have; here it would only ever have pointed at the staging.
