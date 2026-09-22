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

## Verifying permissions and other cached decisions
- Never conclude that a permission, grant or policy is unnecessary because removing it did not break anything within a minute. Lambda and IAM cache authorization decisions in both directions: an allow keeps working after the grant is deleted, and a deny keeps failing after the grant is added. A fast negative result is not evidence.
- Verify with `aws iam simulate-principal-policy` (pass `--resource-policy` and any needed `--context-entries`), or by polling for longer than the cache TTL, at least several minutes. Prefer the simulator, because it answers immediately and does not depend on guessing a TTL.
- The same caution applies to any "I removed X and it still works" conclusion about IAM, resource policies, or service-linked roles.
- When a result is intermittent, stop changing things. Alternating success and failure means requests are landing on different execution environments or different cached decisions, and every change made during that window will look like it worked or failed at random.

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
- Every function imports its dependencies and builds its AWS clients and DB connections at module scope, never lazily inside the handler. Measured 2026-09-20: the same imports cost 11,910 ms inside the handler and 712 ms at init, both at 128 MB, because Lambda gives init more CPU. This keeps every function at 128 MB.
- Powertools (Logger, Metrics) for JSON logs and metrics. Not Tracer: it wraps the X-Ray SDK, unsupported from 2027-02-25. Correlation ID propagated end to end, including SQS message attributes.
- orders-service: Aurora DSQL (Postgres-compatible), IAM auth tokens, no VPC. Checkout is one transaction (validate cart, decrement inventory, write order and items), requires an idempotency key, retries SQLSTATE 40001 with exponential backoff and jitter, logs every retry. Design around DSQL unsupported features (check docs).
- cart-service: DynamoDB, provisioned capacity.
- fulfillment-worker: consumes placed-orders SQS queue, calls payment-provider, marks orders paid. DLQ with maxReceiveCount. Partial batch failure reporting.
- payment-provider: mock third-party API Lambda with configurable latency and errors.
- Every function has published versions and a `live` alias. All traffic goes through the alias.
- Deployments table (DynamoDB): service, previous version, new version, git SHA, timestamp, actor.
- Operational feature flags in SSM, cached about 30 s per Lambda: `payments_degraded_mode` (fulfillment defers payment; `scripts/replay_placed_orders.py` recovers), `checkout_rate_limit` (per-environment token bucket, 429 when exceeded). Built in M2b step 2.
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
- Main: terraform plan runs automatically on PRs. Apply is a separate `workflow_dispatch` job the owner triggers by hand, which is the approval gate while the repo is private (GitHub Free cannot create environments on private repos). Apply then publishes Lambda versions, shifts aliases, and records the deployment. If the repo goes public, switch the gate to a GitHub environment with required reviewers.

