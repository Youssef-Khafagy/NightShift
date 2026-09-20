# NightShift

An AI on-call engineer for AWS. It gets paged, investigates, finds the root cause, proposes allowlisted fixes (and runs them with approval), verifies recovery, and writes a postmortem. A benchmark harness measures how often it is right against baselines.

Status: M0 complete (account safety and cost plan). No AWS resources exist yet beyond two billing budgets.

- Cost plan and free tier limits: [COST.md](COST.md)
- What each milestone taught: [LEARNING.md](LEARNING.md)

All results in this README will come from real runs, labelled with date, commit SHA, and model.
