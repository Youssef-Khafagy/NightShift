# LEARNING.md

How NightShift works and why it was built this way, written for someone who knows Python and web development but has never used AWS. Every section answers four questions: what is this, why does it exist, what would break without it, and what did we get wrong before getting it right. Each major section ends with questions someone could ask me about that part, answered the way I would answer them.

## Read these first

1. **Section 1, the system in one page.** Everything else hangs off it.
2. **Section 3, keeping it at $0.** The constraint that shaped every other decision.
3. **Section 8, Aurora DSQL.** The deepest technical story in the project: a bug with no symptom, found in a billing metric.
4. **Section 15, deploys and rollbacks.** The premise of the whole project is that an agent can roll back safely; this is how.
5. **Section 19, mistakes that taught the most.** A one-page index of every wrong turn, which is where interview questions usually go.

Then read the rest in order when you have time. Section 2 is the AWS vocabulary the rest assumes.

---

## 1. The system in one page

**What it is.** NightShift is an AI on-call engineer for a small online store running on AWS. There are three parts. A store, which is real enough to break in realistic ways. A chaos framework, which breaks it through real mechanisms (a bad deploy, a wrong config value, a removed permission). And an agent, which gets paged, investigates using read-only tools, names the root cause, proposes a fix, and with approval executes one of a few safe, reversible actions. A benchmark harness scores the agent against simpler baselines. The benchmark is the point; the store exists to be broken.

**The store is four Lambda functions, three data stores and one queue.**

| Piece | What it does | Built on |
|---|---|---|
| cart-service | Stores a shopping cart | Lambda + DynamoDB |
| orders-service | Checkout: prices the cart, takes the stock, writes the order | Lambda + Aurora DSQL, publishes to SQS |
| fulfillment-worker | Reads placed orders off the queue, charges them, marks them paid | Lambda + SQS + DSQL |
| payment-provider | A mock third-party payment API with configurable latency and errors | Lambda |

**The path of one order.**

```
client ──PUT /carts/{id}──▶ cart ──▶ DynamoDB
client ──POST /checkout──▶ orders ──GET cart──▶ cart
                              │
                              ├─ one DSQL transaction: price, take stock, write order + lines + idempotency key
                              └─ after commit: SendMessage ──▶ SQS placed-orders
                                                                  │
                                         fulfillment ◀────────────┘  (batches of up to 10)
                                              ├─ invoke payments (charge)
                                              └─ UPDATE orders SET status='paid' WHERE status='placed'
```

Every log line on that path carries the same correlation ID, so "what happened to this order" is a filter, not a search.

**Around the store:** Terraform builds everything. GitHub Actions plans on every pull request and applies only when I trigger it by hand. Lambda publishes metrics and logs to CloudWatch. Five custom business metrics, two feature flags in SSM Parameter Store, a topology description for the agent, a deployments table, and (in progress) alarms that email me.

**Why this shape.** Each piece exists partly for what it does and partly for how it fails. A queue in the middle makes poison messages and backlogs possible. Two different databases fail in different ways. A mock payment provider can be made slow or broken by changing configuration, not code. Aliases on every function make rollback a one-call action. All of it fits inside AWS's Always Free allowances.

**Questions about the system**

- *What does your project actually do?* It is an on-call engineer for AWS. I built a small store, a way to break it realistically, and an agent that investigates the incident and proposes or executes a safe fix. The part I care about is measuring how often the agent is right against simpler baselines, not the store itself.
- *Why serverless?* Mainly cost: Lambda, DynamoDB, SQS and DSQL all have free allowances and nothing bills for existing idle. It also forced realistic operational problems, like cold starts, concurrency limits and at-least-once delivery, which are exactly what an on-call agent has to reason about.
- *Why is there a queue between checkout and payment?* So a slow or failing payment provider degrades fulfilment instead of checkout. It also makes several failure scenarios possible at all: a poison message, a growing backlog and a retry storm only exist when there is a queue in the middle.

---

## 2. AWS in one page, for a web developer

**Account and regions.** An AWS account is a billing and security boundary. Resources live in a region (ours is `ca-central-1`, Montreal); a few services are global (IAM, Budgets, CloudFront). Most pricing, quotas and free allowances are per region.

**Identities.** The *root user* is the email you signed up with; it can do everything and cannot be restricted by any policy. An *IAM user* is an identity inside the account that starts with no permissions. An *IAM role* is an identity with no password that something *assumes* to get temporary credentials: a Lambda function assumes its execution role, GitHub Actions assumes a CI role. Everything has an ARN, a global name like `arn:aws:lambda:ca-central-1:<ACCOUNT_ID>:function:nightshift-orders`.

**Policies.** A policy is JSON listing actions (`lambda:InvokeFunction`), resources (ARNs) and optional conditions. *Identity policies* attach to users and roles ("this role may invoke that function"). *Resource policies* attach to a resource ("this function may be invoked by that role"). *Trust policies* on a role say who may assume it. IAM evaluates all that apply: an explicit **deny always wins**, otherwise you need at least one allow.

**Credentials.** An *access key* is a permanent username and password for the API. *Temporary credentials* come from STS (the Security Token Service) and expire. This project uses only temporary ones.

**Managed services here:** Lambda (functions), DynamoDB (key-value database), Aurora DSQL (distributed PostgreSQL-compatible SQL), SQS (queues), SNS (notifications), CloudWatch (logs, metrics, alarms), SSM Parameter Store (small config values), S3 (object storage, only for Terraform state), CloudTrail (an audit log of API calls).