## Milestones (stop for owner review after each)
- M0: account safety and cost plan (budgets, MFA, dev credentials, region, Lambda concurrency quota, COST.md, repo scaffold, pre-commit). Create nothing in AWS beyond budgets until COST.md is approved.
- M1: Terraform skeleton, GitHub Actions OIDC pipeline, hello-world Lambda through an alias.
- M2a: packaging (shared deps layer), DSQL cluster, schema and migrations, DynamoDB cart table, SQS with DLQ, the four services working end to end.
- M2b: SSM flags, correlation ID propagation, EMF metrics, tracing, topology parameter, rate-capped traffic generator, DPU cost-check script.
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
- New accounts can have Lambda concurrency 10; reserved concurrency needs at least 100 unreserved. Ours was raised to 1000 on 2026-09-19.
- GitHub environments do not exist on GitHub Free for private repos ("Users with GitHub Free plans can only configure environments for public repositories"), and required reviewers on a private repo need Enterprise even on Pro or Team. They are free on public repos; if the repo is made public, do not enable "prevent self-review" (single maintainer).
- GitHub Actions on a private repo draws on the Free plan allowance of 2,000 minutes and 500 MB of artifact storage per month. Public repos get standard runners free with no minute cap. Both are $0; the private one has a ceiling to watch.
- Groq free models allow 8K TPM and 200K TPD per model; Gemini per-model limits are only visible in AI Studio.
- GitHub repos created after 2026-07-15 use immutable OIDC subject claims: `repo:OWNER@OWNER-ID/REPO@REPO-ID:ref:refs/heads/BRANCH`. Ours was created 2026-09-19, so the old `repo:OWNER/REPO:...` form does not validate. Owner ID 232406487, repo ID 1376738088.
- IAM OIDC providers no longer need a thumbprint. AWS verifies GitHub's endpoint against its trusted root CAs and retrieves a thumbprint itself when none is given. Pinning one breaks deploys on certificate rotation.
- Terraform S3 backend locking is `use_lockfile = true` (native, conditional writes) since Terraform 1.10. `dynamodb_table` is deprecated and will be removed.
- Newest GA Lambda Python runtime is `python3.14` (3.15 is public preview, not for production). Functions run on arm64: cheaper per GB-second, same free allowance.
- Lambda `logging_config { log_format = "JSON" }` gives structured logs with no library. Fields passed via `logger.info(..., extra={...})` land at the top level.
- Aurora DSQL free tier (checked 2026-09-20): first 100,000 DPUs and 1 GB storage per month, then $8 per million DPUs and $0.33 per GB-month. Idle clusters scale to zero and incur no DPU charge. AWS benchmarks 100K DPU at roughly 700,000 TPC-C transactions.
- DSQL hard limits (checked 2026-09-20): Repeatable Read only; 3,000 rows and 10 MiB modified per transaction; 5 minute maximum transaction; 60 minute maximum connection; 10,000 connections and 100 new connections/second per cluster; 24 indexes, 255 columns and 2 MiB rows per table; 10 schemas and 1,000 tables per database; one database named `postgres`; C collation; UTC.
- DSQL compatibility (checked 2026-09-20): DDL and DML require separate transactions and one DDL per transaction; no `TRUNCATE` (use `DELETE`), no temp tables (use CTEs), no triggers, no PL/pgSQL (SQL functions only). Foreign keys, sequences (max 5,000) and views are supported. Use `CREATE INDEX ASYNC`.
- DSQL auth: boto3 `dsql` client, `generate_db_connect_auth_token` for a custom database role and `generate_db_connect_admin_auth_token` for admin. Token expires in 15 minutes by default; an established connection survives token expiry. SSL required.
- Powertools Tracer is a thin wrapper over the AWS X-Ray SDK and the Powertools docs say it was chosen over ADOT for cold start. Since the X-Ray SDK is unsupported from 2027-02-25, this project uses Powertools for Logger and Metrics only.
- Lambda tracing options: active tracing alone gives 2 segments per trace with no SDK, at a fixed sampling rate of 1 request/second plus 5% of the rest, which cannot be configured. AWS also documents a manual OpenTelemetry path with a Simple Span Processor, an X-Ray UDP span exporter and an X-Ray Lambda propagator, requiring no collector layer.
- An idle SQS-triggered Lambda long-polls at roughly 648K requests/month per queue, about two thirds of the 1M free allowance, for zero work.
- botocore's retry config: `max_attempts` is the number of retries, `total_max_attempts` is the number of calls. `{"max_attempts": 2}` makes three calls.
- Lambda logs appear to count against the 5 GB CloudWatch Logs free tier (September 2026 bill, checked 2026-09-22; docs are silent since the May 2025 vended-logs pricing). **Evidence, not settled:** the bill quantities were 0 GB, so rounding could hide a vended logs line, and the Free plan bill may present usage differently after the upgrade. Re-check after the first traffic generator run and on the first Paid-plan bill. EMF metrics are ordinary custom metrics plus log bytes, and are not extracted in the Infrequent Access log class. `aws freetier get-free-tier-usage` is free; the Cost Explorer API is $0.01 per request.

## Current status (2026-09-22)
M0 and M1 complete. **M2a complete and approved by the owner. M2b steps 1 to 7 done (2026-09-22); first-half review passed. Owner decided INFO platform logging and reserved concurrency 5 for orders and cart (branch `m2b-logging-concurrency`, not yet deployed). Next is step 8 (tracing, behind the 300 ms gate).** Everything below is verified.

**Shut down cleanly. Nothing is polling or scheduled:**
- The only event source mapping is `nightshift-fulfillment` on `nightshift-placed-orders`, state `Disabled`. It has been enabled for measurements and checks and disabled again each time, always through Terraform rather than the CLI, so state never drifted. Last cycle: the step 2 queue-path check on 2026-09-22.
- Both flags at their defaults: `checkout_rate_limit` = `0`, `payments_degraded_mode` = `false`.
- No EventBridge rules exist. No provisioned concurrency on any function. Every function has reserved concurrency 2.
- Both queues are empty, DLQ included. DSQL holds 160 paid orders (56 from load run `363e7f2a`) and nothing in `placed`; stock is too low for a full 20-minute incident; `scripts/load.py`'s dry run prints the exact restock command, far under the 1 GB free storage, and an idle cluster costs nothing.
- Both budgets still armed with `IncludeCredit=false`. Month-to-date spend $0.00, confirmed with Cost Explorer.
- `terraform plan` clean after the step 6 live run (consumer enabled and disabled through Terraform). PRs #11 to #19 are merged; the step 6 write-up PR is open.
- Custom metrics in the account: 3 (`list-metrics`), all in `NightShift`.
- The 2026-09-22 session spent **7.19 DPU** (TotalDPU for the day): three smoke tests, the step 2 to 4 live checks and a few short reads.

