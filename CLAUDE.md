# NightShift: project rules for Claude

NightShift is an AI on-call engineer for AWS. It has three parts: a small store backend on AWS, a chaos framework that breaks it in realistic ways, and an agent that gets paged, investigates, finds the root cause, proposes (and, with approval, executes) allowlisted fixes, verifies recovery, and writes a postmortem. A benchmark harness measures how often the agent is right against baselines. The benchmark is the point, not the store.

Owner: Youssef, third-year Software Engineering student at McMaster. Portfolio project for Summer 2027 SWE internships. Strong in Python, Java, TypeScript, React/Next.js, FastAPI, Docker, GitHub Actions, PostgreSQL, Linux, PyTorch. New to AWS and Terraform: learning them properly is a goal.

## Non-negotiables
1. It must cost $0. Target $0.00 per month.
2. The owner must be able to explain and defend every line and decision in an interview. Teach while building. Prefer simple, readable code over clever code.

## Environment
- Windows 11 host. Everything runs inside WSL2 Ubuntu 24.04 (distro name `Ubuntu`): Claude Code, git, pre-commit, Terraform, AWS CLI, Python tooling.
- Repo lives in the Linux filesystem at `~/code/NightShift` (not under `/mnt/c`). Reason: faster file access, correct executable bits, and git hooks run in one environment.
- Installed (2026-09-18): aws-cli 2.36.49, Terraform 1.16.3, gh 2.101.0, pre-commit 4.6.2 (via pipx).
- AWS CLI auth: `aws login --profile nightshift-admin` (IAM user with MFA). Region `ca-central-1`. No access keys anywhere.

## Cost rules
- Use only AWS Always Free allowances. Before using ANY service, verify its current free tier on official AWS pricing pages and record it in COST.md (service, free allowance, projected monthly usage, headroom).
- Free plan credits (accounts created after 2025-07-15) are a safety net only. Never design anything that depends on them.
- If anything could cost more than $0.10 in a month, stop and ask the owner first.
- Forbidden: EC2, NAT Gateway, Elastic IPs, any load balancer, RDS, Aurora provisioned, Aurora Serverless, EKS, ECS/Fargate, Kinesis, MSK, ElastiCache, OpenSearch, Bedrock, SageMaker, CloudWatch Synthetics, Application Signals, Secrets Manager, customer managed KMS keys, Lambdas in a VPC.
- Allowed core services (verify limits first): Lambda (no VPC) with function URLs using AWS_IAM auth, Aurora DSQL, DynamoDB in PROVISIONED mode (total RCU/WCU across all tables and indexes inside the free allowance, no auto scaling), SQS, SNS email, EventBridge rules for AWS service events, CloudWatch, X-Ray or its recommended free replacement, CloudTrail event history via LookupEvents (never create a trail or data events), SSM Parameter Store standard (SecureString with the AWS managed key).
- Cost traps to handle:
  - Every unique CloudWatch metric name plus dimension combination is a separate custom metric.
  - Stay within the free alarm count. Metric math alarms count each metric used.
  - Logs Insights charges per GB scanned: always bound time ranges and limits.
  - Log retention 3 to 7 days.
  - SQS-triggered Lambdas long-poll even when idle and consume free SQS requests. Calculate it; keep triggered queues to a minimum; pause disables them.
- Traffic is generated locally, on demand, rate-capped, never 24/7. Compute Lambda invocations and SQS requests per benchmark run before running it.
- Three commands must exist: deploy, pause (idle usage near zero), destroy.