**Free tier, three kinds.** *Always Free* allowances renew every month forever (Lambda's 1M requests). *Twelve-month free* runs out. *Credits* ($140 on this account) are money AWS pre-paid for you and also run out. The design may only depend on the first kind.

---

## 3. Keeping it at $0

**What it is.** A set of rules, ledgers and checks that keep the account inside Always Free allowances, plus two budgets that email me if anything slips.

**Why it exists.** It is the project's first rule, and it shaped every other decision: which database, which log level, how many alarms, whether the queue consumer runs, even how I check the bill.

**What would break without it.** Nothing technical. The failure is a bill, discovered late. AWS billing data lags by hours, and nothing in AWS stops spending when a budget fires, so a cost control that is not designed in up front is a receipt, not a guard.

### Design first, detect second

- **Only services with an Always Free allowance**, each verified on AWS's own pricing page and recorded in COST.md with the allowance, projected use and headroom.
- **A forbidden list** of things that bill for existing: EC2, NAT Gateway, load balancers, RDS, Fargate, OpenSearch, Secrets Manager, customer managed KMS keys, Lambdas in a VPC.
- **Nothing runs continuously.** Traffic is generated on demand and rate-capped. The queue consumer is off except during runs.
- **Ledgers.** Anything with a small fixed allowance gets a table in COST.md that must be updated *before* the resource exists: DynamoDB capacity (16 of 25 units allocated), custom metrics (5 of 10), alarm metrics (9 of 10). Two of those ledgers are checked by tests, so adding something unbudgeted fails CI.
- **Measure, then project.** Every per-order cost in COST.md is measured, not estimated: 0.25 DPU of DSQL, 4.7 Lambda invocations, about 2.2 SQS requests and 5.9 KB of logs per order. A full benchmark pass (42 incidents × 2 orders/s × 20 minutes = 100,800 orders) projects to about 25K of 100K DPU, 475K of 1M Lambda requests, 250K of 1M SQS requests and 4.0 of 5 GB of logs.

### Budgets, and gross versus net

Two budgets, both emailing me: `nightshift-monthly-1usd` ($1, actual and forecast) and `nightshift-tripwire` ($0.01, actual). For a design meant to be entirely free, the first cent is the signal.

Both set `IncludeCredit=false`. By default a budget measures *net* cost: credits are subtracted first. With $140 of credits, a service could bill every day and net spend would stay $0, so neither budget would ever fire. Excluding credits makes the budget measure the *gross* charge.

**What we got wrong.** For two days I reported "month-to-date spend $0.00, confirmed with Cost Explorer". The console later showed $0.03. CloudTrail, which records every API call for free, showed 91 Cost Explorer events that month: 86 from the console (free), 2 from AWS's own Resource Explorer, and **3 `GetCostAndUsage` calls from the AWS CLI**. The Cost Explorer *API* costs $0.01 per request. So checking the spend was the spend. And CloudTrail kept the request parameters, which explained the $0.00: an unfiltered query includes credit records, so the $0.03 charge and the $0.03 credit cancelled out. It was a net number, the wrong question for a free-tier project. The tripwire budget, which measures gross, fired on it and its email arrived: the first real proof the alerting works.

Rules that came out of it: never call the Cost Explorer API from code; `scripts/cost_check.py` uses only free APIs; never repeat a number from an earlier session without re-reading it.

### The monthly cost check

`scripts/cost_check.py` shows every allowance in one table from two views. **Billing's view** comes from the Free Tier API (`aws freetier get-free-tier-usage`, free, authoritative, about a day behind). **A live view** comes from CloudWatch `GetMetricStatistics` month to date, which also covers Aurora DSQL, which billing does not track at all. Each row is OK, WATCH (50%) or ALERT (85%); any ALERT exits 1 so it can gate a benchmark run. It also lists any service in the bill that this project does not use.

Its first run found AWS Glue requests, which this project does not use. CloudTrail showed they came from `resource-explorer-2`, AWS Resource Explorer's own role indexing the account. Harmless, but "usage nobody planned" is exactly what a cost check should surface, so Glue is now in the expected list with that reason written next to it.

`GetMetricData` is billed even inside the free tier; `GetMetricStatistics` is not. Every script uses the second.

### Idle costs that are not obvious

- An **enabled SQS trigger** long-polls continuously: about 648,000 requests a month per queue at default settings, two thirds of the free allowance, for zero work. So it ships disabled and is enabled only for a run. With a concurrency cap set, idle polling measured 6 empty receives a minute.
- **S3 for Terraform state** has no Always Free tier for new accounts. It is a fraction of a cent a month, the one accepted exception, covered by credits for now.
- **Logs Insights queries** bill every byte in the queried time range, whether or not it matches. The log budget is dominated by the agent's future searches, not by ingestion.

### Pause and destroy

**Pause** (`scripts/pause.py`) brings idle usage to zero and proves it. Almost nothing here costs anything while unused, so pausing is small: make sure the queue trigger is off (through Terraform, never the CLI, so state never drifts), then check that nothing can start by itself (no EventBridge rules or schedules, no provisioned concurrency) and that the last 10 minutes show zero invocations and zero SQS polls. Its first live run correctly refused to call the store paused: a deploy's smoke test had run 6 minutes earlier. Ten quiet minutes later it passed.

**Destroy** (`scripts/destroy.py`) removes the 65 resources Terraform manages, but first writes a real destroy plan and groups it by consequence: data lost for good (the DSQL cluster, the cart and deployments tables), CI that stops working (the OIDC provider and CI roles, which CI itself can never recreate), paging that stops (alarms and the email subscription, which would need the confirmation click again), and everything else. It is a dry run unless `--apply`, and then needs the project name typed out; it applies the exact plan it showed. It runs locally only. What survives: the state bucket, the budgets, the IAM user and group, and the raised concurrency quota, because Terraform never owned them. In M3 it was only ever run as a dry run.

**Questions about cost**

- *How do you guarantee $0?* Nothing is guaranteed by one control, so it is layered. I only use services with an Always Free allowance, each verified and budgeted in a ledger before it exists. Nothing runs continuously. Every per-order cost is measured, and a load generator refuses runs that would cross half of any allowance. Then detection: a $0.01 tripwire budget on gross charges, which has already fired once and reached my inbox.
- *Your console showed $0.03 while you reported $0.00. What happened?* Three Cost Explorer API calls from the CLI at $0.01 each, which I found in CloudTrail by splitting the calls into console and programmatic. They were the calls that reported $0.00, because an unfiltered query nets charges against credits. I stopped using that API and my cost check reports gross usage from free APIs only.
- *Why exclude credits from your budgets?* Credits absorb charges, so a net figure reads $0 while real usage is happening. Whether I am inside the free tier is a question about gross usage.
- *Why doesn't the queue consumer run all the time?* An idle SQS trigger polls around the clock and would spend about two thirds of the free SQS allowance doing nothing. I enable it for a run and disable it afterwards, always through Terraform so state never drifts.
- *How do you know the system is really idle?* A pause script checks that nothing can start by itself and that the last ten minutes had zero invocations and zero queue polls. It refused to say "paused" the first time because a deploy's smoke test had just run, which is the kind of honesty I want from it.
- *What would destroy lose?* It tells you before it does anything: every order and the deployments history, CI's access to AWS, and the alarms. The state bucket and budgets survive because Terraform doesn't own them, on purpose.

---

## 4. Identity and access

**What it is.** How humans, CI and the functions themselves prove who they are to AWS, and what each is allowed to do.

**Why it exists.** A leaked credential is the most common way AWS accounts get hurt, and the damage is proportional to what the credential can do and how long it lives.

### Humans: root locked away, no access keys

Root has a strong password and MFA and no access keys, and is not used. Daily work happens as the IAM user `youssef-admin`, which has MFA and gets `AdministratorAccess` through a group, so removing access is one action.

There are **no access keys anywhere**. An access key never expires and works from anywhere; bots scan GitHub for them within minutes of a push. Instead, `aws login --profile nightshift-admin` opens a normal browser sign-in with MFA and gives the CLI short-lived credentials plus a refresh token. When the session ends I log in again, which is why commands sometimes fail with an expired-token error: that expiry is the feature.

IAM Identity Center would be the standard answer at work, but it needs AWS Organizations, and joining an Organization moves an account off the Free plan, which removes the "cannot be charged" guarantee. The constraint picked the design.

**What we got wrong.** `~/.aws` was a symlink to the Windows drive, where Linux file permissions do not exist, so the refresh token was readable by anything (mode 777) and by Windows sync tools. It now lives in a real Linux directory with mode 700.

### Keeping secrets and the account ID out of git

gitleaks runs in a pre-commit hook and again in CI (the hook can be skipped with `--no-verify`; CI cannot). A custom hook blocks any ARN with a real 12-digit account ID. Account IDs are not secret, but a public repo naming one tells an attacker which account to target. The ID reaches workflows as a GitHub secret, so GitHub masks it in logs.

**What we got wrong.** The real ID was in a local commit before the first push. Because nothing had been pushed, history could simply be rewritten; after a push, it would have had to be treated as leaked.

### CI: GitHub OIDC instead of stored keys

GitHub Actions never holds an AWS key. Each job asks GitHub for a short-lived signed token (a JWT) describing the run: which repository, which branch or event. It hands that to STS with `AssumeRoleWithWebIdentity`. AWS checks GitHub's signature and compares the token's claims to the role's trust policy, then returns credentials that expire in an hour.

The claim that matters is `sub`, the subject. Repositories created after 2026-07-15, like ours, use an immutable format with numeric IDs: `repo:Youssef-Khafagy@232406487/NightShift@1376738088:ref:refs/heads/main`. Names can be released and re-registered by someone else; IDs cannot. The trust policies list exact subjects with `StringEquals`, never a wildcard like `repo:owner/repo:*`, which would also match every fork's pull request.

Two roles, because plan and apply carry different risk:

- **`nightshift-ci-plan`** has AWS's managed `ReadOnlyAccess`. Broad on purpose: a plan must read every resource type the configuration uses, that set grows every milestone, and a role that cannot write cannot break anything. All data is synthetic.
- **`nightshift-ci-apply`** is scoped by hand to this project's resources (`function:nightshift-*`, `role/nightshift-*`, and so on), with actions listed one by one rather than `lambda:*`. It is assumable only from `main`.
- **Explicit denies** on the apply role: it cannot modify the CI roles or the OIDC provider, create IAM users or keys, touch Organizations or budgets, or delete the state bucket. Changing CI's own permissions therefore has to be a local apply by me (`terraform apply -target=aws_iam_role_policy.ci_apply`), which is the deny working as designed.

### What least privilege actually costs

Hand-scoped IAM fails in specific, instructive ways, and each one happened here:

- **One resource, two ARN shapes.** CloudWatch Logs uses `log-group:NAME` for the group and `log-group:NAME:*` for streams inside it. Some actions need one, some the other. The first apply failed on `logs:ListTagsForResource` until both were listed.
- **Hidden resource types.** Tagging an event source mapping needs permission on `event-source-mapping:*`, a different resource type from the function.
- **Service-linked roles.** Creating the first DSQL cluster makes AWS create a role on your behalf (`AWSServiceRoleForAuroraDsql`), which needs `iam:CreateServiceLinkedRole`. It is granted only on the reserved `/aws-service-role/dsql.amazonaws.com/` path and only when the `iam:AWSServiceName` condition equals `dsql.amazonaws.com`.
- **Conditions on missing keys.** A condition on a key that is not in the request's context does not narrow an allow, it makes the allow never match.

### The 403 that was never explained, and the rule it produced

**What happened.** orders-service could not call cart-service through cart's function URL: every request returned 403, and cart's code never ran. An identity policy, a resource policy, `lambda:*` on everything, and several variations all failed. The same request from my laptop succeeded. The eventual pattern: a request signed by an IAM *role* was rejected at these function URLs, while the identical request signed by an IAM *user* succeeded, both for the orders role and later for the CI role. It was never root-caused.

**What we got wrong.** Twice, something looked fixed because a test passed seconds after a change. Both times the pass came from a cached authorization decision or from a deploy replacing warm environments. One wrong conclusion, that a resource policy was unnecessary, was committed and broke checkout. IAM and Lambda cache authorization decisions in both directions, so a fast pass or a fast fail after a change proves nothing.

**What came of it.** Internal calls use the Lambda Invoke API instead of function URLs, which is better anyway: an internal call has no reason to leave AWS. Function URLs remain the external entry point. And CLAUDE.md now says: verify permissions with `aws iam simulate-principal-policy` or by waiting longer than the cache, and when results alternate, stop changing things.

**Questions about identity and access**

- *How does your CI authenticate to AWS?* With GitHub's OIDC tokens, so there are no stored keys. The job gets a short-lived signed token describing the run, exchanges it with STS, and AWS checks the subject claim against an exact list in the role's trust policy. My repo uses GitHub's immutable subject format with numeric IDs, because names can be re-registered by someone else.
- *Your plan role has ReadOnlyAccess. Isn't that too broad?* It is broad on purpose. A plan has to read everything the configuration touches, and a role that cannot write cannot break anything; the only risk is reading data, and all of it is synthetic. The role that can change things is scoped by hand, with explicit denies on anything that could widen its own permissions.
- *What stops the pipeline from giving itself more permissions?* An explicit deny on the apply role for every mutating IAM action on the CI roles and the OIDC provider. Deny always wins in IAM, so changing CI's permissions has to be a local apply by me.
- *Tell me about a hard bug.* A 403 between two of my services that no policy change fixed. I never found the root cause, and I am honest about that. What I learned is that IAM caches decisions both ways, so I had been reading fast results as evidence. I moved internal calls to the Lambda Invoke API and wrote a rule to verify permissions with the policy simulator instead of trial and error.

---

## 5. Terraform

**What it is.** Terraform describes infrastructure in `.tf` files; `plan` shows the difference between the files and reality, and `apply` makes reality match. Every AWS resource in this project is in `terraform/`, one module (`lambda_service`) per kind of function.

**Why it exists.** Reproducibility and review. A change to infrastructure is a pull request with a plan attached, not a console click nobody remembers.

### State, and why its bucket is not Terraform's

Terraform does not diff your files against AWS directly. It keeps a *state* file mapping each resource in the code to the real thing AWS created. Lose it and Terraform forgets it owns anything and tries to recreate it all.

State lives in an S3 bucket so my laptop and CI share it. The bucket has versioning (state can be rolled back), public access blocked, encryption, a TLS-only policy, and lifecycle rules (expire old versions after 30 days; abort incomplete uploads after 7, the classic invisible S3 charge). **Locking** uses `use_lockfile = true`, S3's own conditional writes, so two applies cannot overwrite each other; the old DynamoDB lock table is deprecated.

The bucket is created by a script, not by Terraform: Terraform cannot create its own backend before `init`, and if it managed the bucket, `terraform destroy` would delete the bucket it is writing state to.

### Plan, then apply exactly that plan

CI runs `terraform plan -out=tfplan` and then `terraform apply tfplan`. A bare `apply` re-plans and applies whatever it finds now, which may differ from what was reviewed. A saved plan applies exactly what was reviewed, or fails.

### Three patterns worth knowing

**Letting something else own a value: `ignore_changes`.** Some values must be changed outside Terraform on purpose. The feature flags are flipped during incidents; the `live` aliases are moved by the deploy and rollback scripts. `lifecycle { ignore_changes = [value] }` means Terraform creates the resource with a starting value and never "fixes" it back. Without it, a routine apply in the middle of an incident would silently undo an operator's flag or a rollback.

**Unknown values break `count`.** The Lambda module creates an extra IAM policy only if one is passed in (`count = var.extra_policy_json == null ? 0 : 1`). When that policy referenced a resource's ARN, Terraform could not know the ARN until apply, so it could not know the count, and planning failed. The fix is to build ARNs from values known at plan time (region, account ID, name), which is how `cart_function_arn` and the flag ARNs are built.

**Failing the plan on purpose: preconditions.** The topology parameter must stay under SSM's 4 KB standard-tier limit, because the advanced tier costs money and can never be downgraded. A `precondition` fails the plan if the JSON is over 4,096 characters or contains non-ASCII (so characters equal bytes). It runs during `plan`, so an oversized topology fails the pull request before anything is applied. Both halves were tested by breaking them.

**Questions about Terraform**

- *What is Terraform state and what could go wrong with it?* It is Terraform's record of what it built. It can be lost, corrupted or written by two applies at once, so it lives in a versioned S3 bucket with native locking, public access blocked and TLS enforced. The bucket is made by a script because Terraform cannot create its own backend.
- *When would you not want Terraform to own a value?* When something is supposed to change it at runtime. My feature flags and my Lambda aliases both use `ignore_changes`, because an incident response or a rollback is not drift, and a routine apply must not undo it.
- *Why apply a saved plan?* Because apply without a plan re-plans against whatever reality is now. Applying the saved file applies exactly what was reviewed or fails.

---

## 6. The pipeline and keeping the repo honest

**What it is.** Pre-commit hooks locally, two GitHub Actions workflows, and a smoke test that buys something after every deploy.

**Why it exists.** So a broken change is caught by a machine before it reaches main, and a deployed change is proven to work, not just to apply.

### Hooks locally, the same checks in CI

`.pre-commit-config.yaml` runs formatting and whitespace fixes, YAML/JSON checks, private key detection, gitleaks, the account ID block, ruff, and `terraform fmt`. CI runs the same config with `--all-files`, plus the unit tests on Python 3.12 and 3.14, `terraform validate`, `tflint`, a `trivy` security scan, and a plan. The hook is fast feedback; CI is enforcement.

### The approval gate on a private repo

The textbook gate is a GitHub *environment* with required reviewers. GitHub Free cannot create environments on private repos. So `ci.yml` plans automatically on pull requests and posts only the plan counts (the full plan contains ARNs), and `apply.yml` runs only when I press "Run workflow" on main and type `apply`. Pressing the button is the approval; GitHub records who did it. When the repo goes public, this moves to an environment.

### Reproducible artifacts

**What we got wrong, twice.** The first CI plan wanted to redeploy a function that had just been deployed from the same commit. Terraform's `archive_file` zipped the whole source directory, and a local test run had left a `__pycache__` file there. The artifact depended on the machine, and a junk file had shipped. The fix is an allowlist: the module zips exactly the committed `.py` files, never "the directory minus exclusions".

Later the dependency layer's zip differed between my laptop and the CI runner. Identical sizes, different bytes. I first suspected compression and switched to uncompressed archives; that was wrong. Adding a second digest over file *contents* showed the files themselves differed, and a per-file manifest named one: `jmespath`'s `RECORD` file, which contained the hash of a console script whose first line named the Python interpreter that built it (`/home/youssef/...` versus `/opt/hostedtoolcache/...`). The build now drops `RECORD` lines for files not in the artifact, and compression was turned back on. The lesson: measure a difference at the level where you can act on it, and distrust a fix that makes a symptom go away without explaining the evidence.

### The smoke test

`terraform apply` succeeding means the infrastructure matches the files, not that a customer can buy anything. A long stretch of M2a had every plan and apply green while every checkout returned 502. So every deploy now ends with `scripts/smoke_checkout.py`: store a cart, check out (201), check the total, replay the same idempotency key (200 and the *same* order ID, because a new ID would be a double charge), and check that a missing key is rejected (400). Since M3 it runs inside `scripts/deploy.py`, which rolls back if it fails.

### Commands that gate things must fail loudly

**What we got wrong, four times.** A `trivy ... | tail -6; echo $?` reported `tail`'s exit status, not trivy's, so a failed scan looked clean. Fast IAM results were read as evidence (section 4). A commit went in over a failing test because the test and the commit were joined with `;`. So a rule was written: gating commands run under `set -euo pipefail`.

Then the rule itself failed. A chain meant to apply a permission locally, then push, merge and deploy, hit an expired login on its first command and carried on anyway: it pushed, merged the pull request and started the deploy. The deploy failed safely (CI lacked the permission the skipped step would have added, and nothing was created), but the chain should never have got there. The cause: in the tool I run commands through, `set -e` at the top level of the shell is silently ignored. `set -e; false; echo x` prints `x`. The same lines run in a child shell (`bash <<'EOF' ... EOF`) stop at `false`.

The lesson is older than this project: a safety mechanism that has never been seen to fire is a guess. The hooks, the lock file's hashes and the metrics ledger were all tested by making them fail; the fail-loudly rule was not, until it failed for real. Now every gating pattern is checked with a deliberate `false` before it is trusted.

**Questions about the pipeline**

- *How does a change reach production?* A pull request runs lint, secret scanning, tests and a Terraform plan. After merge, I trigger the apply workflow by hand, which applies a saved plan, moves the aliases to the new versions, records the deployment, and runs a smoke test that buys something. If the smoke test fails, the aliases go back automatically.
- *Why is the apply manual?* GitHub Free has no approval gates for private repos, so pressing the button is the approval, and GitHub records who pressed it.
- *What is a reproducible build and why did you care?* The same commit producing the same bytes on any machine. Without it, the plan is never empty and stops meaning anything, and junk from a laptop can ship. I found two causes, a stray `__pycache__` and a metadata file embedding the build machine's Python path, and fixed both at the source.

---

## 7. Lambda

**What it is.** Lambda runs a function when invoked and bills per request and per GB-second of runtime. Each function here is Python 3.14 on arm64 with 128 MB of memory.

**Why it exists here.** No servers to pay for when idle, a free allowance of 1M requests and 400,000 GB-seconds a month, and realistic failure modes (cold starts, concurrency limits, timeouts) for the agent to reason about.

### Versions, aliases, and why nothing calls $LATEST

Every function has `$LATEST`, which changes whenever code changes. Publishing creates an immutable numbered version. An alias (`live`) is a named pointer to a version, and every caller uses the alias. A deploy moves the alias forward; a rollback moves it back. It is one API call that rebuilds nothing, which is why rollback can be the first thing an agent tries. The version number is in every log line, so I can say which code served which request.

### Function URLs, and why internal calls don't use them

A function URL is a free HTTPS endpoint Lambda manages itself (API Gateway and load balancers are not free). With `AWS_IAM` auth, every request must be signed by a principal allowed to invoke it; unsigned requests get 403. Internal calls use the Lambda Invoke API instead (section 4). `service_client.call` sends the same event shape a function URL would, so each service keeps one handler, and it sets a read timeout, so a slow dependency surfaces as a timeout rather than an endless wait.

### Memory, CPU and where imports run

Lambda gives CPU in proportion to memory; 128 MB is roughly a twelfth of a vCPU.

**What we got wrong.** The first function with real dependencies timed out after 5 seconds with no log line. Timing each import at 128 MB, with imports done lazily inside the handler: Powertools 1.7 s, psycopg 5.8 s, boto3 2.8 s, a DSQL client 1.7 s, **11.9 s** in all. The obvious conclusion was "128 MB is too small". A memory sweep showed something subtler: CPU scales linearly with memory, so the same CPU-bound work costs the same GB-seconds at any size (1.49 at 128 MB, 1.39 at 1,024 MB). More memory buys speed, not savings, for CPU work.

The real fix cost nothing. Lambda runs module-level code in an *init* phase with more CPU than the configured memory buys. Moving the identical imports to module scope took them from 11.9 s to **712 ms**, at the same 128 MB. The rule in CLAUDE.md: imports and clients at module scope, never lazily in the handler.

**Init is billed.** Once platform log lines were turned on (below), a cold orders request reported 2,997 ms handler plus 1,107 ms init, billed as 4,105 ms.

### Anatomy of a slow first request

A new environment's first request took 1.9 to 3.0 s against 0.3 s warm. I suspected the DSQL connection, which is still opened lazily on the first request. Timing it (a `database connected` log line with `token_ms` and `connect_ms`): 37 to 94 ms to sign the token, **about 0.9 s** for the TLS connection. That is under half. The rest is most likely the downstream function cold-starting at the same moment (cart for orders, payments for fulfillment), because a deploy replaces every environment at once. The suspicion was half right, and the timing line showed that before anything was changed on the strength of it.

### Layers and cross-building dependencies

All four services share one *layer*, a zip Lambda unpacks to `/opt` and puts on the import path, holding Powertools and psycopg. Deploys then upload only a few kilobytes of code.

The layer is built on an x86 laptop for arm64 and Python 3.14 without Docker: `pip download --only-binary=:all: --python-version 3.14 --platform manylinux_2_28_aarch64 --platform manylinux2014_aarch64`. `--only-binary` forbids compiling anything locally, which would produce a wheel that cannot load on Lambda. Both platform tags are needed because psycopg only publishes the newer one. A lock file pins every wheel by sha256 and the build uses `--require-hashes`; replacing one hash with zeros made the build fail, which proved the check works. boto3 is not in the layer because the runtime provides it.

### Concurrency

Concurrency is how many copies of a function run at once. New accounts start with 10 per region; ours was raised to 1,000 (free, a limit not a purchase), because reserving any concurrency needs 100 left unreserved. *Reserved concurrency* caps one function, which limits blast radius: a loop cannot spend the whole account's allowance.

**What we got wrong.** Every function started at a cap of 2. At just 1 order per second, a load run was throttled 4 times, all in the first minute, when one slow first request held one of only two slots. orders and cart went to 5; the next run had no throttles on them. That run then exposed fulfilment being throttled by its own queue trigger (section 10).

### Logging configuration

`logging_config { log_format = "JSON" }` makes Python's logging emit JSON with fields at the top level, where queries can filter on them. Log groups are created by Terraform with 3-day retention, so Lambda never creates one that keeps logs forever.

**What we got wrong.** `system_log_level` was `WARN`. Lambda writes its own platform lines (START, REPORT with duration and memory, the init report) at INFO, so none ever reached CloudWatch: cold starts and durations were invisible in logs, and a claim I made that the report line's `initDurationMs` could replace a cold-start metric was wrong. It is now INFO. The cost was measured: log volume per order went from about 2.0 KB to 5.9 KB, still inside the budget.

**Questions about Lambda**

- *How do you deploy and roll back a function?* Every deploy publishes an immutable version, and callers use an alias. Rolling back moves the alias to the previous version: one API call, nothing rebuilt. The version is in every log line, so I know which code served each request.
- *Your functions run at 128 MB. Why not more?* I measured it. For CPU-bound work the GB-seconds are the same at any size, so more memory buys speed, not savings. My real problem was imports running lazily inside the handler; moving them to module scope took them from 11.9 s to 0.7 s at the same 128 MB.
- *Is Lambda init time billed?* Yes. A cold request reported about 3.0 s of handler time and 1.1 s of init, billed as 4.1 s.
- *Why reserved concurrency?* It caps how many copies of a function can run, which limits the blast radius of a loop or retry storm. I started at 2, measured throttling at 1 request per second, and raised the two busiest functions to 5.

---

## 8. Aurora DSQL

**What it is.** Aurora DSQL is AWS's serverless, distributed, PostgreSQL-compatible database. No instance, no VPC, and an idle cluster costs nothing. 100,000 DPUs (its billing unit) and 1 GB of storage are free every month.

**Why it exists here.** Orders need a relational database with transactions, and DSQL is the only one AWS offers that is free when idle and needs no VPC (a Lambda in a VPC is on the forbidden list).

### It is not ordinary PostgreSQL

- **Passwords are IAM tokens.** `generate_db_connect_auth_token` signs a request locally and returns a token used as the password. Nothing to store or rotate; it expires in 15 minutes, but an open connection outlives it.
- **Optimistic concurrency.** Conflicting transactions do not wait for each other; both run, and the loser fails *at commit* with SQLSTATE `40001`. Retrying is how it is meant to be used, so `retry_on_conflict` retries with exponential backoff and **full jitter** (a random delay in `[0, delay)`), because without jitter every loser retries at the same instant and collides again. Every retry is logged and counted as a metric.
- **Repeatable Read only**, and the schema rules below.

**What we got wrong connecting.** `sslrootcert="system"` failed with "certificate verify failed", which looked like a server problem. The psycopg binary wheel ships its own OpenSSL, which does not know where Ubuntu keeps certificates. The code now names the CA bundle file (Ubuntu's or Amazon Linux's, whichever exists) and keeps `sslmode=verify-full`, which also checks the hostname. Separately, local development needed `botocore[crt]`, because the credential provider for `aws login` depends on it and plain boto3 does not include it.

### Migrations when DDL and DML cannot share a transaction

DSQL forbids schema changes and data changes in the same transaction, and allows one schema statement per transaction. So a migration tool cannot change the schema and record that it did so atomically; a crash between the two leaves them out of step. Since the gap cannot be closed, the design makes it harmless: every migration uses `IF NOT EXISTS` so re-running is a no-op, each file holds exactly one statement (enforced), and applied files are checksummed so editing history is refused. All three guards were tested by tripping them.

**What we got wrong.** The first version's dry run created the bookkeeping table on a fresh cluster. A dry run that writes is worse than none, because it teaches you to trust a claim that is false. Now only `--apply` creates anything.

### The schema's shape

- **UUID primary keys generated in the app.** DSQL spreads data by primary key; sequential keys would concentrate writes. The hot-row scenario is only meaningful if the default is not already contended.
- **`inventory` separate from `products`.** A product is read constantly and almost never written; stock is written on every checkout. Merged, two unrelated purchases of the same product would conflict at commit.
- **The price is copied onto each line item**, so an order records what was charged, not today's price.
- **`idempotency_keys` is its own table keyed by the idempotency key**, because a primary key is the one uniqueness guarantee every distributed SQL engine offers.

### A DPU is transaction time, and a bug with no symptom

**The discovery.** Before measuring checkout cost, I looked at what the cluster had already billed. Four minutes each carried almost exactly 315 DPU, and each was one read-only transaction that had read 104 bytes. No amount of work reads 104 bytes. `ComputeDPU` turned out to be exactly `ComputeTime` in milliseconds over 1,000.

A controlled experiment settled it: identical `SELECT 1` work, committed immediately versus held open 60 seconds. The held one billed 60.062 DPU. **One DPU per second a transaction stays open**, within 0.1%. The free allowance is about 27.8 hours of open transaction time a month. A transaction costs money for being open, not for being busy.

**The bug.** psycopg's default is `autocommit=False`, which opens a transaction on the first statement and holds it until someone commits. Four code paths never did. Then Lambda froze the environment with the transaction still open on the server, and DSQL billed it until its 5-minute cap killed it: 315 DPU each time, 1,590 DPU from about six requests. One path held a transaction open *across the call to the payment provider*, so a slow provider would have become a database bill.

It had **no functional symptom at all**: every response was correct, the smoke test passed, nothing was slow or logged. It was visible only in a billing metric, to someone who looked at a number that did not fit. For a project about an on-call agent, that is the thesis in one incident.

**The fix is a default, not four patches.** Connections now default to `autocommit=True`, so a lone statement is its own transaction and ends immediately. Code that needs atomicity says so with `with conn.transaction():`, which checkout does. Patching the four reads would have failed on the fifth.

**Testing a bug with no symptom.** No assertion about a response could catch it, so the tests assert about the *connection*: after the handler returns, is a transaction still open? The fake connection models psycopg's real state machine, and the first two tests prove the fake can tell the difference, because a fake that always says "idle" would make every test pass against broken code. The tests read the real default off `dsql.connect`, so reverting the fix fails them; reverting the three fixed files failed 9 of 15. The sharpest test records the transaction state at the moment the payment provider is called and asserts it is idle.

### Measuring the cost of an order

Batches of 5, 25 and 50 checkouts, with a line fitted through them: the slope is the cost per checkout and the intercept is a fixed cost per batch, which amortises away in a long run.

**What we got wrong.** The first measurement used two batch sizes and reported 0.1189 DPU per checkout. Two points always fit a line exactly, so the fit could not say whether the model was right. A third point moved it to **0.1366, up 15%**, in the direction that flatters the estimate, on the term multiplied by 100,800 in the projection. Fulfilment measured 0.0768 per order at two sizes that agreed to four decimals, which is what a genuinely linear result looks like. Write DPU turned out to come in units of 0.05, so tiny writes cost the same as slightly larger ones; fewer, fuller write transactions are cheaper.

**Questions about DSQL**

- *How does DSQL handle concurrent writes?* Optimistically. Both transactions run and the loser fails at commit with 40001, so I retry with exponential backoff and full jitter and log every retry, because rising retries are the first sign of contention.
- *What is the hardest bug you've found?* A transaction leak with no symptom at all. Four code paths left a transaction open, Lambda froze with it open, and DSQL billed it until a 5-minute cap. Every response was correct. I found it in the billing metric, proved DSQL bills per second of open transaction with a controlled experiment, and fixed it by changing the default to autocommit so the mistake can't happen again, with tests that assert on connection state.
- *How do you run migrations when DDL can't share a transaction with DML?* I can't make them atomic, so I make re-running harmless: `IF NOT EXISTS`, one statement per file enforced, and checksums so an applied migration can't be edited.
- *How did you measure what an order costs?* Batches of three sizes and a fitted line, so the per-order slope is separated from a fixed per-batch cost. My first version used two sizes and was 15% low; two points always fit a line, so they can't tell you if you're wrong.

---

## 9. Checkout: one transaction that has to be right

**What it is.** The one place where being wrong costs a customer money.

```
price the cart -> take the stock -> write the order -> write the lines -> record the idempotency key -> commit
                                                                                     then publish to SQS
```

**The stock check that matters is the UPDATE.** The first `SELECT` reads stock, but under Repeatable Read it cannot see a concurrent purchase, so checking quantity in Python would be theatre. What prevents overselling is `UPDATE inventory SET quantity = quantity - %s WHERE product_id = %s AND quantity >= %s` followed by checking that exactly one row changed.

**Idempotency.** The client must send an `idempotency-key` header (a missing key is a 400). The key is inserted last; a retried request collides on its primary key after everything else is staged, the whole transaction rolls back, and the handler returns the original order with 200 instead of 201. Without it, a network retry after a successful checkout would charge twice.

**Publish after commit.** Publishing inside the transaction could announce an order that then rolls back. Publishing after means a crash in the gap leaves an order stuck in `placed` with no message, which can be found and replayed: losing work you can find beats inventing work that never happened. `scripts/replay_placed_orders.py` does the replaying.

**Questions about checkout**

- *How do you prevent overselling?* The UPDATE that decrements stock has `quantity >= wanted` in its WHERE clause, and I check exactly one row changed. The earlier SELECT can't see concurrent purchases under Repeatable Read, so it only produces a nicer error message.
- *What happens if a client retries a checkout?* It must send an idempotency key. The retry collides on that key's primary key, the transaction rolls back, and I return the original order with a 200. The smoke test checks the replay returns the same order ID, not just a 200.
- *Why publish to the queue after committing?* A message inside the transaction could announce an order that rolls back. After commit, the worst case is an order stuck in `placed`, which a replay script can find.

---

## 10. SQS and fulfilment

**What it is.** SQS is a managed queue. orders sends a message per order; an *event source mapping* polls the queue and invokes fulfillment with batches of up to 10.

**Why it exists.** It decouples payment from checkout and makes queue failure modes possible for the agent to diagnose.

### Settings that are not arbitrary

| Setting | Value | Why |
|---|---|---|
| Visibility timeout | 180 s | While fulfillment works on a message, it is hidden from other consumers. AWS says at least 6× the consumer's timeout (30 s); too short and a slow success is processed twice. |
| `maxReceiveCount` | 3 | After 3 failed deliveries a message moves to the dead-letter queue (DLQ). |
| DLQ retention | 14 days | The maximum, so a human or the agent can still look. |
| Redrive allow policy | only this queue | So no other queue can dump messages into this DLQ. |
| Encryption | SSE-SQS | Free AWS-managed encryption. The trivy scan flagged the queue as unencrypted; the fix that most tutorials use, a customer managed KMS key, costs money and is forbidden. |

### Partial batch failures

By default, if the handler raises, the whole batch of 10 is retried. Nine good messages get processed twice, and each burns a delivery attempt, so one poison message can push its nine neighbours into the DLQ. With `ReportBatchItemFailures`, the handler returns only the IDs that failed, and only those are retried. The handler wraps each *message* in a try, not the loop.

This has a consequence found in M3: because failures are reported per message, the invocation succeeds, and Lambda's `Errors` metric stays at 0. An errors alarm on fulfillment would miss every payment failure.

### Idempotent settlement

SQS delivers at least once, so marking an order paid must be safe twice: `UPDATE orders SET status='paid' WHERE order_id=%s AND status='placed'`. A redelivery changes nothing. A missing order is dropped, not retried, because retrying cannot make a row appear. This is the second of three idempotency mechanisms in the project, each suited to its layer: a primary key collision in checkout, a conditional update here, and DynamoDB conditional writes for incident correlation in M5.

### The trigger, off by default, and capped

The mapping ships `enabled = false` (section 3). **What we got wrong.** It also had no concurrency cap, so the SQS poller could invoke fulfillment above its reservation of 2, and was throttled. A throttled batch goes back to the queue with its receive count raised, so under load healthy orders could reach the DLQ, which is exactly the signal the poison-message scenario relies on. `scaling_config { maximum_concurrency = 2 }` now caps the poller at the function's own limit; both values come from one variable so they cannot drift.

**Questions about SQS**

- *What's a dead-letter queue for?* Messages that fail repeatedly move there after 3 deliveries, so they stop blocking and stop being retried, and someone can inspect them. DLQ depth above zero is one of my alarms.
- *Why partial batch failure reporting?* Without it one bad message fails the whole batch, the good ones are reprocessed, and each retry counts against the DLQ threshold, so innocent messages end up dead-lettered.
- *What happens if a message is delivered twice?* Settling is a conditional update that only matches orders still in `placed`, so the second delivery changes nothing.
- *Why cap the consumer's concurrency?* The poller could invoke above the function's reserved concurrency and get throttled, and each throttled receive counts toward the DLQ threshold. Capping it at the function's limit removes that path.

---

## 11. DynamoDB

**What it is.** A key-value database. The cart table stores one document per cart, read and written by key. The deployments table records every alias move.

**Provisioned, not on-demand.** The free allowance (25 read and 25 write capacity units per region, forever) applies only to provisioned capacity. On-demand looks more serverless but has no free capacity tier. A ledger in COST.md keeps the total across all tables at or below 25: cart 5/5, deployments 1/1, and two M5 tables planned at 5/5 each. No auto scaling, because it works by creating CloudWatch alarms, which are spoken for.

**TTL instead of a cleanup job.** Carts carry `expires_at`, and DynamoDB deletes them for free, without spending write capacity.

**Questions about DynamoDB**

- *Why provisioned capacity?* Only provisioned capacity has a free tier. I keep a ledger so every table and index together stays within 25 units.
- *How do you clean up old carts?* A TTL attribute. DynamoDB deletes expired items for free, which a scheduled job would pay for in invocations and write capacity.

---

## 12. Observability: logs, metrics, traces, topology

**What it is.** Everything the agent will read in M5: structured logs with correlation IDs, five business metrics, Lambda's own metrics, a topology description, and Lambda's X-Ray traces.

**Why it exists.** An agent can only diagnose what the system reveals. If this is weak, M5 looks like a model problem when it is a visibility problem.

### Correlation IDs, proven end to end

Each service reads `x-correlation-id` from the request (or mints one) and attaches it to every log line. HTTP headers cannot cross the SQS queue, so orders copies the ID into a message attribute and fulfillment reads it back. `scripts/trace_correlation.py` proves it: it places an order with a fresh ID and rebuilds the chain from that ID alone (cart stored, cart read, checkout complete, charge approved, order paid), and separately finds every line mentioning the order ID and requires all of them to carry the same correlation ID. The second query catches what the first cannot: a line with the right order and the wrong ID. The pass/fail logic is two pure functions tested against broken chains.

### Metrics through EMF, and the ledger that enforces them

A CloudWatch custom metric is billed per unique combination of name and dimension values; 10 are free. So the five metrics (`CheckoutsPlaced`, `CheckoutsRejected`, `SerializationRetries`, `OrdersPaid`, `PaymentFailures`) are separate names with `service` as the only dimension. A reason for a rejection goes in the log line, never in a dimension, or each reason would be a billed metric.

They are emitted with *EMF*, embedded metric format: Powertools prints a JSON log line with an `_aws` block, and CloudWatch Logs turns it into a metric. No extra API call and no extra permission, but each metric costs both a metric slot and log bytes (about 210 bytes per line).

`tests/test_metrics.py` drives every path that emits a metric, captures the EMF lines, and checks them against the ledger table in COST.md. An extra dimension or an unbudgeted name fails CI; both were tested by planting them. Two traps: the Infrequent Access log class silently does not extract EMF, and under Lambda's JSON log format I checked the raw line arrived unwrapped before trusting it.

### Log volume and the cost of searching

Measured, an order produces about 5.9 KB of logs with platform lines on. A benchmark pass ingests about 0.6 GB. The bigger cost is searching: Logs Insights bills every byte in the queried time range across every log group named. The agent gets a 25 MB scan budget per investigation, and the Logs budget sits at 4.0 of 5 GB. Whether Lambda logs count against the 5 GB free tier (AWS reprices them as "vended logs") was checked against the actual bill, which showed only a free-tier ingestion line; that is recorded as evidence, not proof, to re-check at higher volume.

### The topology parameter

`/nightshift/topology` in SSM holds compact JSON of what exists and what depends on what: each service's function, log group, callers, stores and flags; the queue and its DLQ; the cluster ID. It is **structure, not state**: no versions, timeouts or capacity, because those are what some faults change, and a snapshot would mislead. Terraform generates it from resource attributes and a precondition keeps it under 4 KB (currently 1,173 bytes). The live value was compared byte for byte with Terraform's.

### Tracing: decided by research

**What we got wrong in the plan.** The plan was to hand-build OpenTelemetry with an X-Ray UDP exporter and drop it if it added more than 300 ms of init. Reading AWS's docs and PyPI first showed no small documented Python path: AWS recommends a layer (whose ARN embeds an AWS account ID and which wraps every cold start), the manual path needs a collector a Lambda does not have, and the only packaged exporter lives in a distribution with 62 dependencies. Meanwhile Lambda *active tracing* was already on, free, at zero init cost. ADR 0001 records the decision: active tracing only, and timing inside handlers comes from log lines like `database connected`.

**Questions about observability**

- *How do you follow one request through the system?* A correlation ID attached to every log line, carried in HTTP headers and in an SQS message attribute across the queue. I have a script that places an order and rebuilds its whole path from the ID alone, and it also checks no line carries the wrong ID.
- *How do you stop metrics from getting expensive?* One dimension with a fixed value, separate metric names, and a test that checks every emitted metric against a budget table and fails CI on anything extra.
- *Where does your observability budget go?* Into searching logs, not writing them. Logs Insights bills every byte in the time range, so the agent will have a per-investigation scan cap.
- *Why didn't you add distributed tracing?* I researched it first. No small, documented Python exporter works inside Lambda without a collector, and Lambda's own tracing was already on for free. I wrote an ADR with the conditions for revisiting it.

---

## 13. Feature flags: operational controls

**What it is.** Two switches in SSM Parameter Store that change behaviour during an incident without a deploy.

| Flag | Read by | When set |
|---|---|---|
| `payments_degraded_mode` | fulfillment | Skip the payment provider, leave orders in `placed`, acknowledge the message. `replay_placed_orders.py` republishes them later. |
| `checkout_rate_limit` | orders | Allow N checkouts per second per execution environment; the rest get 429 with `retry-after: 1`. 0 is off. |

**Why these designs.** Degraded mode defers rather than refusing checkout (which would hurt customers more than a slow provider does) or failing fast (which fills the DLQ and fakes a poison-message signal). The rate limit is a token bucket in each execution environment, not a global counter, because a shared counter would cost write capacity and add a new way for every checkout to fail. The real ceiling is 5N, since orders runs up to 5 environments.

**The reader.** `src/common/flags.py` caches each value for 30 seconds, so SSM is not on every request. It **fails open**: if SSM errors, it keeps the last value read, or the default, and caches the failure too, so an outage does not add a failing call per request. Timeouts are 1 second.

**What we got wrong.** botocore's `max_attempts` counts *retries*, not attempts, so `{"max_attempts": 2}` made three calls. A test caught it; the setting is `total_max_attempts`. And the replay script refused its first live run because the order was only 7.5 minutes old against a 10-minute safety filter; I had assumed 25. The guard was right. When a guard refuses, check its inputs before doubting the guard.

**Verified live.** The rate limit took effect 26.5 s after the write and was lifted 16.6 s after reset, both inside the 30 s cache. Degraded mode deferred a real queued order; after clearing the flag, the replay script got it paid with exactly one payment call.

**Questions about flags**

- *Why SSM instead of environment variables?* Changing an environment variable here means publishing a new version, which is a deploy. A flag has to change in seconds during an incident. SSM standard parameters are free, and each function can read only its own flag.
- *What happens to checkout if SSM is down?* Nothing a customer sees. The reader keeps the last value or the default and caches the failure for 30 seconds, with 1-second timeouts.
- *Your rate limit isn't exact. Is that OK?* It's per execution environment, so the ceiling is 5 times the setting. An exact limit needs a shared counter on every checkout, which costs capacity and adds a failure mode. For shedding load in an incident, roughly right is the better trade.
- *How do you stop `terraform apply` from undoing a flag change?* `ignore_changes` on the value. Terraform owns that the parameter exists, not what it's set to.

---

## 14. Load testing and measurement

**What it is.** `scripts/load.py`, the only thing that sends traffic in bulk, and the measurements it produced.

**Refuse first.** It is a dry run unless `--run`. It plans every cart from a seed, projects Lambda, SQS, DSQL and log cost from measured per-order numbers, reads month-to-date usage live, and refuses if a run exceeds 5 orders/s, 30 minutes or 3,600 orders, would take DSQL or Lambda past half their monthly allowance, would empty any product's stock (an out-of-stock storm looks exactly like a fault), or would run with the consumer off. Every reason is reported, not just the first.

**Open loop.** A generator that waits for each response slows down exactly when the system does and hides the slowdown; this is called *coordinated omission*. `load.py` sends on a fixed schedule with at most 8 requests in flight, and counts any tick that hits the cap as *dropped*, so overload shows up as a number.

**What we got wrong.** The first open-loop test had the fake request block the scheduler, which is closed-loop behaviour, and asserted the closed-loop result; it proved nothing. The first percentile function used `round(99.5)`, which Python rounds to 100 (half to even); nearest-rank is `ceil`. And three of four cost projections were off on the first live run: fulfilment batched 1.7 orders per invocation, not 10, and an enabled consumer's idle polling was missing from the SQS model entirely. The constants now come from measurements, rounded up.

**Questions about load testing**

- *How do you stop a load test from blowing your free tier?* It's a dry run by default, projects every cost from measured numbers, reads this month's usage live, and refuses anything that would pass half of an allowance.
- *What is coordinated omission?* A closed-loop generator slows down with the system and under-reports load exactly when it matters. Mine sends on a fixed schedule and counts dropped requests.
- *Your projection was wrong. What did you learn?* Batching is weak at low rates because several pollers each take what's there, and an enabled consumer polls around 20 times a minute regardless of load. Both are in the model now, rounded up so the guard errs toward refusing.

---

## 15. Deploys and rollbacks

**What it is.** Terraform publishes a new version of each changed function. `scripts/deploy.py` moves each `live` alias to exactly that version, records every move in the `nightshift-deployments` DynamoDB table, and runs the smoke test. If anything fails, it moves every alias it touched back, records each as `auto-rollback` with the reason, and fails the job. `scripts/rollback.py` moves one service back, records it, and runs the smoke test.

**Why it exists.** Rollback is the first thing an on-call agent should try, so it must be fast, safe and visible. The deployments table is what the agent reads to answer "what changed just before this started".

**What would break without the split.** Before M3, Terraform owned each alias's version. Any rollback done outside Terraform, by a script or by the agent, would have been drift, and the next routine `terraform apply` would have silently moved the alias forward again, undoing the rollback. Now the alias has `ignore_changes` on its version: Terraform creates it and publishes versions, and only the scripts move it.

**Details that matter.** Aliases move leaf services first (cart and payments before the services that call them). A row is never overwritten (conditional write). A service missing from the version list is an error, not a skip. If putting one alias back fails, the others are still restored and the stuck one is named.

### What a rollback refuses to do

By default a rollback undoes the service's last recorded move. It refuses to guess, and asks for an explicit `--to VERSION`, in three cases:

- **No history** for the service.
- **The alias is not where the table says.** Something moved it outside the recorded path, so the table's "previous" cannot be trusted.
- **The last move was already a rollback.** Undoing a rollback re-deploys the version someone just decided was bad. This is the guard that matters most once the agent can call rollback: an agent that rolls back twice would put the fault back.

A reason is required, because the record is for whoever investigates next. After moving, it runs the smoke test; if that fails it reports loudly but never moves anything again, since automatically reverting a rollback has the same problem.

### Proven live

orders was moved 17 → 16 → 17 → 16 → 17 through both scripts: an explicit rollback, a deploy forward, a rollback taken from history, and a deploy forward again. Both refusals fired when they should (no history at first; a second rollback in a row). Every move landed in the table with actor and reason, and each ran a smoke test that bought something. Afterwards **`terraform plan` was clean**, which is the property the whole split exists for: four alias moves outside Terraform, and the next apply would not undo any of them.

**What we got wrong.** The live output printed the smoke test before the line saying the alias had moved. When stdout is a pipe, Python buffers the parent's output while the smoke test subprocess writes straight through and overtakes it. Harmless here, misleading in a CI log during an incident, so both scripts flush before running the smoke test.

**Questions about deploys**

- *How do you roll back?* Move the alias to the previous version recorded in the deployments table. One API call, nothing rebuilt, and the rollback itself is recorded.
- *What if a deploy breaks checkout?* The deploy script runs a smoke test that buys something after moving the aliases. If it fails, every moved alias goes back and each reversal is recorded with the reason.
- *Why doesn't Terraform move the aliases?* Because then a rollback done by a script or by the agent would look like drift, and the next apply would undo it. I proved the split works: after four alias moves by the scripts, `terraform plan` was clean.
- *What stops the agent from rolling back into a bad version?* The rollback refuses when the last move was already a rollback, and when the alias isn't where the history says. In both cases it needs an explicit version, so it can't flip back to the bad one by accident.
- *Why record a reason?* The table is the first thing the next person, or the agent, reads to answer "what changed before this started". A move without a reason answers half the question.

---

## 16. Alarms and paging

**What it is.** Ten CloudWatch alarms, each watching one metric. When one changes state it publishes to the SNS topic `nightshift-alerts`, which emails me. In M5 the same state change will also start an investigation.

**Why it exists.** Something has to notice a fault before the agent can investigate it. Every scenario in the benchmark starts with an alarm firing.

**What would break without it.** The agent would have no trigger, and a fault would be found by whoever next looked.

### The budget decides the shape

10 alarm metrics are free. A metric math alarm is billed for every metric in its expression, so an error *rate* (errors ÷ invocations) costs 2, and four of them would cost 8. So every alarm watches one plain metric, and the ledger in COST.md is exactly full:

| Alarm | Watches | Fires when |
|---|---|---|
| `orders-errors`, `cart-errors`, `payments-errors`, `fulfillment-errors` | `AWS/Lambda Errors` per function | ≥ 1 in a minute |
| `checkout-latency` | orders `Duration` p99 | ≥ 2,000 ms for 3 minutes in a row |
| `queue-age` | `ApproximateAgeOfOldestMessage` | ≥ 5 minutes |
| `dlq-depth` | DLQ `ApproximateNumberOfMessagesVisible` | ≥ 1 |
| `throttles` | account-wide `AWS/Lambda Throttles` | ≥ 1 in a minute |
| `serialization-retries` | `NightShift SerializationRetries` | ≥ 10 a minute for 2 minutes |
| `payment-failures` | `NightShift PaymentFailures` | ≥ 3 in a minute |

`tests/test_alarm_ledger.py` checks that the alarm names in `terraform/alarms.tf` and the ledger rows are the same set, so an alarm cannot exist without a budget line. It was tested by planting an unbudgeted alarm.

### Three alarms that would have been wrong

Writing a one-line reason for each alarm before building it found three problems:

- **fulfillment's errors alarm was blind to payment failures.** The worker reports each failed message back to SQS itself (section 10), so the invocation succeeds and Lambda counts no error. The tenth slot went to `payment-failures`, which watches the custom metric the worker emits exactly when a payment call fails.
- **`queue-age` would have paged after every deploy.** The smoke test places a real order, and with the consumer off between runs, its message sits in the queue ageing. So this alarm's notifications are switched on only while the consumer is (`actions_enabled = var.queue_consumer_enabled`). It still records its state, which the agent can read; it just doesn't email about a backlog that is expected.
- **Silence would have paged.** An idle store publishes no data at all. Every alarm sets `treat_missing_data = "notBreaching"`.

The p99 alarm needs three bad minutes in a row, because one cold start (about 3 s) can push a single quiet minute's p99 over the line on its own.

### Paging through SNS

**The topic is unencrypted, deliberately.** CloudWatch alarms cannot publish to a topic encrypted with AWS's managed SNS key: that key's policy does not let CloudWatch use it, it cannot be edited, and the alarm action fails silently. The only fix is a customer managed KMS key, which costs $1 a month and is forbidden here. The messages carry alarm names and metric values from synthetic data. The security scanner flags an unencrypted topic, so that one finding is suppressed next to the reason.

**Only our alarms may publish.** The topic policy allows `cloudwatch.amazonaws.com` to publish only when the source is an alarm in this account whose name starts with `nightshift-`.

**Proven live.** After the deploy, `nightshift-dlq-depth` was forced into ALARM with `aws cloudwatch set-alarm-state` and back to OK 20 seconds later. The alarm history showed "Successfully executed action" for both, and both emails arrived. At the same moment `queue-age` sat in ALARM with actions disabled, because smoke-test orders were waiting while the consumer was off: the exact situation it was designed to stay quiet about.

**What we got wrong getting email delivered.** AWS will not deliver to an email subscription until someone clicks a confirmation link, and the first confirmation email was nowhere to be found. It was in Spam: Gmail's search skips Spam unless you add `in:anywhere`. After resending it (`aws sns subscribe` again on a pending subscription sends a fresh email) and marking it "not spam", a test message landed in the inbox. The confirmation page is also why alarm emails carry an unsubscribe link: clicking it would silently stop paging, so alarm emails should never be forwarded.

**Questions about alarms**

- *Why error counts instead of error rates?* A rate is metric math over two metrics and costs two of my ten free alarm slots. Counts cost one each, and at this traffic "any error" is the right threshold anyway.
- *What does an alarm do when there's no traffic?* There's no data. Every alarm treats missing data as not breaching, because an idle store must not page anyone.
- *Why doesn't your worker's error alarm catch payment failures?* The worker reports failed messages back to SQS individually, so Lambda doesn't count an error. I found that while budgeting the alarms and spent my last slot on a payment-failures alarm on a custom metric instead.
- *Why is your SNS topic unencrypted?* CloudWatch can't publish to a topic encrypted with AWS's managed key, and a customer managed key costs money. The messages are alarm names and numbers from synthetic data, and I suppressed the scanner finding with that reason written next to it.
- *How do you stop a backlog alarm from firing during maintenance?* The queue-age alarm only notifies while the consumer is enabled. When I pause the store, a waiting message is expected, so it records state but doesn't page.

---

## 17. Chaos: breaking it on purpose

**What it is.** A small framework in `chaos/` that breaks the store in one specific way, waits for the alarms, puts everything back exactly, and writes down what happened. Each way of breaking it is a scenario: a YAML file that says what to change, which alarm should fire, what the right answer is, and how to recover.

**Why it exists.** The benchmark needs incidents with a known answer. An agent can only be scored as right or wrong if someone knows the real root cause, so the faults have to be staged and the answer recorded before the agent ever looks.

**What would break without it.** There would be nothing to measure. Waiting for real outages takes too long, and nobody would know for sure what caused them.

### Real mechanisms only

A fault that uses a switch in the app code (`if FAIL: raise`) teaches the agent to look for the switch. So every fault goes through the same path a real mistake would take:

| Scenario | What changes | How |
|---|---|---|
| 1 bad deploy | orders code: a metric unit typo (`"Counts"`) that raises after the order is saved | a real code deploy: build the zip the way Terraform does, publish a version, move the alias, record it in the deployments table |
| 2 config regression | cart's table name set to `nightshift-carts` | a real configuration deploy, recorded like any other |
| 4 slow dependency | the payment provider slows to 5 s, past fulfillment's 3 s timeout | a config change with **no** deployments row, because a real third party's slowdown would leave none in our table |
| 5 poison message | one message with `orderId` instead of `order_id` | a real `SendMessage`: a producer bug |
| 11 legit spike | traffic rises from 1 to 4 orders a second | the load generator. Nothing is wrong |

Because each injection is a real deploy, the evidence is the evidence a real one leaves: a new version, an alias move, a row in the deployments table, errors in the logs. The agent's `list_recent_deployments` will see the bad deploy exactly as it would see mine.

### Keeping the agent from seeing the answer

If the agent could read the scenario file, it would be reading the answer key. So:

- `chaos/` is never deployed and never imported by anything in `src/`.
- `tests/test_integrity.py` fails the build if deployed code imports `chaos`, or contains the words chaos, inject, fault or scenario, even in a comment. A log line saying "injected fault" would give the game away. Four existing comments had to be reworded to pass it.
- Results, including the ground truth, go to `results/chaos/`, which the agent never reads.
- The answers use closed lists (`fault_category`, `component`, allowed actions), checked by Pydantic when the file loads. Grading compares two words from the same fixed list, so there is nothing for a judge to interpret.

### A run, start to finish

`python -m chaos.run --scenario N --run` (a dry run without `--run`, printing every write it would make):

1. **Preflight.** The queue consumer is on, `terraform plan` is clean, no alarm is already firing, the database endpoint is set, and the live code of any function it will change is byte-identical to Terraform's zip, so putting it back cannot drift.
2. **Warm-up.** Three minutes of normal traffic at 1 order a second, so the incident starts from a warm system and the first cold starts are not mistaken for the fault. If the traffic generator has died by the end of warm-up, the run stops here and injects nothing.
3. **Inject.** Before each change, what it is about to change is saved to `state.json`. If the laptop dies mid-run, `--restore state.json` undoes it.
4. **Wait for the expected alarm**, recording how long it took. A no-fault scenario waits the whole window and records anything that fires.
5. **(M5 onwards) the agent investigates here.**
6. **Recover and check health.** Roll the alias back and record the rollback, restore `$LATEST` byte for byte, delete the injected version, then require: alarms back to OK, the dead-letter queue empty where it matters, a smoke-test checkout, and `terraform plan` clean.

### Results (2026-09-23, commit 66184f1)

| Scenario | Expected alarm fired after | Also fired |
|---|---|---|
| 1 bad deploy | `orders-errors`, 113 s | nothing |
| 2 config regression | `cart-errors`, 48.5 s | nothing |
| 4 slow dependency | `payment-failures`, 79.6 s | `throttles`: payments hit its limit of 2 |
| 5 poison message | `dlq-depth`, 698.5 s | `queue-age` at 450 s; `throttles` from one stray cart throttle |
| 11 legit spike | nothing expected | `serialization-retries` and `throttles` |

All five recovered with every health check passing. The poison message is slow by design: it has to fail three deliveries, each after a 180 s visibility timeout, before SQS moves it to the dead-letter queue.

**The side alarms are part of the test, not noise to hide.** In scenario 4, each payment call most likely holds a payments environment for 5 s while fulfillment gives up at 3 s and tries again, so payments runs out of its 2 slots. A real slow dependency causes exactly that knock-on. Scenario 11 fires two alarms with nothing wrong, and it has to: the agent is only started by an alarm, so a spike that fired nothing could never test whether it can say "no fault". The hard part for M7 is that scenario 7 (hot-row contention) also fires `serialization-retries`. The agent has to tell a busy store from a contended one by looking at traffic volume.

### What we got wrong

- **Recovery left the bad version as the newest one.** The first live run of scenario 1 rolled the alias back and restored the code, and every check passed except `terraform plan`. The injected version was still the newest published version. That is the version Terraform reports and the one `deploy.py` ships, so the next routine deploy would have quietly shipped the bad code again. Recovery now deletes the version it published. The plan-clean check exists for exactly this: "the alarms are green" is not the same as "put back".
- **A run with no traffic looked like a missed detection.** The first run the next day injected the bad deploy and no alarm fired. It looked like the alarm was broken. Per-minute invocation counts showed zero for orders: the traffic generator had exited in its first second because `DSQL_ENDPOINT` was not set, and the runner had sent its output to `/dev/null` and never checked its exit status. Broken code that nobody calls raises nothing. The runner now refuses to start without the variable, keeps the generator's output in a log, stops before injecting if a generator has died, and counts every generator's exit status in the health check. Both guards were tested by tripping them on the real system.

**Questions about chaos**

- *How do you know the agent isn't cheating?* The answer key lives in `chaos/`, which is never deployed, and a test fails the build if deployed code imports it or even uses the words chaos, inject, fault or scenario. The agent sees what a human on call would see: logs, metrics, deploy history. Nothing else.
- *Why not just add a flag in the code that makes it fail?* Because then the agent learns to find the flag, and the benchmark measures that instead of diagnosis. My bad deploy is a real deploy with a real typo in it, recorded in the deployments table like any other, so the evidence looks like real evidence.
- *How do you make sure a scenario doesn't leave the system broken?* The injector saves what it's about to change before changing it, restores it byte for byte afterwards, and the run only passes if `terraform plan` is clean. That check caught a real bug: after a rollback, the bad version was still the newest one, and the next deploy would have shipped it again.
- *Your no-fault scenario fires alarms. Isn't that a false positive?* It's the point. The agent only wakes up on an alarm, so a no-fault test has to fire one. What I'm testing is whether it looks at the traffic, says "this is a legitimate spike", and changes nothing.
- *What was the hardest bug in the framework?* A run where no alarm fired. It looked like a detection failure, but the traffic generator had died on a missing environment variable and the runner threw its output away. Nobody called the broken code, so nothing broke. Now the runner stops before injecting if traffic isn't flowing. The lesson was the same one as elsewhere in this project: a check nobody reads is not a check.

---

## 18. The agent: an on-call engineer in a while loop

**What it is.** A Python program that gets paged when an alarm fires, investigates with ten read-only tools, and ends with a structured answer: which component broke, which kind of fault it was (from a fixed list), how confident it is, which steps are the evidence, and what a human should do. It changes nothing. In AWS it runs as the `nightshift-agent` Lambda, started by an EventBridge rule; on the laptop it runs as `python -m agent.investigate`. Both run the same code.

**Why it exists.** It is what the benchmark measures. Everything before it (the store, the telemetry, the chaos framework) exists so this can be scored.

**What would break without each part.** Without the hard limits, a confused model loops until the free quota is gone. Without checkpoints, a Lambda timeout throws the investigation away. Without the Investigator role, a prompt injected into a log line could reach a write API. Without the report validation, "no fault, component orders" goes into the results as an answer.

### No framework: a while loop of about 65 lines

Each turn: rebuild the conversation from the saved state, send it with the tool definitions, run the tool calls the model asks for (at most three), save a checkpoint. Stop when the model calls `finish_investigation` or a limit is hit: 15 steps, 40,000 tokens, 840 seconds. A stop on a limit is an answer too: `insufficient_evidence`, with the reason.

The conversation is never stored. It is rebuilt every turn from the journal of steps, which is what makes a crash cheap: load the checkpoint, rebuild, carry on. A Mistral investigation killed with `SIGKILL` after two steps resumed from DynamoDB at step three with nothing repeated.

### The limit that shapes everything is per request

Groq's free tier allows 8,000 tokens a minute. That is also the largest single request that can ever be sent, however long you wait. So the agent keeps only the three most recent tool results in full and a one-line summary of older ones, estimates the size before sending, and drops full results until it fits. The ten tool definitions alone cost 900 to 1,300 tokens per call, measured.

### Three providers, one interface, no SDKs

Groq and Mistral speak the OpenAI format; Gemini has its own. One set of dataclasses, three small translations, standard-library HTTPS. A 429 waits for `retry-after`, but never past the wall clock. Mistral's free tier turned out to be limited per model: its flagship models answer with a limit of zero requests a minute, and `ministral-14b` is the one that works, measured from its own response headers because its published limits are only in a console.

### Two roles, so the model's reach is small

The tools run as the **Investigator role**: reads on this project's resources, explicit denies on IAM, role chaining, the Terraform state, every write, invoking functions, and receiving queue messages, plus a permissions boundary so even an admin policy attached by mistake grants only reads. It was checked with the IAM policy simulator before it existed, 29 cases, and every tool was then called live through it.

The Lambda's own role holds what the tools must never have: the API keys (SSM SecureString, written by a script, never in Terraform state) and the checkpoint writes. It assumes the Investigator role for the tools, exactly as the laptop does.

Tool output reaches the model as JSON under an `untrusted_data` key, so a log line saying "ignore your instructions" arrives as an escaped string, not as part of the prompt.

### One investigation per incident

A bad deploy can fire three alarms in a minute. The first alarm takes a lock with a conditional DynamoDB write; alarms in the next ten minutes join it instead of starting their own. The investigation ID comes from the EventBridge event ID, so a redelivered event or a Lambda retry finds its own lock and resumes its own investigation.

### The answer is validated where it is made

Pydantic checks the shape; the rules check the meaning: `no_fault` means component `none`, a fault needs evidence, evidence must be real tool steps that returned something. The same check runs when the model calls `finish_investigation`, so a bad answer goes back to the model instead of into the results. A deterministic grader compares the answer to the scenario's ground truth, two words against two words. The agent may not import it.

### The first live check (2026-09-23, Mistral ministral-14b, one run per scenario)

| Scenario | Answer | Truth | Result | Tokens |
|---|---|---|---|---|
| 1 bad deploy | orders / bad_deploy, 90 | orders / bad_deploy | correct, 110 s after injection | 27,906 |
| 2 config regression | cart / bad_deploy, 95 | cart / config_regression | right component, wrong category | 29,429 |
| 4 slow dependency | placed-orders / retry_storm, 90 | payments / slow_dependency | wrong: symptom taken for cause | 32,729 |
| 5 poison message | payments / bad_deploy, 95 | placed-orders / poison_message | wrong: read the previous run's cleanup | 18,513 |
| 11 legit spike | payments / throttling, 95 | no fault | wrong: read the previous run's throttles | 29,828 |

One right out of five, every answer at 90 or 95 confidence, no run above 33K tokens. One run per scenario is not an accuracy figure; it is a list of what to fix before measuring one.

### What we got wrong

- **The answer key was in the deployments table.** Chaos recovery wrote "recovery after scenario run 02-config-regression-..." as the rollback reason, in the table the agent reads. Found while building the tools; now a neutral constant held to the banned-words test, and the four old rows were rewritten.
- **Back-to-back scenarios contaminate each other.** Scenario 5's answer was built on a CloudTrail event from scenario 4's cleanup; scenario 11's on scenario 4's throttles. The runs were minutes apart and the tools look back an hour or more. The benchmark needs a gap longer than the longest lookback, or tools scoped to the incident.
- **Groq validates tool arguments itself** and answered HTTP 400 to `limit: 100`, which the loop first treated as fatal. It now goes back to the model like any bad call.
- **Times were in the laptop's zone.** boto3 returns local datetimes; logs and deployments are UTC. Tools now always say UTC.
- **An answer cited steps the loop had skipped.** Evidence now has to be a step that returned data.
- **The laptop slept mid-run** for two and a half hours. Nothing broke, because recovery had already happened, but a batch needs the machine awake.

**Questions about the agent**

- *Why no agent framework?* The `run` method is about 65 lines, and `agent/loop.py` about 280 with the control tools and limits. I can explain every one: rebuild the conversation, call the model, run tools, checkpoint, check limits. A framework would hide exactly the parts the project is about: limits, checkpoints, and what the model is allowed to reach.
- *How do you stop a prompt injection from doing damage?* The model can only call read-only tools, and those run as a role that is denied every write, with a boundary on top. Tool output is labelled as untrusted data. The worst an injection can do today is make the answer wrong, which the benchmark measures.
- *What happens if the Lambda times out mid-investigation?* The state is checkpointed after every step, Lambda retries the event, and the retry resumes from the checkpoint. I tested it by killing the process with SIGKILL and resuming from the table.
- *Why did your agent get four out of five wrong?* One small model, one run each. Two answers came from the previous scenario's leftovers, which is a flaw in how I ran the batch, not only in the model. The others show the model taking a symptom or a recent deploy for the cause, at 90 to 95 confidence every time. That is what M7 exists to measure, against baselines and bigger models.
- *How do you keep it free?* Every limit is a config value: 40K tokens, 15 steps, 840 seconds, a 20 MB log scan budget. The trigger is off except during runs, one investigation runs at a time, and the most any run has used is 33K tokens.

---

## 19. Mistakes that taught the most

| Mistake | How it was found | What changed |
|---|---|---|
| Transactions left open, billed per second, no symptom | A billing metric that didn't fit | autocommit by default; tests assert connection state (8) |
| Imports inside the handler, 11.9 s at 128 MB | A timeout, then timing each import | Imports at module scope: 0.7 s (7) |
| "128 MB is too small" | A memory sweep | CPU work costs the same GB-seconds at any size (7) |
| A 403 "fixed" by cached decisions | Results that flipped | Verify with the policy simulator; stop changing things when results alternate (4) |
| `pipe \| tail` hid a failed scan; a commit over a failing test; `set -e` silently ignored by the tool's shell | Reading the output again; a chain that merged after an expired login | Gating chains run in a child shell, verified with a deliberate `false` (6) |
| Two points fit a line | A third batch size | Three points minimum for a fit (8) |
| Artifacts differed between machines | A non-empty plan; a per-file manifest | Allowlisted zips; RECORD pruned (6) |
| `$0.00` reported, $0.03 real | The console; CloudTrail | Never call the Cost Explorer API; report gross (3) |
| Platform logs dropped at WARN | Looking for REPORT lines that never came | INFO, with the cost measured (7) |
| Terraform owned the alias | Designing the rollback | Scripts own alias moves; plan clean after four moves (15) |
| Reserved concurrency 2 throttled at 1 req/s | Per-minute CloudWatch metrics | orders and cart at 5; queue trigger capped (7, 10) |
| A dry run that wrote | Reading its own code | Only `--apply` writes (8) |
| Recovery left the bad version newest | The plan-clean health check | Recovery deletes the version it published (17) |
| The answer key sat in a table the agent reads | Building the tool that reads it | Neutral rollback reason, held to the banned-words test (17, 18) |
| Back-to-back scenarios fed each other evidence | Reading the postmortems of wrong answers | A gap between incidents longer than any tool's lookback (M7) (18) |
| A chaos run with no traffic read as a missed detection | Per-minute invocations: zero for orders | The runner logs load output, refuses without `DSQL_ENDPOINT`, and aborts before injecting if a load has died (17) |

---

## Appendix: commands worth knowing

| Command | What it does |
|---|---|
| `aws login --profile nightshift-admin` | Browser sign-in with MFA; short-lived CLI credentials. |
| `aws sts get-caller-identity` | Who am I? Needs no permissions. |
| `aws freetier get-free-tier-usage` | Billing's view of every Always Free allowance. Free. |
| `aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventSource,AttributeValue=ce.amazonaws.com` | Who called a service, when, with what parameters. Free, 90 days. |
| `aws iam simulate-principal-policy` | Would this principal be allowed this action? Answers immediately, no cache. |
| `terraform plan -out=tfplan` then `terraform apply tfplan` | Apply exactly what was reviewed. |
| `terraform apply -target=aws_iam_role_policy.ci_apply` | The one thing CI cannot change: its own permissions. |
| `terraform -chdir=terraform output -json function_versions` | The versions this apply published, for deploy.py. |
| `scripts/cost_check.py` | Every allowance, billing and live views. Exit 1 on ALERT. |
| `scripts/load.py --rate 2 --duration 1200` | Cost projection for an incident. Add `--run` to send. |
| `scripts/trace_correlation.py` | Prove one order can be followed by correlation ID. Needs the consumer on. |
| `scripts/smoke_checkout.py` | Buy something; fail if checkout does not work. |
| `scripts/replay_placed_orders.py` | Republish orders stuck in `placed`. Dry run by default. |
| `scripts/migrate.py --apply` | Apply database migrations. Dry run without `--apply`. |
| `.venv/bin/python scripts/lambda_deps.py lock` / `build` | Lock and build the dependency layer. |
| `pre-commit run --all-files` | Every hook on every file, as CI runs it. |