Done:
- AWS account: Free plan, ACTIVE, $100 credits, ends 2027-03-18. Root has MFA and no access keys. Daily identity is IAM user `youssef-admin` (MFA, no access keys, permissions only via group `nightshift-admins` with AdministratorAccess). CLI auth via `aws login --profile nightshift-admin`.
- `~/.aws` is a real Linux directory (mode 700). It used to be a symlink to the Windows drive; the Windows copy of the login cache was deleted.
- Budgets `nightshift-monthly-1usd` ($1, ACTUAL and FORECASTED at 100%) and `nightshift-tripwire` ($0.01, ACTUAL at 100%), both IncludeCredit=false and IncludeRefund=false. Definitions in `bootstrap/budgets/`.
- Lambda concurrency in ca-central-1 is **1000**. The increase from 10 was requested 2026-09-19 (request id 968450523f244f2b9ed86e0b00f4e5ffhHKUEVOy) and approved about 46 minutes later; status CASE_CLOSED, applied value verified with `get-service-quota`. Reserved concurrency is now usable (it needs at least 100 unreserved).
- Repo scaffold: .gitignore, .gitattributes (LF), .env.example, README stub, pre-commit (pre-commit-hooks, gitleaks, ruff, no-aws-account-id, terraform fmt). gitleaks and the account ID hook both verified to block test input.
- Git: repo-local author is the owner's noreply address. Global credential helper switched from plain-text `store` to `gh auth git-credential`; `~/.git-credentials` deleted.
- GitHub repo `Youssef-Khafagy/NightShift` created **private** and `main` pushed. It goes public later (M8 at the latest), and the docs are already written as if public: no account ID, noreply commit email, no secrets.
- LEARNING.md covers every command and console step so far.

Also done 2026-09-20: owner revoked the old GitHub token that was in `~/.git-credentials` and enabled "Block command line pushes that expose my email". M0 concepts and 5 interview questions written in LEARNING.md.

M1, done and verified 2026-09-20:
- State bucket `nightshift-tfstate-ca-central-1-2f0ad894` created by `bootstrap/state/create-state-bucket.sh` (versioned, public access blocked, SSE-S3, ACLs disabled, TLS-only policy, 30-day noncurrent expiry, 7-day incomplete-MPU abort). Not managed by Terraform, on purpose.
- Terraform 1.16.3, AWS provider 6.65, backend S3 with `use_lockfile = true`. Root config in `terraform/`, one module in `terraform/modules/lambda_service/`.
- GitHub OIDC provider plus roles `nightshift-ci-plan` (ReadOnlyAccess, assumable from pull requests and main) and `nightshift-ci-apply` (hand-scoped, main only, explicit denies on the CI roles, the OIDC provider, IAM user and key creation, Organizations, budgets, and the state bucket).
- `nightshift-hello` Lambda: python3.14, arm64, reserved concurrency 2, published version 1, `live` alias, function URL with AWS_IAM auth, log group with 3-day retention created by Terraform.
- Workflows `.github/workflows/ci.yml` (pre-commit, validate, tflint, trivy config, plan with `-lock=false`, PR comment shows counts only) and `apply.yml` (`workflow_dispatch` on main, confirmation word `apply`, saved plan file).
- Repo secret `AWS_ACCOUNT_ID` set, so workflows build role ARNs and GitHub masks the ID in logs.
- Verified: alias invoke returned ExecutedVersion 2; function URL 403 unsigned and 200 SigV4-signed; log retention 3; JSON log lines carry `correlation_id` and `function_version`; state object present in S3; `terraform plan` clean on both the laptop and the runner.
- First apply was local by the owner, because CI cannot create the roles CI needs. PR #1 exercised the plan job; `apply.yml` then deployed version 2 and moved the `live` alias. A run with the wrong confirmation word failed before checkout and before credentials were requested.
- Two bugs the pipeline caught, both fixed:
  1. `archive_file` with `source_dir` zipped `src/hello/__pycache__` from a local smoke test, so the artifact differed between laptop and runner and a stray `.pyc` shipped inside version 1. The module now builds the zip from an explicit `fileset` allowlist of `**/*.py` plus an `extra_files` variable. Ruled out as causes: file mtime (archive_file normalizes it) and `output_file_mode` (a no-op with content-based sources).
  2. The apply role granted CloudWatch Logs actions only on `log-group:NAME:*`. Actions on the group itself (`ListTagsForResource`, `PutRetentionPolicy`) need the bare `log-group:NAME` form. Both are now listed. This fix had to be applied locally with `-target`, because the apply role is denied `iam:PutRolePolicy` on itself, which is the deny working as designed.

M1 was reviewed and approved by the owner on 2026-09-20.

