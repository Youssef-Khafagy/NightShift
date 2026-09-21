# COST.md

Status: APPROVED by owner on 2026-09-18 (Free plan, ca-central-1, IAM user with MFA plus `aws login`). Update it whenever a number is measured or a service is added.
All allowances below were checked on 2026-09-18 against the sources listed at the bottom.
Re-verified 2026-09-20 before starting M2a: Aurora DSQL, Lambda and X-Ray. See "Sources checked 2026-09-20".
"Projected" numbers are estimates from stated assumptions, not measurements. They get replaced with measured numbers in M2 to M4.

## Account plan

| | Free plan | Paid plan |
|---|---|---|
| Credits | $100 at sign-up, up to $100 more for activities | Same |
| Can you be charged? | No. "No charges incur during usage" | Yes, for anything beyond credits and Always Free |
| Always Free offers | Active | Active |
| Lifetime | Ends after 6 months or when credits run out, whichever is first. Account closes; content kept 90 days; upgrade within 90 days to recover | Does not expire |
| AWS Organizations | Joining or creating one auto-upgrades the account to Paid | Available |
| IAM Identity Center | Needs Organizations to grant account access, so not usable without upgrading | Available |
| Other limits | No Savings Plans, Reserved Instances, some Marketplace offers, no other promo credits | None |

Recommendation: sign up with the standard flow on the Free plan, then upgrade deliberately before month 6. See "Upgrade plan" below.

Sign-up flow note: AWS is piloting a second flow, "Sign up for AWS (new)". It picks the region for you (us-east-2 for Canada), has no root user, uses AWS-managed SCPs and Identity Center, and is in limited rollout. Its one advantage is a hard spend limit, but that needs the Paid plan and has a $20 minimum. We use the standard flow ("Sign up for AWS (advanced)" in the docs).

## Region

Proposed: `ca-central-1` (Canada Central). Aurora DSQL single-Region clusters became available there on 2026-02-11. The other services in this file are core services offered in every commercial region, including ca-central-1. Budgets and billing are global, so region does not affect them.

## Always Free services we plan to use

Usage model for "Projected": one busy month = development plus one full benchmark pass. A benchmark pass is 14 scenarios x 3 runs = 42 injected incidents. Traffic is 2 requests/s for 20 minutes per incident. Every agent and baseline configuration investigates the same injected incident, so incidents are not repeated per configuration.