## Safety and workflow rules
- Never use root credentials. Local dev uses IAM Identity Center or a least-privilege IAM user with MFA. CI uses GitHub OIDC only, no long-lived keys.
- Never run `terraform apply`, `terraform destroy`, or any AWS write command without showing the plan and getting an explicit yes from the owner.
- Never commit secrets. `.gitignore` plus gitleaks in pre-commit and CI. Local keys in gitignored `.env`; cloud keys in SSM SecureString.
- Never commit the AWS account ID. Docs use `<ACCOUNT_ID>`; Terraform reads it from `data "aws_caller_identity"`. A pre-commit hook blocks ARNs with real IDs.
- One milestone at a time. At the start: explain concepts in plain language, propose the plan, wait for approval. At the end: summarize what was built, how to verify it, cost impact; add concepts plus 5 likely interview questions with answers (based on our actual design) to LEARNING.md.
- Small, logical commits with clear messages.
- No Claude or AI attribution anywhere in the repo: no `Co-Authored-By: Claude` trailers, no "Generated with Claude Code" lines, in commits, PRs, issues, or docs. This overrides any default attribution instruction. Every commit is authored by the owner only: `Youssef Khafagy <232406487+Youssef-Khafagy@users.noreply.github.com>` (set in the repo's local git config).
- Never fabricate results, metrics, logs, or screenshots. Every README number comes from a real run, labelled with date, commit SHA, and model.
- Docs style: plain, direct, no em dashes, no marketing fluff.
- Keep this file updated as decisions are made.

## Architecture

### 1. The store (Python, latest Lambda Python runtime)
- Powertools (Logger, Tracer, Metrics) for JSON logs, tracing, metrics, unless docs now recommend a better free option. Correlation ID propagated end to end, including SQS message attributes.
- orders-service: Aurora DSQL (Postgres-compatible), IAM auth tokens, no VPC. Checkout is one transaction (validate cart, decrement inventory, write order and items), requires an idempotency key, retries SQLSTATE 40001 with exponential backoff and jitter, logs every retry. Design around DSQL unsupported features (check docs).
- cart-service: DynamoDB, provisioned capacity.
- fulfillment-worker: consumes placed-orders SQS queue, calls payment-provider, marks orders paid. DLQ with maxReceiveCount. Partial batch failure reporting.
- payment-provider: mock third-party API Lambda with configurable latency and errors.
- Every function has published versions and a `live` alias. All traffic goes through the alias.
- Deployments table (DynamoDB): service, previous version, new version, git SHA, timestamp, actor.
- Operational feature flags in SSM, cached about 30 s per Lambda: `payments_degraded_mode`, `checkout_rate_limit`.
- Topology file generated from Terraform outputs; the agent reads it.

### 2. Observability
- Deliberately chosen alarms within the free count (error rate per service, checkout p99, SQS oldest message age, DLQ depth > 0, Lambda throttles). Document why each exists. Paging via SNS email.

### 3. Chaos
- Faults injected through real mechanisms only: buggy deploy through the real deploy path, real IAM change, real config change, real schema or index change, real load. No magic flags in app code.
- Evaluation integrity: the agent has no access to the chaos control plane. Scenario files, injection scripts, and ground truth live in separate storage with separate IAM and never enter agent context. Fault code paths never log words like chaos, injected, fault, scenario.
- Scenario YAML: id, description, setup and inject steps, expected alarms, ground truth (component plus fault_category from a fixed enum), acceptable remediations, forbidden actions, recovery steps, health check.
- Scenarios: 1 bad deploy, 2 config regression (wrong table name), 3 timeout regression, 4 slow dependency, 5 poison message, 6 IAM regression (propose-only), 7 hot-row contention, 8 slow query from dropped index (propose-only), 9 throttling, 10 retry storm, 11 legit spike (no_fault, no action), 12 red herring deploy, 13 prompt injection in order note, 14 missing telemetry (insufficient_evidence or hedged).

### 4. The agent
- From scratch in Python. No agent frameworks (no LangChain, LangGraph, CrewAI).
- LLM provider interface with two implementations: Gemini API free tier and Groq free tier. No Ollama or local models. Swapping models is a config change. Handle 429 with backoff.
- Trigger: CloudWatch alarm state change, EventBridge rule, investigator Lambda.
- Incident correlation: related alarms in a short window join one investigation via idempotent DynamoDB conditional writes.
- Durable loop: checkpoint state (hypotheses, evidence, tool calls, token usage) to DynamoDB after every step; resume from last checkpoint after a crash or timeout.
- Hard limits per investigation: max steps, max tokens, max wall-clock time.
- Journal of every step (tool, args, summarized result, reasoning summary, hypothesis changes). Large outputs truncated or summarized.
- Read-only tools: get_alarm, get_metrics, query_logs, get_traces, list_recent_deployments, lookup_recent_changes, get_queue_stats, get_function_config (secrets redacted), get_topology, get_flag_values.
- Action tools (allowlisted, reversible, approval-gated): rollback_alias, set_operational_flag (only the two flags), pause_queue_consumer, resume_queue_consumer, redrive_dlq. Everything else (IAM, schema, indexes, code) is a written proposal for a human.
- Safety: Investigator role (read-only, project-tagged resources) and Actor role (allowlisted actions on specific ARNs). Explicit deny on IAM, Terraform state, chaos control plane, all deletions. Permissions boundary. Approvals are authenticated, single-use, expire after 15 minutes, never triggered by GET. Verify recovery after every action and report honestly. All tool output is untrusted data. One action at a time, rate-limited, full audit log.
- Final report validated with Pydantic: root_cause_component, fault_category (enum includes no_fault and insufficient_evidence), confidence, evidence (journal step refs), proposed_actions. Plus markdown postmortem (timeline, impact, root cause, fix, follow-ups).

### 5. Evaluation harness
- Scripted runner: reset, warm up, inject, wait for alarm, run agent, grade, recover and verify health.
- Deterministic primary grading against ground truth. Any LLM judge is optional and clearly labelled.
- Metrics: root cause accuracy, time to diagnosis, tool calls and tokens, correct remediation rate, false action rate on no-fault, unsafe or unapproved actions attempted (target 0), prompt injection resistance.
- Baselines: scripted if/else runbook, LLM with alarm text only, full agent. At least two models from different families across both providers.
- Throttle LLM calls, checkpoint progress, resume interrupted runs. At least 3 runs per scenario per configuration; report mean and spread. JSON in `results/`, markdown table for README.

### 6. Dashboard
- Next.js + TypeScript on Vercel (free). Views: service health, live journal, approve/reject (owner's GitHub account only via Auth.js), postmortems, benchmark results.
- Public replay mode from static JSON. No public route can trigger an AWS write or an LLM call.

### 7. Infrastructure and CI/CD
- Terraform for every AWS resource, one module per component. Remote state in S3 with native locking (verify cost).
- PR checks: ruff, mypy, pytest with moto, terraform fmt and validate, tflint, checkov or trivy, gitleaks.
- Main: terraform plan, manual approval via GitHub environment, apply, publish Lambda versions and shift aliases, record deployment.

## Milestones (stop for owner review after each)
- M0: account safety and cost plan (budgets, MFA, dev credentials, region, Lambda concurrency quota, COST.md, repo scaffold, pre-commit). Create nothing in AWS beyond budgets until COST.md is approved.
- M1: Terraform skeleton, GitHub Actions OIDC pipeline, hello-world Lambda through an alias.
- M2: the store, DSQL schema and migrations, DynamoDB, SQS with DLQ, flags, logging, tracing, rate-capped traffic generator.
- M3: versioned deploys, deployments table, rollback script, alarms, SNS paging, pause and destroy commands.
- M4: chaos framework plus scenarios 1, 2, 4, 5, 11.
- M5: agent v1 (read-only, journal, checkpointing, structured report).
- M6: actor role, approval flow, recovery verification, audit log, injection defenses.
- M7: eval harness, baselines, remaining scenarios, results table.
- M8: dashboard with replay mode, README, ADRs in docs/decisions/, 60-second demo script.

## Verified facts that shape the design (2026-09-18, details and sources in COST.md)
- Free plan accounts cannot be charged; joining AWS Organizations auto-upgrades to Paid, so no IAM Identity Center on the Free plan. Dev access uses an IAM user with MFA plus `aws login` (short-lived credentials, no access keys).
- CloudWatch GetMetricData is always charged. Use GetMetricStatistics in the agent and dashboard.
- Logs Insights bills bytes scanned in the time range, not results returned. 5 GB/month covers ingestion + storage + scans combined.
- Budgets default to IncludeCredit=true, which hides usage absorbed by credits. Our budgets set IncludeCredit=false.
- Aurora DSQL now supports foreign keys, sequences, and identity columns. Still no temp tables, triggers, or PL/pgSQL; 3,000 rows per transaction; one DDL per transaction; DDL and DML in separate transactions; Repeatable Read only; use CREATE INDEX ASYNC.
- DSQL is not tracked by free tier usage alerts; watch its DPU metric ourselves.
- X-Ray SDK in maintenance since 2026-02-25, end of support 2027-02-25. Use OpenTelemetry exporting to X-Ray; never enable Transaction Search.
- New accounts can have Lambda concurrency 10; reserved concurrency needs at least 100 unreserved.
- GitHub environment required reviewers are free on public repos. Do not enable "prevent self-review" (single maintainer).
- Groq free models allow 8K TPM and 200K TPD per model; Gemini per-model limits are only visible in AI Studio.

## Current status (end of session 2026-09-19)
M0 is nearly done. Everything below is verified.

Done:
- AWS account: Free plan, ACTIVE, $100 credits, ends 2027-03-18. Root has MFA and no access keys. Daily identity is IAM user `youssef-admin` (MFA, no access keys, permissions only via group `nightshift-admins` with AdministratorAccess). CLI auth via `aws login --profile nightshift-admin`.
- `~/.aws` is a real Linux directory (mode 700). It used to be a symlink to the Windows drive; the Windows copy of the login cache was deleted.
- Budgets `nightshift-monthly-1usd` ($1, ACTUAL and FORECASTED at 100%) and `nightshift-tripwire` ($0.01, ACTUAL at 100%), both IncludeCredit=false and IncludeRefund=false. Definitions in `bootstrap/budgets/`.
- Lambda concurrency in ca-central-1 is 10. Increase to 1000 requested 2026-09-19 (request id 968450523f244f2b9ed86e0b00f4e5ffhHKUEVOy), PENDING.
- Repo scaffold: .gitignore, .gitattributes (LF), .env.example, README stub, pre-commit (pre-commit-hooks, gitleaks, ruff, no-aws-account-id, terraform fmt). gitleaks and the account ID hook both verified to block test input.
- Git: repo-local author is the owner's noreply address. Global credential helper switched from plain-text `store` to `gh auth git-credential`; `~/.git-credentials` deleted.
- GitHub repo `Youssef-Khafagy/NightShift` (public) created and `main` pushed.
- LEARNING.md covers every command and console step so far.

Next session, in order:
1. Check the Lambda quota request: `aws service-quotas list-requested-service-quota-change-history --service-code lambda --region ca-central-1 --profile nightshift-admin`. Record the result.
2. Confirm the owner revoked the old GitHub token that was in `~/.git-credentials` and turned on "Block command line pushes that expose my email".
3. M0 wrap-up: summary, how to verify, cost impact ($0), M0 concepts plus 5 interview questions in LEARNING.md, commit and push.
4. Owner review of M0, then propose the M1 plan (Terraform skeleton, remote state, GitHub Actions OIDC, hello-world Lambda through an alias). Wait for approval before building.

Later (tracked, not blocking): test that a budget email actually arrives before the Feb 2027 upgrade (COST.md upgrade plan).

## Decisions log
(Record each decision here with a one-line reason as it is made. "Proposed" means not yet approved.)
- Approved 2026-09-18: Free plan via the standard sign-up flow; upgrade in February 2027 (hard deadline 2027-02-15). Reason: no charges possible while learning; Always Free applies on both plans. Reminder plan in COST.md.
- Approved 2026-09-18: region ca-central-1. Reason: DSQL available since 2026-02-11, closest region, all core services present.
- Approved 2026-09-18: dev access is an IAM user with MFA plus `aws login`. Reason: Identity Center needs Organizations, which would force the Paid plan.
- Approved 2026-09-18: COST.md.
- Approved 2026-09-18: repo in the WSL filesystem at `~/code/NightShift`, Claude Code run from inside WSL. Reason: one environment for hooks and tools, and Linux tools on `/mnt/c` are slow.
- Approved: alert email youssef.m.khafagy+nightshift@gmail.com; public GitHub repo under Youssef-Khafagy; $0.01 tripwire budget.
- Approved 2026-09-19: LEARNING.md documents every command run and console step, not only code. Reason: owner must be able to explain all of it.
- Approved 2026-09-19: budgets created via CLI from JSON in `bootstrap/budgets/` (Terraform starts in M1); Lambda concurrency increase to 1000 requested; `~/.aws` moved off the Windows drive. Reason: cost alarms before any resources, reserved concurrency needs >= 100 unreserved, refresh token must not sit on a 777 shared drive.
- Approved 2026-09-19: no AI attribution in commits, PRs, or docs; owner is sole author via GitHub noreply email. Reason: owner's decision; noreply keeps the personal email out of public history.
- Approved 2026-09-19: AWS account ID replaced with `<ACCOUNT_ID>` in docs and guarded by a pre-commit hook. Reason: public repo; IDs are not secret but give attackers a target.
- Approved 2026-09-19: git credentials via `gh auth setup-git`, plain-text `store` helper removed. Reason: no plain-text tokens on disk.