M2a in progress (owner approved the plan 2026-09-20). Steps:
1. **Done.** COST.md carries the re-verified DSQL, Lambda and X-Ray numbers, a DSQL cost model section, and the DynamoDB capacity ledger (16 of 25 RCU/WCU allocated through M5). Two corrections recorded: Lambda's X-Ray sampling rate is fixed and unconfigurable, and the Lambda free tier is identical for x86 and arm64 so arm64 does not stretch it.
2. **Done.** `scripts/lambda_deps.py` (`lock` and `build`) plus `requirements/lambda-deps.in` and `lambda-deps.lock`. Shared deps layer: aws-lambda-powertools 3.35.0 and psycopg[binary] 3.3.6, 28.2 MiB unpacked and 7.7 MiB zipped, sha256 `f9bd9f58...`. Cross-built for cp314 aarch64 with `--only-binary=:all:` and both `manylinux_2_28_aarch64` and `manylinux2014_aarch64` platform tags. Deterministic zip verified by rebuilding, and `--require-hashes` verified by tampering with a hash and watching the build fail. No AWS resources created yet.
3. **Done.** `lambda_service` gained `layer_arns`, a validated `tracing_mode` and a scoped X-Ray policy created only when tracing is Active. `aws_lambda_layer_version.deps` published (version 1). Both workflows build the layer before `terraform init`. CI apply role gained layer permissions on both ARN shapes, applied locally with `-target`. Reproducibility took three attempts: the runner disagreed because pip's generated console script embeds the building interpreter's shebang and `jmespath`'s RECORD carried its hash, so the build now drops RECORD entries pointing outside the layer. Runner and laptop agree on content and zip digests. `hello` reports the layer: powertools 3.35.0, psycopg 3.3.6 with `binary, libpq 180006`, runtime boto3 1.42.97 with the dsql client and both token methods, so the layer does not need boto3. Cold-start imports moved to module scope: 11,910 ms to 712 ms at 128 MB.
4. **Done.** Aurora DSQL cluster (AWS owned KMS key, deletion protection off), `nightshift-cart` DynamoDB table (PROVISIONED 5/5, hash key `cart_id`, TTL on `expires_at`, PITR off), `nightshift-placed-orders` queue (visibility 180 s, redrive to DLQ at `maxReceiveCount` 3) and its DLQ (14 day retention, redrive allow policy limited to the one source queue). Both queues use SSE-SQS, added because the trivy scan failed on AWS-0096. Read the cluster identifier and endpoint with `terraform output`; they are not committed. The CI apply role gained DSQL, DynamoDB and SQS permissions plus `iam:CreateServiceLinkedRole` scoped to the `/aws-service-role/` path with an `iam:AWSServiceName` condition of `dsql.amazonaws.com`, because creating the first cluster also creates `AWSServiceRoleForAuroraDsql`. Verified: cluster ACTIVE, constructed endpoint `<identifier>.dsql.<region>.on.aws` resolves (IPv6) and accepts TCP on 5432, table ACTIVE with TTL enabled, queue attributes as configured. The event source mapping arrives disabled with the worker in step 6.
5. **Done.** `src/common/dsql.py` (IAM auth token, TLS with an explicitly named CA bundle, `retry_on_conflict` for SQLSTATE 40001 with full jitter) and `scripts/migrate.py` (dry run by default, `--apply`, `--status`). Five migrations applied: `products`, `inventory`, `orders`, `order_items`, `idempotency_keys`, plus `schema_migrations`. Four foreign keys verified. Re-running is a no-op. Guards tested by tripping them: editing an applied migration is refused, and a file with two statements is refused. Runtime boto3 already confirmed in step 3, so no DSQL blocker remained.
   - Local dev needs `requirements/dev.txt` in `.venv`, including `botocore[crt]`, which `aws login` credentials require and plain boto3 lacks. Lambda does not need it.
   - `sslrootcert="system"` fails with the psycopg binary wheel because it ships its own OpenSSL; the helper names the bundle path instead.
   - DSQL forbids DDL and DML in one transaction, so a migration cannot be recorded atomically with its own application. Migrations are therefore idempotent, one statement per file, and checksummed.
6. **6a done.** cart-service (DynamoDB, function URL) and orders-service (checkout, DSQL, SQS). Shared code in `src/common` is zipped into each artifact under `common/` by the module's `shared_source_dir`. Database role `orders_service` created by `scripts/grant_db_roles.py` with SELECT/UPDATE only on the five tables it touches, never `admin`. Verified end to end: 201 with the correct total, replayed idempotency key returns the original order with 200, missing key returns 400, stock decremented exactly, SQS message carries the `correlationId` attribute.
   - **Internal calls use the Lambda Invoke API, not function URLs.** A request signed by an IAM role was rejected at a function URL with 403 while the identical request signed by an IAM user succeeded; this happened for both the orders execution role and the GitHub Actions role and was never root-caused. Function URLs remain the external entry point. `service_client.call` sends a function-URL-shaped event so callees keep one handler, and the caller sets a read timeout so a slow dependency surfaces as a timeout.
   - `GRANT USAGE ON SCHEMA public` fails on DSQL with FeatureNotSupported; `public` is a system entity and PostgreSQL grants USAGE on it to PUBLIC anyway.
   - The apply workflow ends with `scripts/smoke_checkout.py` and fails the job if checkout does not return 201.
6b. **Done.** payment-provider (latency and error rate from env config, no function URL) and fulfillment-worker (SQS consumer, partial batch failure reporting, no function URL). Event source mapping ships `enabled = false`; set `-var="queue_consumer_enabled=true"` for a run. Database role `fulfillment_service` has SELECT and UPDATE on `orders` only. Verified: enabling the mapping drained 19 queued orders from `placed` to `paid` in under 15 seconds, disabling returned it to `Disabled` with a clean plan, and a synthetic batch of three with one unparseable body returned exactly `{"batchItemFailures": [{"itemIdentifier": "bad-1"}]}`.
   - The CI apply role needed `lambda:TagResource` on `arn:...:event-source-mapping:*` separately, because `default_tags` tags the mapping and that is a different resource type. Its create/update/delete actions take an `ArnLike` condition on `lambda:FunctionArn`; `TagResource` does not, because that key is not in its request context.