| Service | Always Free allowance | Projected busy month | Headroom | Guardrails |
|---|---|---|---|---|
| Lambda | 1M requests and 400,000 GB-s per month, perpetual, **identical for x86 and arm64** (re-verified 2026-09-20) | ~410K requests, ~60K GB-s | ~59% requests, ~85% GB-s | Share each incident across configs (separate injections per config would be ~1.5M requests, over the limit). Agent never sleeps inside Lambda while waiting on LLM rate limits; it checkpoints and reschedules. **Measured 2026-09-20, resolved:** 128 MB holds. Importing Powertools, psycopg and boto3 and building a DSQL client costs 11,910 ms at 128 MB when done lazily inside the handler, but 712 ms at the same 128 MB when done at module scope during init. Memory was not the lever; where the imports run was. See the memory sweep below. |
| Aurora DSQL | 100,000 DPUs and 1 GB storage per month, Always Free on Free and Paid plans (re-verified 2026-09-20). Beyond: $8 per million DPUs, $0.33 per GB-month | ~20K DPUs, <0.1 GB | ~80% (weak estimate) | DSQL is NOT in AWS's list of services tracked by free tier usage alerts. A local cost-check script reads the DSQL DPU metric with GetMetricStatistics. Measure DPUs per checkout in M2a before any load test. An idle cluster scales to zero and incurs no DPU charge, so leaving the cluster up between sessions is free as long as storage stays under 1 GB. |
| DynamoDB | 25 RCU, 25 WCU (provisioned, Standard table class), 25 GB storage, per region | Planned total <= 20 RCU and 20 WCU across all tables and GSIs | >= 5 RCU/WCU | Provisioned mode only. No auto scaling: target tracking creates CloudWatch alarms that would use our alarm allowance. Capacity ledger kept in this file from M2. |
| SQS | 1M requests per month (each 64 KB chunk is one request, a batch of up to 10 messages is one request) | ~110K with the consumer disabled between runs; ~760K if the trigger is left on 24/7 | ~89% | Idle Lambda trigger polls with 5 long-poll connections. Estimate 5 x 3 per min x 43,200 min = ~648K requests/month per idle queue, for zero work. **Decided 2026-09-20:** the event source mapping ships `enabled = false` and is turned on only for a run. Only one triggered queue; DLQ has no trigger; standard (not provisioned) poller mode. Measure with NumberOfEmptyReceives in M2a. |
| SNS | 1M requests, 1,000 email deliveries per month | ~220 emails | ~78% | Alarm actions only on ALARM transitions we care about. |
| EventBridge | AWS service events (including alarm state changes) on the default bus are free; Scheduler 14M invocations/month | Hundreds | Large | No custom event buses or API destinations. |
| CloudWatch metrics | 10 custom metrics; 1M API requests. GetMetricData, GetInsightRuleReport and GetMetricWidgetImage are ALWAYS charged | <= 6 custom metrics; <100K API requests | ~40% metrics, >90% API | Agent and dashboard use GetMetricStatistics, never GetMetricData. Fixed EMF dimension sets; watch Powertools default dimensions and the cold start metric. |
| CloudWatch alarms | 10 alarm metrics (standard resolution, metrics listed directly) | <= 10 | 0 to 2 | A metric math alarm counts every metric it lists. No composite alarms ($0.50 each), no anomaly detection alarms (count as 3). No CloudWatch billing alarm; Budgets does that job. |
| CloudWatch Logs | 5 GB/month combined: ingestion + archive storage + Logs Insights data scanned | ~3.5 GB | ~30% | Retention 3 days. Short log lines. Insights cost follows bytes scanned in the time range, NOT the result limit, so every query is one log group and a short window. Track scanned bytes per investigation. |
| X-Ray | 100,000 traces recorded and 1M traces retrieved or scanned per month, perpetual (re-verified 2026-09-20) | ~65K recorded | ~35% | **Correction (2026-09-20):** Lambda's sampling rate is fixed at 1 request/second plus 5% of the remainder and **cannot be configured**, so the earlier "explicit sampling rate" guardrail was wrong. The lever is how many requests we send, not what fraction is sampled. X-Ray SDK is in maintenance since 2026-02-25, end of support 2027-02-25; M2b uses OpenTelemetry with the X-Ray UDP span exporter. Never enable Transaction Search or Application Signals (paid span ingestion). |
| CloudTrail | 90-day management event history, viewing and LookupEvents at no charge | Hundreds of lookups | Large | Never create a trail, data events, Lake, or Insights. |
| SSM Parameter Store | Standard parameters and standard throughput: no additional charge | Tens of thousands of reads | Free | Standard tier only. Higher throughput setting stays off (it makes standard parameters billable). |
| KMS | 20,000 requests/month across all regions; AWS managed keys only | ~1K (SecureString decrypts for API keys) | ~95% | Feature flags are plain String, not SecureString. No customer managed keys. |
| AWS Budgets | Monitoring and notifications free; first 2 action-enabled budgets free | 2 budgets, no actions | Free | No budget actions, no budget reports ($0.01 each). |

Not yet re-verified on a pricing page today (expected free, checked before first use): IAM, STS, Service Quotas, Lambda function URLs (billed as normal Lambda requests).

### Aurora DSQL cost model (checked 2026-09-20)

A DPU is not a time unit. AWS counts three things in it: compute used to execute query logic such as joins, functions and aggregations; the I/O to read from and write to storage; and change data capture streaming if enabled. We do not enable CDC.

Consequences for how the store is written:

- Cost follows work done, not wall-clock time, so a cluster sitting idle overnight costs nothing beyond storage. There is no "turn the database off" step in the pause command.
- A checkout that reads the whole inventory table to decrement one row costs far more DPU than one that reads a single row by primary key. Index discipline is a cost control here, not only a latency control.
- Retries are not free. Every attempt of a transaction that hits `40001` and is retried bills its own DPU, so a contention storm costs real DPU. Scenario 7 (hot-row contention) is therefore a cost event as well as a latency event, and its runs must be short.
- Storage is billed at $0.33 per GB-month with 1 GB free, and data is replicated across three Availability Zones at no extra charge. Synthetic order data must be pruned between benchmark passes to stay under 1 GB.

