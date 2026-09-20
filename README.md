# NightShift

An AI on-call engineer for AWS. It gets paged, investigates, finds the root cause, proposes allowlisted fixes (and runs them with approval), verifies recovery, and writes a postmortem. A benchmark harness measures how often it is right against baselines.

Status: M1 complete (Terraform, CI/CD, first Lambda). The store, the chaos framework, the agent, and the benchmark are not built yet.

What runs today:

- Terraform manages every AWS resource, with state in S3 and native locking.
- GitHub Actions authenticates to AWS with OIDC. No stored keys. Pull requests get a read-only plan; apply is a separate manually triggered workflow.
- One `nightshift-hello` Lambda on Python 3.14 and arm64, published as a version and reached through a `live` alias with an IAM-authenticated function URL.

Deploy, pause, and destroy commands arrive in M3.

- Cost plan and free tier limits: [COST.md](COST.md)
- What each milestone taught: [LEARNING.md](LEARNING.md)

All results in this README will come from real runs, labelled with date, commit SHA, and model.