7. **Done 2026-09-21.** Measure DPU per checkout and replace the estimate in COST.md before any load test.
   - Why it matters: COST.md's DSQL projection (~20K of 100,000 DPUs per busy month) is an estimate, explicitly marked weak. The 42-incident benchmark plan depends on it, and DSQL is not covered by AWS free tier usage alerts, so nothing will warn us if it is wrong.
   - How: read the cluster's DPU metric with `GetMetricStatistics` (never `GetMetricData`, which is always billed) over a window with a known number of checkouts, and divide. `scripts/smoke_checkout.py` is a convenient known-cost unit of work.
   - Then rebuild the benchmark projection in COST.md from the measured number, and record whether retries and unindexed reads move it materially.
   - Also still open from step 3: whether Lambda bills init duration for on-demand invocations. COST.md assumes it does, which is the conservative reading.
   - **Done 2026-09-21, before measuring:** reading the cluster's existing DPU history first turned up six minutes of unexplained compute. `ComputeDPU` is `ComputeTime` divided by 1000, and a controlled experiment (7 transactions committed at once against 1 held open 60 s, identical query work) measured **one DPU per transaction-second, within 0.1%**. The free allowance is therefore about 27.8 hours of open transaction time per month, not 100,000 units of work. That exposed four leaked transactions in our own code (`autocommit=False` plus paths that never committed), which had cost 1,590 DPU with no functional symptom at all. Fixed; see COST.md and LEARNING.md.
   - **Measured 2026-09-21 on the deployed fix**, from batches of 5, 25 and 50 against an idle cluster with a 0.000 baseline: **0.1366 DPU per checkout** marginal plus 0.425 DPU fixed per batch. Raw data in `results/dpu-per-checkout.json` and `results/dpu-fulfilment.json`.
   - **Fulfilment measured the same day** by enabling the consumer, draining 31 orders and then 50, and disabling it again: **0.0768 DPU per order**, with no measurable fixed cost. Both drain sizes agreed to four decimal places.
   - Full order lifecycle **0.2134 DPU**. Benchmark projection rebuilt at **~21,500 DPU per pass, ~78% headroom**, with no estimated components left.
   - The third batch size earned its place: it moved the marginal cost up 15% from the two-point answer, in the direction that matters, since that term is multiplied by 100,800. Largest residual is 3.1%.
   - **Write DPU is quantised in units of 0.05**, so a tiny write costs the same as a slightly larger one. Argues for fewer, fuller write transactions.
   - The fix was confirmed in billing data, not only in tests: the apply run's smoke test cost 1.12 DPU across 13 transactions with no delayed spike, against roughly 316 DPU for the same test before the fix.
8. **Done.** M2a reviewed and approved by the owner on 2026-09-21. M2b proposed and approved the same evening.

## M2b plan (approved 2026-09-21, in progress)

**Steps 1 to 7 done 2026-09-22; next is step 8.** Reviewed in two halves: once after step 3, once at the end.

Why M2b exists: M5's agent can only diagnose what the system reveals. Every read-only tool it has (`get_metrics`, `query_logs`, `get_flag_values`, `get_topology`) is backed by something built here. Getting this wrong makes M5 look like a model problem when it is a visibility problem.

1. **Done 2026-09-22.** Custom metric ledger and a measured Logs budget are in COST.md. Lambda logs appear to count against the 5 GB Logs free tier (September bill: a free tier `PutLogEvents` line, no vended logs line, but at 0 GB quantities, so evidence rather than proof; re-check at higher volume and after the upgrade). Ingestion is ~1.8 KB per order, so scans are the Logs budget: M5 gets a 25 MB scan cap per investigation. Log groups must stay Standard class (Infrequent Access drops EMF). Original plan: **Verify and budget. No AWS changes.** Re-verify on official pages: SSM Parameter Store standard tier (free, 4 KB value limit, standard throughput not billable); the exact definition of a billable custom metric and that the 10 free are per account per region; that EMF metric extraction bills ingestion against the 5 GB Logs allowance rather than separately; X-Ray's current free tier for the step 8 gate. Then write a custom-metric ledger into COST.md in the same shape as the DynamoDB capacity ledger.
   - **The EMF question is the one to confirm most carefully.** If EMF ingestion bills differently than assumed, the metric budget still holds but the Logs budget may not: five metrics at 2 requests/s across a 42-incident benchmark is a lot of log lines, and Logs is the allowance with the least headroom at about 30%.