Measurement plan for M2a, before any load test: run one checkout, read the cluster's DPU metric with `GetMetricStatistics`, and record DPU per checkout here. The 42-incident benchmark projection is then rebuilt from that measured number instead of the current estimate.

### Lambda memory and where imports run (measured 2026-09-20)

Cold-start cost of importing Powertools, psycopg and boto3 and constructing a DSQL client, measured on the real runtime through the `nightshift-hello` layer probe.

Lazily, inside the handler:

| Memory | Import time | GB-seconds for that work |
|---|---|---|
| 128 MB | 11,910 ms | 1.49 |
| 512 MB | 2,770 ms | 1.39 |
| 1,024 MB | 1,387 ms | 1.39 |

CPU scales nearly linearly with memory, so the same work costs roughly the same GB-seconds at any size. Paying for more memory buys latency, not cost, for CPU-bound work. The opposite is true for time spent waiting on a database: there, duration does not shrink with memory, so a larger function simply costs more.

At module scope, during the init phase, at 128 MB:

| Step | Lazy at 128 MB | At init, 128 MB |
|---|---|---|
| Powertools | 1,737 ms | 109 ms |
| psycopg | 5,757 ms | 312 ms |
| boto3 | 2,755 ms | 133 ms |
| DSQL client | 1,661 ms | 158 ms |
| **Total** | **11,910 ms** | **712 ms** |

Same memory, 16.7 times faster, because Lambda gives the init phase more CPU than the configured memory would otherwise buy.

**Design rule that follows:** every service imports its dependencies and constructs its AWS clients and database connections at module scope, never on first use inside the handler. Functions stay at 128 MB.

**Not verified:** whether init duration is billed for on-demand invocations. The platform report lines did not surface in time to check. Worth confirming in step 6, since it decides whether the 712 ms is free or counts against the GB-second allowance. The projection above assumes it is billed, which is the conservative reading.

### DynamoDB capacity ledger

The free allowance is 25 RCU and 25 WCU **per region across the whole account**, and every global secondary index consumes its own capacity on top of its table. Provisioned mode only, no auto scaling. Every table added to this project gets a row here before it is created.

| Table | Milestone | RCU | WCU | GSIs | Status |
|---|---|---|---|---|---|
| `nightshift-cart` | M2a | 5 | 5 | none | Planned |
| `nightshift-deployments` | M3 | 1 | 1 | none | Planned |
| `nightshift-investigations` | M5 | 5 | 5 | none | Planned |
| `nightshift-journal` | M5 | 5 | 5 | none | Planned |
| **Allocated** | | **16** | **16** | | |
| **Free allowance** | | **25** | **25** | | |
| **Unallocated** | | **9** | **9** | | |

If a table needs a GSI later, its capacity comes out of the 9 unallocated, not out of thin air.

## Not Always Free

| Item | Cost | Plan |
|---|---|---|
| S3 bucket for Terraform state | No Always Free tier for new accounts. ca-central-1: $0.025/GB-month, $0.0055 per 1,000 PUT/LIST, $0.00044 per 1,000 GET | On the Free plan it is covered by credits, so $0.00. After upgrade: realistic ~$0.001/month (60 runs x ~6 requests, <1 MB state), worst case ~$0.02/month (400 runs, 100 kept versions). Under the $0.10 threshold but not literally $0. **Decided in M1 (2026-09-20): S3 in our own account** with `use_lockfile = true`, versioning, and a lifecycle rule expiring noncurrent versions after 30 days plus aborting incomplete multipart uploads after 7 days. HCP Terraform Free was the alternative; rejected because it adds a third-party account and teaches less AWS. Measured state size after the first apply: 57 KB. |

## Budgets (created 2026-09-19)

1. `nightshift-monthly-1usd`: $1/month cost budget. Email at 100% ACTUAL and 100% FORECASTED.
2. `nightshift-tripwire`: $0.01/month cost budget. Email at 100% ACTUAL.

Both set `IncludeCredit=false` (the API default is true). With credits included, credits would absorb every charge during the Free plan, net cost would stay at $0, and neither budget would ever fire. Excluding credits means any usage outside Always Free triggers an email, which is what we want.
Alerts go to youssef.m.khafagy+nightshift@gmail.com. Free tier usage alerts (85% of any tracked allowance) are on by default for standalone accounts and go to the root email; we point them at the same address in Billing preferences.