2. **Done 2026-09-22, verified live.** Flag behaviour chosen by the owner: `payments_degraded_mode=true` makes fulfillment-worker skip the payment provider, leave the order in `placed` and acknowledge the message; `scripts/replay_placed_orders.py` republishes such orders once the flag is cleared (dry run by default; refuses while the flag is on, while the queue is non-empty, and for orders under 10 minutes old, all to avoid double charging). `checkout_rate_limit=N` is a token bucket per execution environment, checked before the cart and the database, returning 429 with `retry-after: 1`; 0 is off; real ceiling 2N at reserved concurrency 2. Terraform owns the parameters (Standard tier, `allowed_pattern`) but not their values (`ignore_changes = [value]`). Each function can `ssm:GetParameter` only its own flag.
   - Live results: rate limit took effect 26.5 s after the write and was lifted 16.6 s after reset, both inside the 30 s TTL. Degraded mode deferred a queued message (no payment call, queue emptied, order stayed `placed`); the replay then got it to `paid` with the payment provider called exactly once.
   - Two bugs caught before deploy: botocore's `max_attempts` counts retries, not attempts (now `total_max_attempts`); and IAM built from `aws_ssm_parameter.*.arn` broke the module's `count` at plan time (now built from names, like `cart_function_arn`).
   - Original plan: **SSM flags.** Two String parameters, `payments_degraded_mode` and `checkout_rate_limit`. `src/common/flags.py` reads them with a module-scope cache and a 30 s TTL, failing open to a default if SSM errors. IAM scoped per function to its own parameter path. Note the Lambda subtlety: a frozen container can serve a stale flag for a full TTL after it thaws. Verify by flipping a flag and watching behaviour change within 30 s.
3. **Done 2026-09-22, verified live.** `get_metrics()` in `common/context.py`; orders emits `CheckoutsPlaced`, `CheckoutsRejected`, `SerializationRetries`; fulfillment emits `OrdersPaid`, `PaymentFailures` (not `SerializationRetries`, which is budgeted for orders only). `tests/test_metrics.py` parses the COST.md ledger and fails on any unbudgeted metric or dimension, on `ColdStart`, on a budgeted metric nothing emits, or on an unparseable ledger; verified by planting violations.
   - Live: EMF lines arrive unwrapped under Lambda JSON log format; 204 to 214 bytes each (estimate was 400). `list-metrics` shows exactly 3 so far (`CheckoutsPlaced`, `CheckoutsRejected`, `OrdersPaid`), one dimension each; the other two appear on the first retry or payment failure. Logs budget now 3.60 GB of 5.
   - **Open for M3:** orders returns 502 on a cart-service failure without raising, so `AWS/Lambda Errors` for orders stays 0 and an alarm on it would miss a cart outage.
   - Original plan: **EMF metrics, budgeted.** Namespace `NightShift`, `service` as the only dimension, Powertools cold-start metric **off** (it would cost one custom metric per service). Tested the way the transaction leak is tested: assert the emitted EMF document's dimension set, so adding a dimension fails CI instead of quietly costing money.
   - **Owner review point.** Steps 2 and 3 are the ones with irreversible cost consequences.
4. **Done 2026-09-22, verified live.** `scripts/trace_correlation.py` places one order with a fresh ID and rebuilds the chain with Logs Insights from the ID alone (`cart stored`, `cart read`, `checkout complete`, `charge approved`, `order paid`), plus a second query by order ID requiring every line to carry the same ID. Pass/fail is two pure functions tested with broken chains. Live: complete chain from `trace-39e86b797f4f`, 3 of 3 order lines consistent, 4,492 bytes scanned (near-idle groups). An order touches four log groups, not five; hello takes no part. Needs the consumer enabled.
   - Original plan: **Correlation ID, proven end to end.** Mostly already built; this is verification. A script runs one checkout and reconstructs the chain across all five log groups from the ID alone, asserting no break. Bounded time ranges, per the Logs Insights cost rule.
5. **Done 2026-09-22, verified live.** `terraform/topology.tf` writes `/nightshift/topology` (Standard, String): services with function, alias, log group, entry points, calls, stores and flags; the queue's consumer, DLQ and max receive count (from the redrive policy); table billing mode; DSQL cluster ID; metrics namespace. Structure only, never versions, timeouts, memory or capacity, which the agent must read live. Precondition fails the plan above 4096 characters or on non-ASCII; both halves verified by breaking them. Live value is byte-identical to `local.topology_json`, 1,173 bytes. Do not copy it verbatim into M8's public replay files (it holds the cluster ID).
   - Original plan: **Topology parameter.** Terraform writes compact JSON from its outputs to SSM. Hard size guard: fail if it exceeds 4 KB rather than silently needing the paid advanced tier.
6. **Done 2026-09-22, verified live.** `scripts/load.py`: dry run by default; plans carts from a seed; projects Lambda, SQS, DPU and logs; refuses above 5/s, 30 min, 3,600 orders, half the monthly DSQL or Lambda allowance (read live), insufficient stock (one restock command), or a disabled consumer without `--checkout-only`. Open loop, 8 in flight, drops counted. Live run `363e7f2a` (60 orders at 1/s, `results/load-live-check-2026-09-22.json`): 56 placed, 4 Lambda throttles, all 56 paid. Measured 4.64 invocations, about 2.2 SQS requests plus ~20 idle polls/min, and 0.2455 DPU per order; generator constants now 4.7, 2.2 + 20/min, 0.25.
   - **Open decisions:** (1) `system_log_level = "WARN"` in the Lambda module drops all platform lines (START, REPORT, init), so cold starts and durations are only in `AWS/Lambda` metrics; INFO would roughly double log volume per order. (2) The 4 throttles were all in the first minute, with orders and cart at their reserved concurrency of 2 beside one 3.1 s orders invocation; likely a new environment opening its first DSQL connection inside a request (unconfirmed, see (1)). Options: raise reserved concurrency for orders and cart, or rely on the harness warm-up. Every incident needs a warm-up either way.
   - Original plan: **Traffic generator.** Rate-capped, `--dry-run` by default, printing projected Lambda invocations, SQS requests and DSQL DPU before it runs, and refusing above a budget. Uses the measured 0.2134 DPU per order: a 2 requests/s, 20 minute incident is 2,400 orders, about 512 DPU, 0.5% of the month.
7. **Done 2026-09-22, run live.** `scripts/cost_check.py`: billing view from the Free Tier API plus a live month-to-date view from `GetMetricStatistics` (DSQL DPU, Lambda invocations, SQS upper bound, Logs bytes), custom metric and alarm metric counts; OK/WATCH/ALERT at 50%/85%, exit 1 on ALERT; lists services not in the expected set. First run: 18 OK. Custom metrics 4 (`SerializationRetries` appeared from one conflict in the step 6 run). **Open: 10 AWS Glue catalog requests this month; the project uses no Glue; cause unknown** (check CloudTrail `LookupEvents`). `measure_dpu.py` stays separate: it measures experiments, not month-to-date totals.
   - Original plan: **Cost-check script.** `scripts/cost_check.py`: DSQL DPU month-to-date, SQS requests, Logs bytes ingested, and a count of live custom metrics, for COST.md's monthly ritual. `measure_dpu.py` folds into it.
8. **Tracing, last, behind a go/no-go gate.** Add OpenTelemetry with the X-Ray UDP exporter, then measure init duration. **If it costs more than 300 ms on top of the current 712 ms, stop**, remove `get_traces` from the agent's toolset, and write an ADR explaining the call.
   - Reasoning for the gate: the X-Ray SDK is dying so this is a hand-built OTel path, sparsely documented for python3.14 on arm64, feeding 128 MB functions where init cost already forced a design decision. Against that, no scenario among the fourteen is currently known to need traces that correlated structured logs do not cover. A clean documented "we measured it and dropped it" beats a half-working tracer.

### The M2b metric budget

Every M3 alarm (error rate, checkout p99, queue age, DLQ depth, throttles) is built from metrics AWS publishes for free. The custom budget is spent only on business facts AWS cannot see.

| Service | Metric | Custom metrics |
|---|---|---|
| orders | `CheckoutsPlaced` | 1 |
| orders | `CheckoutsRejected` | 1 |
| orders | `SerializationRetries` | 1 |
| fulfillment | `OrdersPaid` | 1 |
| fulfillment | `PaymentFailures` | 1 |
| | **Total** | **5 of 10** |

The rule that keeps it there: separate metric names, never variable dimensions. `CheckoutsRejected`, not `CheckoutOutcome{outcome=rejected}`. The reason for a rejection goes in the log line, where the agent finds it with a bounded query.

Later (tracked, not blocking): test that a budget email actually arrives before the Feb 2027 upgrade (COST.md upgrade plan).