## Other free allowances (not AWS)

| Thing | Free allowance | Projected usage | Headroom |
|---|---|---|---|
| GitHub Actions, private repo | 2,000 minutes/month, 500 MB artifact storage | CI on pull requests only, target under 5 minutes per run, so well under 400 minutes/month | Large. Public repos have no minute cap, so this ceiling disappears if the repo is made public. |
| Vercel Hobby (M8 dashboard) | Hobby plan, non-commercial | One Next.js project, replay mode from static JSON | Verify current Hobby limits before M8 |

Overage on GitHub Actions is only possible with a payment method on file and spending limit raised above $0. The default spending limit on the Free plan is $0, so jobs stop instead of billing.

## LLM providers (free tiers, not AWS)

Groq free plan, per model, per organization (verified):

| Model | RPM | RPD | TPM | TPD |
|---|---|---|---|---|
| openai/gpt-oss-120b | 30 | 1K | 8K | 200K |
| openai/gpt-oss-20b | 30 | 1K | 8K | 200K |
| qwen/qwen3.8-27b | 30 | 1K | 8K | 200K |

429 responses include a `retry-after` header. Cached tokens do not count toward limits.

Gemini API: free tier input and output are available for gemini-3.8-flash, gemini-3.7-flash, gemini-3.6-flash, gemini-3.5-flash, gemini-3.5-flash-lite, gemini-3.1-flash-lite, gemini-2.5-pro, gemini-2.5-flash, gemini-2.5-flash-lite. Not free: gemini-3.1-pro-preview. The per-model RPM/TPM/RPD numbers are only shown in AI Studio for your project, so they are PENDING until you sign in. Limits are per project; RPD resets at midnight Pacific. Free tier content may be used by Google to improve products; all our data is synthetic.

Benchmark feasibility (estimate): assume a full-agent investigation is 12 LLM calls averaging 4K input + 400 output tokens, about 53K tokens. At 200K TPD a Groq model finishes about 3 investigations per day. 42 full-agent runs per model take about 14 days per model (models run in parallel on separate limits). The alarm-only baseline needs about 84K tokens total. At 8K TPM a single investigation takes at least ~7 minutes. Design consequences for M5 to M7: a per-investigation token budget of about 30K to 50K, aggressive tool-output summarization, prompt-prefix caching where the provider supports it, and a resumable multi-day benchmark runner.

## Upgrade plan

The Free plan ends about 6 months after sign-up. Upgrade to Paid when all of these are true, and no later than the start of month 6:
- 30 days of measured usage inside every Always Free allowance above.
- `pause` verified to bring idle SQS, Lambda, and X-Ray usage to about zero.
- Both budgets tested and delivering email.
Remaining credits carry over after upgrade and still act as a safety net.

### Upgrade target: February 2027

The Free plan ends 6 months after sign-up, or earlier if credits run out. Signing up in September 2026 puts the end in March 2027. We upgrade in February 2027, about a month early, so a missed step never closes the account.

Free plan end date from the Billing console after sign-up: **2027-03-18** (account created 2026-09-18, $100 credits, 183 days). The hard deadline of 2027-02-15 is 31 days before it. The owner has set these reminders in their own calendar.

### Calendar reminders

| Date | Reminder | What to do |
|---|---|---|
| 1st of each month, Oct 2026 to Feb 2027 | NightShift monthly cost check (15 min) | Billing home: credit balance and Free plan end date. Free Tier page: any service above 50%. Both budgets: status. Run the cost-check script (DSQL DPUs, SQS requests, Logs bytes) once it exists. Record numbers in this file. |
| 2027-01-15 | NightShift upgrade readiness check | Confirm 30 days of measured usage inside every allowance, `pause` verified, both budgets tested with a delivered email. Fix anything missing before February. |
| 2027-02-01 | NightShift upgrade window opens | Billing console, Upgrade plan, review, Upgrade account. Then re-check both budgets still have IncludeCredit=false, Free Tier alerts still on, and watch Bills daily for 7 days. |
| 2027-02-15 | NightShift upgrade HARD DEADLINE | If not upgraded yet, upgrade now or decide on purpose to let the account close. Before closing: export results, journals, and postmortems so the Vercel replay mode keeps working without AWS. |