## Decisions log
(Record each decision here with a one-line reason as it is made. "Proposed" means not yet approved.)
- Approved 2026-09-18: Free plan via the standard sign-up flow; upgrade in February 2027 (hard deadline 2027-02-15). Reason: no charges possible while learning; Always Free applies on both plans. Reminder plan in COST.md.
- Approved 2026-09-18: region ca-central-1. Reason: DSQL available since 2026-02-11, closest region, all core services present.
- Approved 2026-09-18: dev access is an IAM user with MFA plus `aws login`. Reason: Identity Center needs Organizations, which would force the Paid plan.
- Approved 2026-09-18: COST.md.
- Approved 2026-09-18: repo in the WSL filesystem at `~/code/NightShift`, Claude Code run from inside WSL. Reason: one environment for hooks and tools, and Linux tools on `/mnt/c` are slow.
- Approved: alert email youssef.m.khafagy+nightshift@gmail.com; GitHub repo under Youssef-Khafagy; $0.01 tripwire budget.
- Approved 2026-09-19: LEARNING.md documents every command run and console step, not only code. Reason: owner must be able to explain all of it.
- Approved 2026-09-19: budgets created via CLI from JSON in `bootstrap/budgets/` (Terraform starts in M1); Lambda concurrency increase to 1000 requested; `~/.aws` moved off the Windows drive. Reason: cost alarms before any resources, reserved concurrency needs >= 100 unreserved, refresh token must not sit on a 777 shared drive.
- Approved 2026-09-19: no AI attribution in commits, PRs, or docs; owner is sole author via GitHub noreply email. Reason: owner's decision; noreply keeps the personal email out of public history.
- Approved 2026-09-19: AWS account ID replaced with `<ACCOUNT_ID>` in docs and guarded by a pre-commit hook. Reason: the repo goes public eventually; IDs are not secret but give attackers a target, and history is permanent once pushed.
- Approved 2026-09-19: git credentials via `gh auth setup-git`, plain-text `store` helper removed. Reason: no plain-text tokens on disk.
- Approved 2026-09-20: the GitHub repo stays private for now and goes public by M8. Reason: owner is not ready to show the work; everything is still written as if public so nothing has to be rewritten later.
- Approved 2026-09-20: the M1 apply gate is a manual `workflow_dispatch` job, not a GitHub environment with required reviewers. Reason: GitHub Free cannot create environments on private repos, and required reviewers on a private repo need Enterprise. Clicking Run workflow is the approval. Revisit when the repo goes public.
- Approved 2026-09-20: Terraform remote state in an S3 bucket in our own account with `use_lockfile = true` (S3 native locking, which replaced the DynamoDB lock table in Terraform 1.10). Reason: realistic cost is about $0.001/month and worst case about $0.02, inside the $0.10 rule, and it keeps everything in one AWS account instead of adding a third-party service.
- Approved 2026-09-20: M1 started (Terraform skeleton, S3 backend bootstrap, GitHub OIDC, hello-world Lambda through a `live` alias).
- Approved 2026-09-20: the Terraform state bucket is created and owned by a bootstrap script, not by Terraform. Reason: Terraform cannot create its own backend, and a bucket it manages could be deleted by `terraform destroy` while state is being written to it.
- Approved 2026-09-20: the CI plan role uses the AWS managed `ReadOnlyAccess` policy while the apply role is scoped by hand. Reason: a plan must read every resource type in the configuration and that set grows each milestone; a role that cannot write cannot break anything, and all data in the account is synthetic.
- Approved 2026-09-20: the AWS account ID is a GitHub repository secret (`AWS_ACCOUNT_ID`) and workflows build ARNs from it. Reason: keeps the ID out of the repo, and GitHub masks secret values in job logs.
- Approved 2026-09-20: M2 is split into M2a (data plane) and M2b (telemetry), each with its own owner review. Reason: M2 as originally scoped is about four times the size of M1, and a wrong turn should cost half a milestone rather than a whole one.
- Approved 2026-09-20: tracing uses OpenTelemetry with the X-Ray UDP span exporter and the X-Ray Lambda propagator, set up manually, with no collector layer. Powertools is used for Logger and Metrics only. Reason: Powertools Tracer wraps the AWS X-Ray SDK, which is unsupported from 2027-02-25, inside this project's life; the ADOT managed layer is heavier and its ARN carries an AWS-owned account ID that the pre-commit hook blocks.
- Approved 2026-09-20: Python dependencies ship in one shared Lambda layer built by a script from a pinned requirements file. Reason: function zips stay small and reviewable, deploys upload only our code, the layer ARN is ours so no foreign account ID enters the repo, and layers are worth knowing.
- Approved 2026-09-21: M2b custom metrics are five separate metric names with `service` as the only dimension, and the Powertools cold-start metric is off. Reason: a custom metric is a name plus a dimension set, so one variable dimension multiplies the bill by its cardinality; separate names keep the count fixed and predictable, reasons belong in log fields, and every M3 alarm can be built from free AWS-published metrics anyway. Leaves 5 of 10 free for M3 to M7.
- Approved 2026-09-21: M2b tracing is built last behind a 300 ms init-duration gate, and dropped with an ADR if it exceeds it. Reason: it is a hand-built OpenTelemetry path on a dying SDK, feeding 128 MB functions where init cost already forced imports to module scope, and no scenario is currently known to need traces that correlated logs do not cover. The risky item must not block the useful ones.
- Approved 2026-09-22: `payments_degraded_mode` defers payment (order stays `placed`, message acknowledged, replay script afterwards) rather than acting as a checkout kill switch or failing fast into the DLQ. Reason: it is the remediation for a slow payment provider, so it must not hurt customers more than the fault does, and it must not muddy the DLQ-depth alarm that scenario 5 relies on. The replay script also covers the existing commit-then-crash gap in orders-service.
- Approved 2026-09-22: `checkout_rate_limit` is a per-execution-environment token bucket, not a global DynamoDB counter. Reason: no shared state, $0, and no new way for checkout to fail; it is load shedding during an incident, and the 2N ceiling is documented.
- Approved 2026-09-22: Terraform manages the flag parameters with `ignore_changes = [value]`. Reason: a routine apply during an incident must never silently revert a flag an operator or the agent has flipped.
- Approved 2026-09-21: M2b is reviewed in two halves, after step 3 and at the end. Reason: the same logic that split M2 into M2a and M2b. Steps 2 and 3 carry the irreversible cost consequences, so a wrong turn there should surface before everything is built on top of it.
- Approved 2026-09-21: DSQL connections default to `autocommit=True`, and code that needs several statements to be atomic asks for a transaction explicitly with `with conn.transaction():`. Reason: DSQL bills compute by how long a transaction stays open (measured, one DPU per transaction-second), and psycopg's normal default leaves a transaction open after any lone statement, which costs up to 315 DPU each time a Lambda freezes afterwards. Four such leaks were already in the code and had no functional symptom.
- Approved 2026-09-20: the SQS event source mapping ships disabled and is enabled explicitly for a run. Reason: an idle triggered queue spends about two thirds of the free SQS allowance doing nothing, and this makes most of M3's pause command already built.