If credits ever drop by more than $1 in a month, treat it as an incident: find the cause before doing anything else.

## Sources checked 2026-09-20 (before M2a)

- Aurora DSQL pricing and free tier: https://aws.amazon.com/rds/aurora/dsql/pricing/
- Aurora DSQL cluster quotas and database limits: https://docs.aws.amazon.com/aurora-dsql/latest/userguide/CHAP_quotas.html
- Aurora DSQL PostgreSQL compatibility and migration: https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-postgresql-compatibility-unsupported-features.html
- Lambda free tier, identical for x86 and arm64: https://aws.amazon.com/lambda/pricing/
- Lambda Python tracing options and fixed sampling rate: https://docs.aws.amazon.com/lambda/latest/dg/python-tracing.html
- X-Ray to OpenTelemetry migration, Lambda options: https://docs.aws.amazon.com/xray/latest/devguide/xray-sdk-migration.html
- X-Ray free tier: https://aws.amazon.com/xray/pricing/
- Powertools Tracer wraps the X-Ray SDK: https://docs.aws.amazon.com/powertools/python/latest/core/tracer/

## Sources (checked 2026-09-18)

- Free vs Paid plan: https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html
- Sign-up options compared: https://docs.aws.amazon.com/accounts/latest/reference/sign-up-for-aws.html
- New experience regions: https://docs.aws.amazon.com/accounts/latest/reference/project-regions.html
- New experience spend limits: https://docs.aws.amazon.com/accounts/latest/reference/create-spend-limit.html
- Identity Center account instances: https://docs.aws.amazon.com/singlesignon/latest/userguide/account-instances-identity-center.html
- aws login: https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html
- Always Free list (DSQL on both plans): https://aws.amazon.com/free/
- Aurora DSQL pricing: https://aws.amazon.com/rds/aurora/dsql/pricing/
- Aurora DSQL in Canada: https://aws.amazon.com/about-aws/whats-new/2026/02/amazon-aurora-dsql-additional-aws-regions/
- Aurora DSQL PostgreSQL differences: https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-postgresql-compatibility-unsupported-features.html
- Lambda pricing: https://aws.amazon.com/lambda/pricing/
- Lambda quotas and new-account concurrency: https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html
- Lambda with SQS: https://docs.aws.amazon.com/lambda/latest/dg/with-sqs.html
- SQS idle polling behaviour: https://aws.amazon.com/blogs/apn/understanding-amazon-sqs-and-aws-lambda-event-source-mapping-for-efficient-message-processing/
- DynamoDB provisioned pricing: https://aws.amazon.com/dynamodb/pricing/provisioned/
- Target tracking creates alarms: https://docs.aws.amazon.com/autoscaling/application/userguide/application-auto-scaling-target-tracking.html
- SQS pricing: https://aws.amazon.com/sqs/pricing/
- SNS pricing: https://aws.amazon.com/sns/pricing/
- EventBridge pricing: https://aws.amazon.com/eventbridge/pricing/
- CloudWatch pricing: https://aws.amazon.com/cloudwatch/pricing/
- X-Ray pricing: https://aws.amazon.com/xray/pricing/
- X-Ray SDK maintenance and OpenTelemetry: https://docs.aws.amazon.com/xray/latest/devguide/xray-sdk-migration.html
- CloudTrail pricing: https://aws.amazon.com/cloudtrail/pricing/
- Systems Manager pricing: https://aws.amazon.com/systems-manager/pricing/
- KMS pricing: https://aws.amazon.com/kms/pricing/
- S3 pricing: https://aws.amazon.com/s3/pricing/ and https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/s3/USD/current/s3.json
- Budgets pricing: https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/
- Budgets CostTypes defaults: https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_budgets_CostTypes.html
- Free tier usage alerts: https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/tracking-free-tier-usage.html
- HCP Terraform free tier: https://www.hashicorp.com/en/blog/continuing-hcp-terraform-s-enhanced-free-tier-experience
- GitHub environments: https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments
- Vercel Hobby: https://vercel.com/docs/plans/hobby
- Groq rate limits: https://console.groq.com/docs/rate-limits
- Gemini pricing: https://ai.google.dev/gemini-api/docs/pricing
- Gemini rate limits: https://ai.google.dev/gemini-api/docs/rate-limits
