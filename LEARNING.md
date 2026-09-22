# LEARNING.md

What each milestone taught, written so it can be explained out loud. Each milestone ends with 5 likely interview questions answered from our actual design.

## M0: Account safety and cost plan

### Environment setup (2026-09-18)

**Why WSL2.** WSL2 runs a real Linux kernel in a lightweight VM on Windows. Terraform, the AWS CLI, shell scripts, GitHub Actions runners, and Lambda are all Linux-first, so developing on Linux removes a whole class of "works on my machine" differences.

**Why the repo lives in `~/code/NightShift`, not `/mnt/c/...`.** `/mnt/c` is the Windows drive shared into Linux over a file-sharing protocol, so every file operation crosses the VM boundary. Git and Terraform touch thousands of files and get slow. NTFS also cannot store Linux permission bits by default: when we copied CLAUDE.md out of `/mnt/c` it arrived marked executable (`755`), and git would have recorded that wrong mode.

**What the install commands did**

| Command | What it does |
|---|---|
| `sudo apt update` | Downloads the latest package lists from each configured repository. Installs nothing. |
| `sudo apt install -y unzip jq pipx python3-venv ca-certificates gnupg wget` | `unzip`: the AWS CLI ships as a zip. `jq`: filters the JSON that AWS CLI commands return. `pipx`: installs Python command-line apps, each in its own virtual environment. `python3-venv`: creates virtual environments. `ca-certificates`: the root certificates HTTPS uses to trust servers. `gnupg`: checks signatures and converts signing keys. `wget`: downloads files. |
| AWS CLI `curl ... awscliv2.zip`, `unzip`, `sudo ./aws/install` | AWS does not publish the CLI through apt; it ships a zip with its own bundled Python. The installer puts it in `/usr/local/aws-cli` and links `/usr/local/bin/aws`. Update later with `sudo ./aws/install --update`. We need 2.32.0 or newer for `aws login`. |
| Terraform: `wget ... gpg`, then `gpg --dearmor` | Downloads HashiCorp's public signing key and converts it to the binary keyring format apt expects. |
| Terraform: `echo "deb [arch=... signed-by=...] ..." \| sudo tee ...` | Adds HashiCorp's repository. `signed-by=` means apt trusts that key ONLY for this repository, so a leaked HashiCorp key could not be used to fake packages from any other source. `dpkg --print-architecture` gives `amd64`, and the codename gives `noble` (Ubuntu 24.04). |
| `sudo apt install -y terraform` | Installs Terraform. Because it comes from a repository, `apt upgrade` keeps it patched and every download is signature-checked. |
| GitHub CLI block | Same pattern as Terraform: a signing key in `/etc/apt/keyrings`, a repository entry that uses it, then `apt install gh`. |
| `pipx ensurepath` | Adds `~/.local/bin` to your PATH so tools pipx installs can be found. |
| `pipx install pre-commit` | Installs pre-commit into its own private virtual environment, so its dependencies never clash with the project's. No sudo needed. |
| `exec bash -l` | Replaces the current shell with a fresh login shell so the PATH change takes effect. |

Verified versions: aws-cli 2.36.49, Terraform 1.16.3, gh 2.101.0, pre-commit 4.6.2.

**The idea underneath: supply chain trust.** Every tool came from the vendor's own channel. For apt packages, apt checks the vendor's signature on every install and upgrade. For the AWS CLI we trusted HTTPS from `awscli.amazonaws.com` and skipped the optional PGP signature check on the zip. That is a conscious tradeoff worth being able to name in an interview.

### AWS account setup (2026-09-18 to 2026-09-19)

**Root user vs IAM users.** The root user is the email and password you signed up with. It *is* the account: it can do everything, including a few things no policy can grant or take away (close the account, change the root email, change the support plan, restore a policy that locked everyone else out). You cannot restrict root with an IAM policy. An IAM user is an identity created *inside* the account. It starts with zero permissions and can do only what policies attached to it (or its groups) allow. Every IAM identity has an ARN like `arn:aws:iam::<ACCOUNT_ID>:user/youssef-admin`; root's ARN ends in `:root`.

**Why root gets locked away.** Because root cannot be limited, a stolen root login means total loss: the attacker can lock you out, run up charges, or delete everything, and nothing in IAM can stop them. So the rule is: root gets a strong unique password and MFA, has no access keys, and is used only for the handful of root-only tasks. Everything else, including admin work, happens as an IAM user. That way the everyday credential is one that could be revoked or restricted if it leaked. We verified this state with `aws iam get-account-summary`: `AccountMFAEnabled: 1` (root has MFA) and `AccountAccessKeysPresent: 0` (root has no keys).

**Why no access keys anywhere.** An access key (`AKIA...` plus a secret) is a long-lived password for the API. It never expires on its own, has no MFA attached, and works from anywhere on the internet. It ends up in `~/.aws/credentials`, shell history, `.env` files, CI logs, and eventually a public git commit; bots scan GitHub for `AKIA` strings within minutes of a push. The alternative is temporary credentials: they expire on their own, so a leaked copy is useless within minutes to hours. Locally we get them from `aws login`; in CI we will get them from GitHub OIDC (M1). `aws iam list-access-keys --user-name youssef-admin` returned an empty list, which is the goal.

**Why permissions go on a group, not the user.** `youssef-admin` has no policies attached directly (`list-attached-user-policies` is empty). It gets `AdministratorAccess` because it is a member of `nightshift-admins`. Reasons: (1) permissions are defined once per role of person, not copied per person; (2) removing access is one action (remove from group) and cannot leave a stray policy behind; (3) auditing is easier: "who is an admin?" is "who is in this group?". With one person it looks like ceremony, but it is the pattern that scales and the one AWS recommends.

**What MFA actually protects against.** MFA makes a login need two things: the password (something you know) and a code from your phone (something you have). It protects against the password alone being enough: phishing that captures only the password, password reuse from another site's breach, and guessing. It does NOT protect against: a phishing page that relays your code in real time (an authenticator app code is still phishable; a passkey or hardware key is not, because it checks the website's domain), malware on a machine where you are already logged in, or someone stealing credentials that were already issued after MFA (a session token or a cached refresh token). It also does nothing for access keys, which is one more reason not to have any. Our IAM user's MFA device is `arn:aws:iam::<ACCOUNT_ID>:mfa/youssef-phone`.

**How `aws login` gives short-lived credentials.** Added in AWS CLI 2.32.0. `aws login --profile nightshift-admin` opens the browser to the normal console sign-in. You sign in as the IAM user with password and MFA. The browser then hands the CLI an authorization code (an OAuth 2.0 flow, the same idea as "Sign in with Google"), and the CLI exchanges it for tokens. It saves them in `~/.aws/login/cache/`: an access token (ours expired about 15 minutes after issue), a refresh token, and a key used to prove the tokens belong to this machine. Every AWS CLI call uses them to get temporary AWS credentials, and the CLI refreshes them silently until the login session ends, then you run `aws login` again. The config file only records which identity the profile is logged in as:

```
[profile nightshift-admin]
login_session = arn:aws:iam::<ACCOUNT_ID>:user/youssef-admin
region = ca-central-1
```

There is no secret in the config file, and nothing in `~/.aws/credentials`. The cache folder does hold a refresh token, so it should be treated like a password: never copy it, commit it, or sync it.

**The console steps and what each did**

| Step | What it did and why |
|---|---|
| Root MFA | Tied root sign-in to your phone. Root is the one identity no policy can restrict, so it needs the strongest protection. |
| Activate IAM Access (Account settings, IAM user and role access to Billing information) | By default only root can open Billing pages, even for an IAM user with `AdministratorAccess`. This root-only switch lets IAM identities with the right permissions see Billing, Budgets, and Free Tier. Without it we would need root for every cost check. |
| Group `nightshift-admins` with `AdministratorAccess` | The place the permissions live. `AdministratorAccess` is an AWS managed policy: `Allow *` on `*`. Broad on purpose for a human admin in a one-person account; CI and the agent get narrow roles later. |
| User `youssef-admin`, console password, added to the group | The daily identity. Console password so `aws login` has something to sign in with. No access keys created. |
| MFA on `youssef-admin` | Same protection as root, for the identity we actually use every day. |
| Free Tier usage alert email (Billing preferences) | AWS emails when any tracked Always Free allowance passes 85%. We pointed it at the `+nightshift` address. Aurora DSQL is not tracked by these alerts, so we will watch its DPU metric ourselves. |

**The CLI checks and what each showed (2026-09-19)**

All read-only. None of them can change anything.

| Command | What it asks AWS | Our result |
|---|---|---|
| `aws sts get-caller-identity` | "Who am I right now?" STS answers for any valid credential; it needs no permissions, so it is the standard first test. | ARN `...:user/youssef-admin`. Signed in as the IAM user, not root. |
| `aws lambda get-account-settings` | This region's Lambda limits and usage. | `ConcurrentExecutions: 10`. New accounts start low; the normal default is 1,000. |
| `aws service-quotas get-service-quota --service-code lambda --quota-code L-B99A9384` | The same limit through Service Quotas, which also says whether it can be raised. `L-B99A9384` is the code for "Concurrent executions". | 10, `Adjustable: true`. |
| `aws freetier get-account-plan-state` | Which plan the account is on. | `FREE`, `ACTIVE`, $100 credits, expires 2027-03-19 03:14 UTC (2027-03-18 in Toronto). |
| `aws iam get-account-summary` | Account-wide IAM counters. | Root MFA on, zero root access keys. |
| `aws iam list-access-keys`, `list-mfa-devices`, `list-groups-for-user`, `list-attached-user-policies` | How `youssef-admin` is set up. | No keys, one MFA device, member of `nightshift-admins`, no direct policies. |
| `aws budgets describe-budgets` | Existing budgets. Budgets is a global service reached through `us-east-1`. | None yet. |

**Why concurrency 10 matters.** Concurrency is how many copies of your Lambda functions can run at the same moment, summed across all functions in the region. Lambda also requires at least 100 of it to stay unreserved, so with a limit of 10 we cannot reserve concurrency for any function at all. That blocks the throttling scenario and any per-function cap. Raising the quota is free; it is a limit, not a purchase.

### Moving `~/.aws` off the Windows drive (2026-09-19)

**What was wrong.** `~/.aws` was a symlink (a shortcut) to `/mnt/c/Users/youss/.aws`, the Windows profile folder, left over from an earlier setup in February 2026. `aws login` stores a refresh token there, and a refresh token can mint new credentials until the login session ends. Two problems: (1) the Windows drive cannot store Linux permissions, so inside WSL every file showed mode `777` (anyone can read, write, execute); (2) any Windows program running as you, including things that sync or back up your user folder, could read the token.

**What we ran**

| Command | What it does |
|---|---|
| `rm ~/.aws` | Deletes the symlink only. `rm` on a symlink removes the link, not the folder it points to. |
| `mkdir -m 700 ~/.aws` | Creates a real folder on the Linux filesystem. `700` means only the owner can read, write, or enter it. |
| `cp .../config ~/.aws/config`, `cp -r .../login ~/.aws/login` | Copies the profile settings and the current login cache, so no new sign-in was needed. |
| `chmod -R go-rwx ~/.aws`, `chmod 600 ...` | Removes all access for group (`g`) and others (`o`). `600` on a file is owner read and write only. |
| `aws sts get-caller-identity` | Confirms the CLI still works from the new location. |
| `rm -r /mnt/c/Users/youss/.aws/login` | Deletes the old copy of the token on the Windows drive, so the only copy is the protected one. |

**Reading a permission mode.** `700` is three digits: owner, group, others. Each digit adds read (4), write (2), execute (1). So `7` = 4+2+1 = everything, `6` = read and write, `0` = nothing. On a folder, "execute" means "allowed to enter it".

### Budgets and the Lambda quota request (2026-09-19)

**Budgets.** Created with `aws budgets create-budget`, using the JSON files in `bootstrap/budgets/`. A budget has two parts: the budget itself (`--budget`: name, amount, period, which costs count) and its notifications (`--notifications-with-subscribers`: when to email and who). We keep them as files, not inline JSON, so they are reviewable and in git. Budgets is a global service, so the commands go to `us-east-1` no matter where our resources live. We made these with the CLI because Terraform does not exist in the project until M1.

| Budget | Amount | Emails when |
|---|---|---|
| `nightshift-monthly-1usd` | $1.00/month | Actual spend > 100%, forecast spend > 100% |
| `nightshift-tripwire` | $0.01/month | Actual spend > 100%, meaning any charge at all |

Both set `IncludeCredit=false` and `IncludeRefund=false`. The API default counts credits and refunds. That would let the $100 of credits cancel out every charge, so net spend stays at $0 and the budget never fires. We want to hear about the charge itself, even if a credit pays it. Verified with `describe-budgets` and `describe-notifications-for-budget`. Forecast emails need several weeks of billing history before AWS can make a forecast.

**Service quota request.** `aws service-quotas request-service-quota-increase --service-code lambda --quota-code L-B99A9384 --desired-value 1000` asked AWS to raise Lambda concurrency in ca-central-1 from 10 to 1,000. It returned status `PENDING`. A person or automated check at AWS reviews it, which can take days, and they may open a support case asking about the use case. Check it with `aws service-quotas list-requested-service-quota-change-history --service-code lambda`. Quotas are limits, not purchases, so raising one costs nothing. What costs money is actually using the capacity.

**Outcome (checked 2026-09-20).** Approved. The history shows `Status: CASE_CLOSED` with `LastUpdated` 2026-09-19 01:14, about 46 minutes after the request. `CASE_CLOSED` is not the same as "granted", it only means AWS finished with the support case, so the applied value has to be read separately: `aws service-quotas get-service-quota --service-code lambda --quota-code L-B99A9384` now returns `1000.0`. Two related commands worth knowing apart: `get-service-quota` returns the value applied to this account, and `get-aws-default-service-quota` returns the default for the service, so comparing them tells you whether an account has been adjusted. Reserved concurrency is now usable, because reserving any amount requires at least 100 unreserved concurrency to remain, which was impossible at a limit of 10.

### Repo scaffold (2026-09-19)

**Commands**

| Command | What it does |
|---|---|
| `git init` | Creates the `.git` folder that holds all history. The default branch is `main` because your global git config sets `init.defaultBranch=main`. |
| `git config user.name "Youssef Khafagy"`, `git config user.email "232406487+...@users.noreply.github.com"` | Sets the author for commits in *this repo only* (no `--global`, so it's written to `.git/config`). Every commit records an email, and on a public repo anyone can read it. The GitHub noreply address still links commits to your profile without exposing your real inbox. |
| `git config core.autocrlf false` | Tells git not to convert line endings itself. `.gitattributes` controls that instead, so the rule lives in the repo and applies to everyone, not only this machine. |
| `pre-commit autoupdate` | Updates each hook's `rev` in `.pre-commit-config.yaml` to that tool's latest release tag. Pinning to a tag means everyone runs the same hook version. |
| `pre-commit install` | Writes `.git/hooks/pre-commit`, a small script git runs before every commit. If any check fails, the commit is refused. It lives in `.git/`, which is never pushed, so each fresh clone needs this once. |
| `pre-commit run --all-files` | Runs every check against the whole repo, not just staged changes. The first run downloads each tool into `~/.cache/pre-commit`. |
| Leak test in a scratch repo | Staged a fake GitHub token and ran gitleaks. It failed with rule `github-pat`, which proves the hook really blocks secrets rather than just passing everything. |

**Files**

| File | Why it exists |
|---|---|
| `.gitignore` | Files git should never track. The important ones: `.env` (real API keys); `*.tfstate` and `*.tfplan` (Terraform state and plans can contain secrets and resource details); `*.tfvars` (variable values, often secret); `.terraform/` (downloaded providers). `.terraform.lock.hcl` is deliberately committed: it pins exact provider versions and checksums, like `package-lock.json`. |
| `.gitattributes` | `* text=auto eol=lf` stores and checks out text files with Linux line endings. Windows uses CRLF (`\r\n`). A shell script with CRLF fails on Linux with a confusing `bad interpreter` error, and mixed endings make diffs noisy. |
| `.env.example` | The *names* of the settings the project needs, with empty values. Copy it to `.env` and fill it in. It has no AWS keys because credentials come from `aws login`. |
| `.pre-commit-config.yaml` | The checks listed below. |
| `README.md` | A stub for now. It fills in as milestones land, and every number in it will come from a real run. |
| `bootstrap/budgets/*.json` | The budget definitions created by CLI before Terraform exists. |

**The pre-commit checks**

| Hook | Catches |
|---|---|
| `check-added-large-files` | Accidentally committing a big file (a Lambda zip, a dataset). Git history keeps it forever, even after you delete it. |
| `check-merge-conflict` | Leftover `<<<<<<<` markers from a merge. |
| `check-yaml`, `check-json`, `check-toml` | Files that won't parse, before CI or AWS finds out. |
| `end-of-file-fixer`, `trailing-whitespace`, `mixed-line-ending` | Whitespace noise in diffs. These fix the file themselves. You re-stage it and commit again. |
| `detect-private-key` | `-----BEGIN ... PRIVATE KEY-----` blocks. |
| `gitleaks` | Hundreds of secret patterns (AWS keys, GitHub tokens, API keys) in staged changes. The same tool runs again in CI (M1), because local hooks can be skipped with `git commit --no-verify`. |
| `ruff-check`, `ruff-format` | Python lint and formatting. They skip until there is Python code. |
| `terraform-fmt` (local hook) | Terraform files not in standard format. `language: system` means it uses the Terraform we installed with apt instead of downloading another copy. |

**Why hooks and CI both.** A hook is fast feedback on your machine, but it's optional: it only exists if someone ran `pre-commit install`, and `--no-verify` skips it. CI can't be skipped. The hook stops mistakes early, and CI is the check that actually gets enforced.

### GitHub auth and publishing the repo (2026-09-19)

**Where git kept your GitHub password.** Your global git config had `credential.helper=store`. That helper saves your GitHub token in plain text in `~/.git-credentials` (a line like `https://user:TOKEN@github.com`) and hands it to git on every push. Anything that can read your home folder can read the token: a malicious npm or pip package, a backup, a stray `cat`.

| Command | What it does |
|---|---|
| `gh auth login` (you ran it) | Signs the GitHub CLI in through the browser. The token it gets is stored by `gh` in `~/.config/gh/hosts.yml`, or in the OS keyring if one is available. |
| `gh auth setup-git` | Registers `gh` as git's credential helper for `github.com`. When git needs a password, it asks `gh`, and `gh` supplies the token. One login now serves both tools. |
| `git config --global --unset-all credential.helper` | Removes the old `store` helper so git stops reading and writing the plain-text file. |
| `rm ~/.git-credentials` | Deletes the plain-text token. Deleting the file does not cancel the token itself. It stays valid on GitHub until it expires or is revoked (github.com/settings/tokens or github.com/settings/applications). |
| `gh repo create Youssef-Khafagy/NightShift --public --source . --remote origin --push` | Creates the repo on GitHub, adds it as the remote named `origin`, and pushes `main`. |

**Keeping the AWS account ID out of the repo.** AWS says account IDs are not secret, but in a public repo one tells an attacker exactly which account to target. They could try to find resources you shared by accident, send you phishing that looks legitimate, or probe resource policies that trust the account. So the docs use `<ACCOUNT_ID>`. A `pygrep` pre-commit hook (a regex check built into pre-commit, no extra tool) rejects any commit containing an ARN with a real 12-digit ID or `--account-id` followed by 12 digits. Terraform will get the ID at run time from `data "aws_caller_identity"`, so the code never needs it written down.

**Rewriting history before the first push.** The real ID was already in a local commit, and changing the file in a new commit would still leave it in history. Once pushed, history is effectively permanent: forks, clones, and GitHub's caches keep it. Before any push, rewriting is free. `git update-ref -d refs/heads/main` deletes the branch pointer, so the next commit starts a new history, while the files on disk stay as they are. `git rm -r --cached .` unstages everything without touching the files. Then the commits are made again, one group at a time. The old commits are no longer on any branch, and git deletes them during a later cleanup. After a push you would need a history-rewriting tool plus a force push, and you would still have to assume the data leaked.

### Keeping the repo private, and what it costs you (2026-09-20)

The repo was created private. That is a product decision, not a technical one, but it changes two things in GitHub Actions, so it is worth knowing before M1 is designed around the wrong assumption.

**Environments and approval gates.** A GitHub *environment* is a named deployment target (`production`, `staging`) that a workflow job can reference. Its useful feature here is a protection rule called *required reviewers*: the job pauses and a named human has to press Approve before it runs. That is exactly the shape of "show me the Terraform plan, then let me decide". The catch is the plan tiers. GitHub's docs say "Users with GitHub Free plans can only configure environments for public repositories", and required reviewers on a *private* repo need Enterprise, not just Pro or Team. So on a private repo with a free account, the environment gate does not exist at any price we will pay.

**The replacement: `workflow_dispatch`.** A workflow with a `workflow_dispatch` trigger only runs when someone presses "Run workflow" in the Actions tab (or calls the API). So the pipeline splits in two. Plan runs automatically on every pull request and posts the Terraform plan. Apply is a separate manually triggered workflow. The human pressing the button *is* the approval, and it is still auditable: GitHub records who triggered every run. What is lost compared to a real environment gate is the coupling. The gate is not attached to a specific reviewed plan, so nothing stops someone from dispatching apply without reading the plan first. With one maintainer that is an acceptable trade, and the fix when the repo goes public is to move the apply job behind an environment.

**Actions minutes.** Public repos get GitHub-hosted standard runners with no minute cap. Private repos draw on the Free plan allowance of 2,000 minutes and 500 MB of artifact storage per month. Both are $0, but the private one has a ceiling, so CI should stay quick and should not run on every push to every branch.

**Why the docs are still written as if the repo were public.** No account ID, the noreply commit email, secret scanning in hooks and CI. Making a repo public is one click, and at that moment the whole history becomes readable at once. Nothing about "it is private right now" makes a committed secret safe, because the commit outlives the setting.

### M0 concepts and interview questions

**The idea M0 is built on: put the limits in place before there is anything to limit.** Nothing was created in AWS except billing alarms. Every guard rail (budgets, MFA, no access keys, a forbidden-service list, a concurrency cap, secret scanning) exists before the first resource. That ordering is the point. A cost guard added after the bill arrives is not a guard, it is a receipt.

**Layers of credential risk.** Root can do anything including closing the account and cannot be restricted, so it gets MFA and is then left alone. An IAM user with MFA is the daily identity, but a user is still a long-lived identity, so it holds no access keys. `aws login` exchanges an MFA-backed browser sign-in for credentials that expire, which is why the session in this repo went stale between sessions. That expiry is the feature: a leaked file from yesterday is worthless today.

**Free tier is not one thing.** Always Free allowances renew every month forever. Twelve-month free tier and the $100 of Free plan credits both run out. The design may only depend on the first kind. Credits are treated as a safety net for a mistake, never as budget.

**Budgets are a backstop, not a control.** Billing data lags by hours, so a budget email tells you a charge already happened. Nothing in AWS stops spending when a budget fires. The real controls are the choices upstream: only Always Free services, no service that bills per hour of existence, traffic generated on demand instead of continuously, and a destroy command.

**Quotas cut the blast radius.** The account's Lambda concurrent executions limit is 10. That is small enough that a runaway loop cannot invoke thousands of functions in parallel. It is also low enough to block reserved concurrency, which needs at least 100 unreserved, which is why the increase to 1,000 was requested. A quota is a ceiling, not a purchase: raising it costs nothing, using it costs money.

**Two places to catch a secret, for two different reasons.** Pre-commit hooks are fast and local, so they fail in two seconds instead of two minutes. But they live in `.git/hooks`, which is never cloned, and `git commit --no-verify` skips them. CI cannot be skipped. The hook is convenience, CI is enforcement, and you want both because the cost of the two outcomes is not symmetric: a blocked commit costs a minute, a pushed secret has to be treated as leaked and rotated.

**Interview questions**

**1. You claim this project runs on AWS for $0.00 a month. How do you actually guarantee that, and how would you know if you were wrong?**

Nothing is guaranteed by a single control, so it is layered. Design first: only services with an Always Free allowance, checked against the pricing page and written into COST.md with the allowance, the projected usage and the headroom. An explicit forbidden list for anything that bills for merely existing, which is most of the expensive ones: EC2, NAT Gateway, load balancers, RDS, Fargate, OpenSearch, Secrets Manager, customer managed KMS keys. Nothing runs continuously, traffic is generated locally on demand and rate-capped, and there is a pause command that takes idle usage to near zero plus a destroy command. Then detection: a $1 monthly budget on actual and forecast spend, and a $0.01 tripwire on actual spend, because in a design that is supposed to be entirely free, the first cent is the signal, not the first dollar. Both budgets exclude credits so a charge is visible even when credits absorb it. I would know I was wrong from the tripwire email, and because billing data lags by hours, the budget is the backstop and the design constraints are the actual control.

**2. Why are there no AWS access keys on your laptop?**

An access key is a long-lived credential with no expiry that works from anywhere in the world. If it ends up in a commit, a backup, a screen share or a malicious dependency that reads `~/.aws`, the attacker has my permissions until I notice and rotate. Instead the IAM user has MFA and no keys, and `aws login` performs a browser sign-in that returns short-lived credentials cached on disk. They expire, so a stolen copy has a short useful life. The usual best answer is IAM Identity Center, and I would use it at work, but it requires AWS Organizations, and joining an Organization auto-upgrades an account off the Free plan, which would let this account be charged. So the constraint picked the design, and I can say exactly what I gave up.

**3. You have a $1 budget and a $0.01 budget. Isn't the $1 one enough?**

They answer different questions. The $1 budget watches actual and forecast spend, so it catches a slow drift: something small billing every day that would add up. The $0.01 budget on actual spend answers a yes or no question instead: has anything charged me at all? For a system designed to be entirely inside Always Free, any non-zero charge means a design error, not a usage spike, and I want to hear about it the day it starts rather than when it approaches a dollar. The $1 budget also covers the case where the tripwire fires and I misjudge it.

**4. You set `IncludeCredit=false` on both budgets. What does that flag do and why was the default wrong for you?**

By default a budget measures net cost, so credits and refunds are subtracted before the number is compared to the threshold. This account has $100 of Free plan credits. With the default, a service could charge me every day and the credits would absorb it, net spend would stay at $0, and neither budget would ever fire. I would find out when the credits ran out. `IncludeCredit=false` makes the budget measure the gross charge, so it fires on the charge itself and the credits are what they should be: a safety net behind the alarm, not a way to silence it. `IncludeRefund=false` is the same reasoning for refunds.

**5. Your pre-commit hooks already run gitleaks. Why bother running it again in CI?**

Because a pre-commit hook is not a security control, it is a convenience. It only exists if someone ran `pre-commit install`, it lives in `.git/hooks` which is never cloned or pushed, and anyone can bypass it with `git commit --no-verify`. It is there because a two-second local failure is much cheaper than a two-minute CI failure. CI runs on the server on every pull request and cannot be skipped by the person making the change, so that is where the rule is actually enforced. The asymmetry justifies the duplication: a false block costs me a minute, while a secret that reaches a remote has to be assumed leaked and rotated, even in a private repo, because history is effectively permanent once pushed. The same logic covers the hook that blocks the AWS account ID.

## M1: Terraform, GitHub OIDC, and the first Lambda

### What M1 actually is (2026-09-20)

Eleven AWS resources and one S3 bucket. None of them is interesting on its own. The point is the path: a change to a file in this repo can reach a running Lambda without anyone typing an AWS command, without a stored password, and without skipping a review.

| Piece | Resource | Why it exists |
|---|---|---|
| State | S3 bucket `nightshift-tfstate-ca-central-1-2f0ad894` | Terraform remembers what it built |
| Trust | `aws_iam_openid_connect_provider.github` | AWS agrees to believe tokens GitHub signs |
| Read access | `nightshift-ci-plan` role + ReadOnlyAccess | Pull requests can plan |
| Write access | `nightshift-ci-apply` role + scoped inline policy | Only a manual run on main can apply |
| The workload | `nightshift-hello` function, `live` alias, function URL, exec role, log policy, log group | Something to deploy |

### Terraform state, and why the bucket is not Terraform's

**What state is.** Terraform is not reading your AWS account and diffing it against your files. It keeps a JSON file that records every resource it created and the real ID AWS gave it. `plan` compares three things: your configuration, that state file, and a refresh of the real resources. Without state, Terraform has no idea that `aws_lambda_function.this` in your file is the function called `nightshift-hello` in AWS, and it would try to create a second one.

**Why the state file goes in S3.** On your laptop, state is a single file that only you have. If it is lost, Terraform forgets everything it owns and the next apply tries to recreate resources that already exist. If CI is going to run Terraform too, CI and your laptop need to read and write the same state. S3 gives both: durable, versioned, and reachable by an IAM role.

**Locking.** Two applies at once would both read the old state, both act, and one would overwrite the other's record. Terraform prevents that with a lock. The old way was a DynamoDB table holding a lock row. The current way, and the only way in new code, is `use_lockfile = true`: Terraform writes a small `terraform.tfstate.tflock` object next to the state and relies on S3 conditional writes, which are atomic, so exactly one writer wins. DynamoDB locking is deprecated and will be removed. This also saves a table and its provisioned capacity, which matters when the whole project is on a capacity budget.

**The chicken and the egg.** The backend has to exist before `terraform init` can use it, so Terraform cannot create the bucket that stores Terraform's state. `bootstrap/state/create-state-bucket.sh` creates it with the AWS CLI, once. There is a second reason to keep it outside Terraform: if Terraform managed the bucket, `terraform destroy` would try to delete the bucket it is writing its state to, halfway through. The script defaults to a dry run and prints every command before `--apply` runs it, the same pattern as the M0 budget files.

**What the bucket has turned on, and why**

| Setting | Reason |
|---|---|
| Versioning | State is the one file you cannot regenerate. A bad apply or a truncated write is recoverable from the previous version. |
| Block all public access | State lists every resource, its configuration, and sometimes secrets. |
| SSE-S3 (AES256) | Encryption at rest, free. A customer managed KMS key would cost money and is on the forbidden list. |
| `BucketOwnerEnforced` ownership | Disables ACLs entirely, so access is decided only by IAM and the bucket policy. One mechanism instead of two. |
| Deny non-TLS bucket policy | `aws:SecureTransport = false` is refused, so state never travels in plaintext. |
| Lifecycle: expire noncurrent versions after 30 days | Versioning without expiry grows forever and is billed forever. |
| Lifecycle: abort incomplete multipart uploads after 7 days | A failed large upload leaves parts that are billed until aborted. This is the classic invisible S3 charge. |

### GitHub OIDC: logging in without a password

**The problem.** CI needs AWS credentials. The obvious answer is to create an IAM user, generate an access key, and paste it into GitHub secrets. That key never expires, works from anywhere, and now exists in at least two places. If it leaks, you find out later.

**How OIDC replaces it.** OpenID Connect is a standard for one system to prove an identity to another. The flow here:

1. A workflow job asks GitHub for an OIDC token. That is what `permissions: id-token: write` grants.
2. GitHub mints a short-lived JWT describing the run: which repo, which branch or event, which workflow. It signs it with GitHub's private key.
3. The job calls `sts:AssumeRoleWithWebIdentity` with that token.
4. AWS fetches GitHub's public keys, checks the signature, then checks the token's claims against the role's trust policy.
5. STS returns temporary credentials that expire in an hour.

Nothing is stored. The credential is created per run and dies with it.

**The two claims that matter.** `aud` (audience) is who the token is for, `sts.amazonaws.com`. `sub` (subject) is who the token is about. The subject is the real access control, and getting it wrong is how people accidentally let any repository on GitHub assume their role.

**Immutable subject claims.** GitHub changed this format for repositories created after 2026-07-15, which includes ours (created 2026-09-19). The old subject was `repo:OWNER/REPO:ref:refs/heads/main`. The new one is:

```
repo:Youssef-Khafagy@232406487/NightShift@1376738088:ref:refs/heads/main
```

The numbers are the account ID and the repository ID. They exist because names can be given up. If you rename your account or delete the repo, someone else can register the old name, and a trust policy matching the old string would then trust their repository. The numeric IDs are never reissued. The old format does not validate for these repositories at all, so a guide written before mid-2026 produces a role nobody can assume. Look the IDs up with:

```
gh api users/Youssef-Khafagy --jq .id
gh api repos/Youssef-Khafagy/NightShift --jq .id
```

**Why the conditions use StringEquals, not StringLike.** It is tempting to write `repo:owner/repo:*`. That matches every branch, every tag, and every pull request, including `pull_request` runs from forks. The plan role lists exactly two subjects (`:pull_request` and `:ref:refs/heads/main`); the apply role lists exactly one (`:ref:refs/heads/main`). A trust policy with no subject condition at all would let any GitHub repository in the world assume the role, which is a well documented way people have lost accounts.

**No thumbprint.** Older guides pin a certificate thumbprint like `6938fd4d...`. AWS now verifies GitHub's endpoint against its own library of trusted root certificate authorities and only falls back to thumbprints for providers using a private CA. When no thumbprint is supplied at creation, IAM retrieves one itself. Pinning one means every GitHub certificate rotation breaks deploys.

### IAM: two roles, and why one is broad on purpose

**Plan is read-only and uses the AWS managed `ReadOnlyAccess` policy.** This is deliberately broad, and it is worth being able to defend. A plan has to read every resource type the configuration touches, and that set grows with every milestone, so a hand-written read policy means a CI failure every time a new service appears. A role that cannot write cannot break anything. The real risk of a broad read role is data exposure, and everything in this account is synthetic. The role that can actually change the account is scoped by hand.

**Apply is scoped by resource, not by service.** Lambda permissions apply only to `function:nightshift-*`, IAM permissions only to `role/nightshift-*`, logs only to `/aws/lambda/nightshift-*`, and S3 only to this project's prefix in the state bucket. The Lambda actions are listed one by one rather than `lambda:*`, so that a dangerous action added to the Lambda API next year does not silently land in this role.

**Explicit deny beats allow, always.** IAM evaluates every applicable policy: if any statement denies, the request fails, no matter how many allow it. That makes deny the right tool for "never, under any circumstances":

| Deny | Reason |
|---|---|
| Mutating the two CI roles and the OIDC provider | CI must not be able to widen its own permissions. Changing CI's IAM is a local apply by the owner. Get and List are still allowed, because Terraform reads these on every refresh. |
| `iam:CreateUser`, `iam:CreateAccessKey`, login profiles | These create identities that outlive the workflow run. A compromised pipeline's first move is usually to mint a credential it can come back with. |
| `organizations:*`, `account:*` | Joining an Organization auto-upgrades the account to the Paid plan, which removes the "cannot be charged" guarantee. |
| `budgets:ModifyBudget`, `budgets:DeleteBudget`, `ce:*` | The cost alarms are the safety net. Nothing automated may touch them. |
| `s3:DeleteBucket` and bucket-level settings on the state bucket | Terraform must never be able to delete or unprotect the bucket holding its own state. |

### Versions, aliases, and why nothing points at $LATEST

Every Lambda function has an unpublished `$LATEST` that changes whenever you update the code. `publish = true` makes Terraform also create an immutable numbered version on every code change. Version 1 is frozen forever.

An alias is a named pointer to a version. `live` points at 1 today. A deploy is "publish version 2, move the alias". A rollback is "move the alias back to 1", which is one API call, does not rebuild anything, and is why M3 can have a rollback script at all. If callers invoked `$LATEST`, there would be nothing to roll back to and no way to say which code served a given request. The function's version appears in every log line for the same reason.

Invoking the alias looks like this. `ExecutedVersion` is the proof that the alias resolved:

```
aws lambda invoke --function-name nightshift-hello:live \
  --payload '{"headers":{"x-correlation-id":"m1-smoke-test"}}' out.json
{"status": 200, "version": "1", "error": null}
```

**Function URLs.** A function URL is an HTTPS endpoint Lambda manages itself. It is free, where API Gateway and any load balancer are not, which is the whole reason it is in this design. `authorization_type = "AWS_IAM"` means every request must be SigV4-signed by a principal allowed to invoke this alias. The alternative, `NONE`, is a public endpoint anyone can invoke, which on a project whose first rule is $0 would be a stranger spending your free tier. Verified both ways:

```
curl $URL                          -> http 403
curl --aws-sigv4 ... $URL          -> http 200
```

**arm64.** Graviton costs less per GB-second than x86_64 beyond the free tier. Python has no compiled dependencies in M1, so there is no portability cost yet.

> **Correction, 2026-09-20.** The original version of this note said the lower arm64 price makes the free allowance "go further". That is wrong, and it is the kind of claim an interviewer would push on. The Lambda free tier is 1 million requests and 400,000 GB-seconds per month and is **identical for both architectures**, re-verified on the pricing page. A GB-second is a GB-second; a lower price per GB-second buys nothing extra inside an allowance denominated in GB-seconds. The real arguments for arm64 are that it is cheaper once the free tier is exhausted, and that better price-performance can mean the same work finishes in fewer GB-seconds. The second one is a claim about speed, and this project has not measured it. The "no portability cost" half also has a shelf life: from M2a the functions depend on psycopg, which means every wheel has to be an aarch64 build.

**Reserved concurrency of 2.** Reserved concurrency is a hard ceiling on how many copies of this function can run at once. It is a blast radius control: a retry storm or a loop cannot spend the account's whole free allowance on one function. It is also why the M0 quota increase mattered, since reserving anything requires at least 100 unreserved concurrency to remain.

**The log group is created by Terraform, not by Lambda.** If Lambda creates the group on first invocation, it is created with "never expire", and CloudWatch's free 5 GB per month covers storage as well as ingestion. Creating it in Terraform sets three-day retention from the start. It also means the execution role does not need `logs:CreateLogGroup`, which the AWS managed `AWSLambdaBasicExecutionRole` grants across the entire account. The role here can only write to its own group.

**Structured logs for free.** `logging_config { log_format = "JSON" }` makes the runtime emit each log record as JSON. Combined with `logger.info("...", extra={...})`, fields land at the top level where Logs Insights can filter on them, with no library:

```json
{"timestamp": "2026-09-20T22:27:18Z", "level": "INFO", "message": "handled request",
 "requestId": "c4407763-...", "service": "hello", "correlation_id": "m1-smoke-test",
 "function_version": "1"}
```

### The pipeline

**Two workflows, because plan and apply have different risk.**

`ci.yml` runs on pull requests. Job one runs `pre-commit run --all-files`, which is the same config and the same pinned versions as the local git hook. One source of truth, and this copy cannot be skipped with `--no-verify`. Job two assumes the plan role and runs `terraform validate`, `tflint`, a `trivy config` scan, and `terraform plan`.

`apply.yml` runs only on `workflow_dispatch`, only on main, and only when the person triggering it types `apply` into the confirmation box. That is the approval gate. GitHub environments with required reviewers would be the textbook answer, but they do not exist on GitHub Free for a private repo. GitHub records who triggered every run, so it is still auditable. What is lost is the coupling between a reviewed plan and the apply, which is a real gap with one maintainer and the reason M3 onwards keeps a deployments table.

**Details worth knowing**

| Detail | Reason |
|---|---|
| `permissions: {}` at the top, then per job | The default `GITHUB_TOKEN` can be broad. Starting from nothing and granting per job means a hostile dependency in one job cannot push code or create releases. |
| `plan -lock=false` in CI | The plan role cannot write to the state bucket, and writing a lock object is a write. A read-only plan does not need a lock. |
| Only the plan counts are posted as a PR comment | The full plan contains ARNs, which contain the account ID. GitHub masks secrets in job logs, so `secrets.AWS_ACCOUNT_ID` is redacted there, but bot comments are not masked and PR history survives the repo going public. |
| `AWS_ACCOUNT_ID` as a secret, role names in the clear | The workflows build `arn:aws:iam::***:role/nightshift-ci-plan`. The account ID stays out of the repo and out of the logs; the role names stay readable. |
| `concurrency` group on apply, `cancel-in-progress: false` | Never two applies at once, and never cancel one halfway through. |
| Plan file passed to apply, not a bare `terraform apply` | `apply` on its own re-plans and applies whatever it finds, which may differ from what was reviewed. Applying a saved plan applies exactly that plan or fails. |

**Why the first apply was local.** CI cannot create the roles CI needs in order to run. The owner ran `terraform apply` once from the laptop with the admin profile, and everything after that goes through the pipeline.

### Commands used in M1

| Command | What it does |
|---|---|
| `./bootstrap/state/create-state-bucket.sh` | Prints every AWS call it would make and exits. Default is dry run on purpose. |
| `./bootstrap/state/create-state-bucket.sh --apply` | Creates and configures the state bucket. Safe to re-run: every step is idempotent. |
| `terraform init` | Downloads providers, writes `.terraform.lock.hcl`, and connects to the backend. Needed again whenever providers or backend config change. |
| `terraform validate` | Checks syntax and internal consistency. No AWS calls, no credentials needed. |
| `terraform fmt -recursive` | Canonical formatting. Also a pre-commit hook, so a badly formatted file cannot be committed. |
| `terraform plan -out=tfplan` | Shows what would change and saves that exact plan to a file. |
| `terraform apply tfplan` | Applies the saved plan, or fails if reality moved. |
| `terraform output -raw NAME` | Prints one output without quotes, for use in shell variables. |
| `tflint --recursive` | Lints Terraform for things `validate` does not catch: deprecated syntax, unused declarations, invalid AWS values. Caught a real one here: the module had no `required_version` or provider constraints. |
| `trivy config terraform` | Scans the Terraform for insecure configuration. Clean at every severity after the IAM actions were listed out instead of `lambda:*`. |
| `gh secret set AWS_ACCOUNT_ID` | Stores the account ID as a repository secret so workflows can build ARNs and GitHub masks it in logs. |
| `curl --aws-sigv4 "aws:amz:ca-central-1:lambda" --user "$KEY:$SECRET" -H "x-amz-security-token: $TOKEN"` | Signs a request the way the AWS SDKs do, to test an `AWS_IAM` function URL from the shell. |

### How to verify M1

```
cd terraform && terraform plan          # expect: No changes
aws lambda invoke --function-name nightshift-hello:live --payload '{}' out.json
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/nightshift-hello \
  --query 'logGroups[0].retentionInDays'                      # expect: 3
curl "$(terraform output -raw hello_function_url)"            # expect: http 403
aws s3 ls s3://nightshift-tfstate-ca-central-1-2f0ad894/nightshift/
```

### Cost of M1

$0.00 measurable. IAM roles, policies, the OIDC provider, Lambda aliases and function URLs are all free. The function is not invoked unless something invokes it, and 1M requests plus 400,000 GB-seconds per month are free. Logs are on three-day retention inside the 5 GB allowance. The one real charge is the state bucket: under 100 KB of state, a handful of requests per run, so roughly $0.001 a month and at worst $0.02. Actions minutes come out of the Free plan's 2,000 per month for private repos; a full CI run is a few minutes.

### What the first CI run caught

The pipeline's first real job found a bug on its first try, which is the best argument for building it before the interesting code.

The plan job should have reported no changes, because the infrastructure had just been applied from the same commit. It reported `Plan: 0 to add, 2 to change, 0 to destroy`, and the difference was `source_code_hash` on the Lambda:

```
~ source_code_hash = "5SG0UQSTGUG74L1nbQanvYVRPTPGk+VYGZos1vR4ykA="
                  -> "WtqeoBI37UPsSWbApdxX4fgljLqp6avYm+PC6RrmcO8="
```

Same commit, same Terraform, different zip. The cause: the module used `archive_file` with `source_dir`, which zips whatever is in that directory. Running the handler locally to smoke-test it had created `src/hello/__pycache__/app.cpython-312.pyc`, and that file went into the deployed artifact. A clean CI checkout has no `__pycache__`, so its zip differed.

Two separate problems in one symptom:

1. **A junk file shipped to production.** Version 1 of the function contains a bytecode file compiled by the local Python 3.12 for a runtime that runs 3.14. Harmless here. In a project with a virtualenv, local credentials or a `.env` in the source directory, it would not be.
2. **Drift on every run.** The artifact depended on the machine, so CI would have wanted to redeploy the function forever, and "the plan is not empty" would have stopped meaning anything.

`.gitignore` did not help, because `archive_file` reads the filesystem, not git.

**The fix is an allowlist, not an exclude list.** `excludes = ["__pycache__/**"]` would have fixed this one case and nothing else. Instead the module lists the files it wants:

```hcl
dynamic "source" {
  for_each = toset(concat([
    for f in fileset(var.source_dir, "**/*.py") : f
  ], var.extra_files))

  content {
    content  = file("${var.source_dir}/${source.value}")
    filename = source.value
  }
}
```

Now the artifact contains exactly the committed Python files, with a fixed 1980 timestamp, so the same commit produces the same bytes on any machine. It is the same principle as the IAM policy two sections up: name what is allowed, because a denylist only covers the cases you thought of.

Two things that looked like fixes and were not. File modification time is not the cause: `archive_file` already normalizes it, confirmed by backdating the source file and getting an identical plan. `output_file_mode = "0644"` is a no-op with content-based sources, confirmed by comparing the checksum with and without it, so it was removed rather than left in with a comment claiming otherwise.

**The side benefit.** The job log printed the plan as `arn:aws:lambda:ca-central-1:***:function:nightshift-hello:1`. GitHub masked the account ID because it is stored as a repository secret, which was the point of storing it that way.

### What the first CI apply caught

The first run of the apply workflow failed too, for a different and more instructive reason:

```
Error: listing tags for CloudWatch Logs Log Group
(arn:aws:logs:ca-central-1:***:log-group:/aws/lambda/nightshift-hello):
AccessDeniedException: User: arn:aws:sts::***:assumed-role/nightshift-ci-apply/...
is not authorized to perform: logs:ListTagsForResource
```

**The cause: one resource, two ARN shapes.** CloudWatch Logs writes a log group's ARN two ways. `arn:aws:logs:REGION:ACCOUNT:log-group:NAME` refers to the group itself, and `arn:aws:logs:REGION:ACCOUNT:log-group:NAME:*` reaches the log streams inside it. Which one an action expects depends on the action. `logs:CreateLogStream` and `logs:PutLogEvents` want the `:*` form; `logs:ListTagsForResource` and `logs:PutRetentionPolicy` want the bare form. The policy only had the `:*` form, so Terraform could create the group but not read its tags during refresh. The fix lists both.

This is the cost of hand-scoped IAM, and it is worth being honest about it in an interview: least privilege is not free, and the bill is paid in exactly this kind of failure. The trade was made deliberately for the role that can write, and deliberately not made for the role that can only read.

**The deny worked, and it was inconvenient in exactly the right way.** The fix is a change to `aws_iam_role_policy.ci_apply`, which is the apply role's own policy. The apply role is explicitly denied `iam:PutRolePolicy` on itself, so CI could not have deployed this fix even if asked. It had to be applied from the laptop:

```
terraform apply -target=aws_iam_role_policy.ci_apply
```

`-target` restricts an apply to one resource and its dependencies. Terraform prints a warning every time, because a targeted apply leaves the rest of the configuration unapplied and is easy to misuse as a way to avoid reading a plan. Here it is the right tool: the rule is that CI IAM changes are local and everything else goes through the pipeline, and `-target` is what expresses that rule in one command.

**End to end, this is what the pipeline then did.** A `workflow_dispatch` run with the confirmation word assumed `nightshift-ci-apply`, planned, applied, published version 2 and moved the `live` alias to it. Verified afterwards:

```
aws lambda invoke --function-name nightshift-hello:live ...
{"status": 200, "version": "2", "error": null}

aws lambda get-function --function-name nightshift-hello:live --query Code.Location
-> downloaded zip contains exactly ['app.py']

terraform plan
-> no differences
```

The negative test matters as much as the positive one. Triggering the same workflow with `confirm=nope` failed at the first step, before checkout and before any AWS credentials were requested:

```
Check the confirmation word: failure
Run actions/checkout@v7: skipped
Assume the apply role: skipped
```

### M1 concepts and interview questions

**1. Walk me through how your CI authenticates to AWS.**

GitHub Actions uses OIDC, so there are no AWS keys anywhere. The job asks GitHub for a short-lived signed JWT that describes the run, then calls `sts:AssumeRoleWithWebIdentity` with it. AWS validates the signature against GitHub's public keys and checks the token's claims against the role's trust policy: the audience must be `sts.amazonaws.com`, and the subject must exactly match one of the subjects I listed. STS returns credentials that expire in an hour. The subject condition is the actual access control, and my repository was created after July 2026 so it uses GitHub's immutable subject format, which embeds the numeric owner and repository IDs instead of names. That matters because names can be released and re-registered by someone else, while the IDs are never reused. I used `StringEquals` with an explicit list rather than a wildcard, because `repo:owner/repo:*` would also match every branch and every fork's pull request.

**2. Your plan role has the AWS managed ReadOnlyAccess policy. Isn't that the opposite of least privilege?**

It is broad, and it was a deliberate trade. A plan has to read every resource type in the configuration, and that set grows every milestone, so a hand-written read policy turns into a CI failure every time I add a service. What I weighed is that the role cannot write, so it cannot change or destroy anything; the remaining risk is reading data, and everything in this account is synthetic test data. The role that can actually change the account is the apply role, and that one is scoped by hand: Lambda only on `function:nightshift-*`, IAM only on `role/nightshift-*`, logs only on this project's log groups, S3 only on this project's state prefix, with the Lambda actions enumerated instead of `lambda:*`. If this were a real production account with customer data, I would scope the read role too and accept the maintenance.

**3. What stops your pipeline from giving itself more permissions?**

An explicit deny in the apply role covering every mutating IAM action on the two CI roles and on the OIDC provider. In IAM, an explicit deny always wins over any allow, so even though the role can create and modify roles named `nightshift-*`, those specific ARNs are carved back out. Get and List are still allowed because Terraform reads those resources on every refresh. The practical effect is that changing CI's own permissions cannot be done by CI: it has to be a local apply by me. The same deny statement blocks creating IAM users and access keys, anything under `organizations:` or `account:`, and modifying the budgets, because those are the account's cost and blast-radius guards.

**4. How do you deploy and roll back a Lambda?**

Every apply publishes an immutable numbered version, and a `live` alias points at one of them. Nothing ever invokes `$LATEST`. A deploy publishes a new version and moves the alias; a rollback moves the alias back to the previous version, which is a single API call and does not rebuild or redeploy anything. That is what makes a rollback fast enough to be the first thing an on-call agent tries, which is the entire premise of this project. It also gives me attribution: the function version is in every log line, so I can say which code served a given request. The alias is also what the function URL is attached to, so traffic follows the alias rather than a version.

**5. Terraform state is in S3. What could go wrong, and what did you do about it?**

Three things. Concurrent writes, handled with `use_lockfile = true`, which is S3 native locking using conditional writes; the old DynamoDB lock table is deprecated. Loss or corruption, handled with bucket versioning, so a bad state file can be rolled back to the previous version, plus a lifecycle rule that expires old versions after 30 days so it does not grow and bill forever. Exposure, since state contains every resource and sometimes secrets, handled with all public access blocked, ACLs disabled via BucketOwnerEnforced, default encryption, and a bucket policy that denies any request not over TLS. The bucket is created by a script rather than by Terraform for two reasons: Terraform cannot create its own backend before `init`, and if it managed the bucket then `terraform destroy` would try to delete the bucket it is writing state to.

## M2a: the store's data plane

### Cost facts verified before building (2026-09-20)

Nothing was created until COST.md carried today's numbers, per the cost rules. The three re-checks that mattered:

Aurora DSQL gives 100,000 DPUs and 1 GB of storage free every month, then charges $8 per million DPUs and $0.33 per GB-month. An idle cluster scales to zero and costs nothing beyond storage, so there is no "switch the database off" step to build later.

A DPU is not a unit of time. AWS counts query compute, the I/O to read and write storage, and change data capture streaming. Three things follow. A checkout that scans a table to change one row costs far more than one that reads by primary key, so indexing is a cost control here and not only a latency control. Every retried transaction bills its own DPU, which makes the hot-row contention scenario a cost event as well as a latency event. And synthetic orders have to be pruned between benchmark passes to stay under the 1 GB storage allowance.

Two earlier claims turned out to be wrong and were corrected rather than quietly edited away. Lambda's X-Ray sampling rate is fixed at one request per second plus 5% of the rest and cannot be configured, so "explicit sampling rate" was never a lever this project had. And the Lambda free tier is 400,000 GB-seconds for both x86 and arm64, so arm64's lower price per GB-second buys nothing inside the free tier. See the correction note in the M1 section.

### Packaging: why a layer, and why a lock file

M1's function was a single file with no dependencies. M2a needs `psycopg` to talk to DSQL and Powertools for logging and metrics, which raises three questions that a hello-world never asks.

**Where do dependencies live?** A Lambda layer is a zip that Lambda unpacks to `/opt` and puts on the import path. One shared layer serves all four services, so a deploy uploads only the handful of kilobytes of code that actually changed instead of 8 MB of wheels four times over. The layer is ours, built in our account, which also means no third-party layer ARN carrying someone else's account ID has to be smuggled past the pre-commit hook.

**Which wheels?** Lambda runs Amazon Linux 2023 on arm64, and the laptop building the zip is x86 Ubuntu running Python 3.12 while the target is Python 3.14. pip can cross-build for another platform without Docker, as long as it is forbidden from compiling anything:

```
pip download -r lambda-deps.in \
  --only-binary=:all: \
  --python-version 3.14 --implementation cp \
  --platform manylinux_2_28_aarch64 \
  --platform manylinux2014_aarch64
```

`--only-binary=:all:` is what makes this safe. Without it pip would happily download a source tarball and build it against the local glibc and CPython, producing a wheel that cannot load on the runtime. The failure would appear at the first invocation, not at build time.

The two platform tags are not interchangeable and this is the part that bites. `manylinux2014` means glibc 2.17; `manylinux_2_28` means glibc 2.28. Amazon Linux 2023 ships glibc 2.34, so it satisfies both, but a wheel tagged `manylinux_2_28_aarch64` is invisible to pip if you only pass `--platform manylinux2014_aarch64`. psycopg publishes exactly that tag, so asking for only the older tag would have failed to resolve at all. Listing both lets pip take the best match each package offers.

**Which exact bytes?** Pinning `psycopg==3.3.6` pins a version, not a file. `scripts/lambda_deps.py lock` resolves the full transitive set for the target platform and records a sha256 for every wheel, and the build installs with `--require-hashes`. This is supply chain hygiene, and it is the same instinct as gitleaks in the pre-commit hook: make the bad outcome fail loudly rather than hoping it does not happen.

It also buys determinism for free. Identical wheels in means identical files out, which is half of what M1's `__pycache__` bug was about.

### Making the zip itself reproducible

The other half is the zip. A zip entry records a modification time and a Unix mode, and both come from the filesystem, so the same files produce different bytes on different machines. The script writes the archive by hand: entries sorted by name, every timestamp fixed at the zip epoch of 1980-01-01, every mode fixed at 0644.

0644 is the right mode for the bundled `libpq` shared objects too, which is worth knowing because it looks wrong. `dlopen` needs a shared library to be readable and mappable, not executable. That is why `/usr/lib/*.so` on any Linux system is 0644 and not 0755. The execute bit matters for `execve`, which is not how a `.so` is used.

Verified by building twice:

```
build 1: f9bd9f5827cad5709ce3eaa91b68806d523e12313b33b51c3d06c71e4f1ee428
build 2: f9bd9f5827cad5709ce3eaa91b68806d523e12313b33b51c3d06c71e4f1ee428
```

And by inspecting the archive: 469 entries, all under `python/`, exactly one distinct timestamp, exactly one distinct mode, zero `__pycache__` or `.pyc` entries.

Two builds on the same machine agreeing is the weak version of the claim. The one that matters is a CI runner agreeing with a laptop, and that is the next section.

### Chasing a one-file difference across two machines

The first CI run of the layer disagreed with the local build:

```
local:  f9bd9f5827cad5709ce3eaa91b68806d523e12313b33b51c3d06c71e4f1ee428
runner: fd376d7a8fb71c0f32f329015b3548e55103d43d5b5b80a0ad158738e72ee6ec
```

Both reported 28.2 MiB unpacked and 7.7 MiB compressed. Identical sizes with different bytes looked like a compression difference, since zlib's deflate output is not guaranteed identical across versions and nothing in a zip records which implementation wrote it. That hypothesis was wrong, and the way it was wrong is the useful part.

**A single digest cannot tell you where to look.** "The artifact differs" has two very different causes: the files installed differ, or the same files were archived differently. So the build gained a second digest covering file names and contents only, independent of how the archive is written. The next run answered the question immediately: the content digests differed too, so the installed files themselves were not the same and the archiver was never implicated.

**A digest still cannot tell you which file.** 468 files, one rolled-up hash. So the build started writing `build/layer-manifest.txt`, one sha256 per path, and CI uploaded it as an artifact. Diffing the two manifests named exactly one file:

```
python/jmespath-1.1.0.dist-info/RECORD
```

**Why that file.** A wheel's `RECORD` lists every installed file with its hash. jmespath is the only dependency shipping a console script, and pip does not take that script from the wheel, it generates it, with a shebang naming the interpreter that ran the install:

```
#!/home/youssef/code/NightShift/.venv/bin/python
```

On a runner that path is somewhere under `/opt/hostedtoolcache`. Different bytes, different hash, and `RECORD` recorded it:

```
../../bin/jp.py,sha256=dzCAT-douyw2dUiBnbigpZGTUwnfJMxrS5y8CqcMBMQ,1725
```

The script itself was already being pruned, because nothing in a Lambda runs a console script. Its fingerprint stayed behind in a metadata file.

**The fix is not a workaround.** `RECORD` is supposed to describe what is installed. An entry for a file that is not in the artifact is simply wrong, reproducibility aside, so the build drops `RECORD` lines whose path escapes the layer, and rewrites the surviving lines byte for byte rather than through a CSV writer that might requote them. After that, runner and laptop agreed:

```
content sha256: da9e383f2cf367edb14e3d4a843a3e8106af0e471df362cef95f1c62ae7e154d
zip sha256:     53aec68892722b82a9f8870bcd633bb2bef222df64a9ce99ae74cccc80f96f48
```

**Then the wrong fix got reverted.** Switching the archive to uncompressed had been a response to the zlib theory, and it cost 20 MiB against a 50 MiB upload limit that M2b's OpenTelemetry packages will eat into. With the real cause fixed, compression was worth re-testing rather than leaving a precaution in place for a problem that did not exist. It reproduced byte for byte on both machines, so the layer is back to 7.7 MiB.

**What to take from this.** Two habits did the work. Measure a difference at the level where you can act on it, which meant adding a digest that separates packaging from archiving and then a manifest that names files. And be suspicious of a fix that works without explaining the evidence: uncompressed archives would have made the symptom go away while leaving a machine-dependent file inside the artifact, ready to cause something stranger later.

### The timeout that taught the most

The layer probe's first real invocation died: `Task timed out after 5.00 seconds`. The platform report said `initDurationMs: 110.7` and `maxMemoryUsedMB: 77` of 128, and no application log line was written at all. So module load was fast, memory was not exhausted, and the time went somewhere inside the handler before the first log statement. That pointed at the imports, which were lazy, inside a function.

**Step one: make the measurement fit the question.** The probe was changed to time each import individually. A timeout tells you the total was too big; it does not tell you which part. At 128 MB:

| Step | Time |
|---|---|
| Powertools | 1,737 ms |
| psycopg | 5,757 ms |
| boto3 | 2,755 ms |
| Build a DSQL client | 1,661 ms |
| **Total** | **11,910 ms** |

Nearly twelve seconds against a five second timeout. The obvious reading is "128 MB is too small", because Lambda allocates CPU in proportion to memory, so a 128 MB function gets roughly a twelfth of a vCPU.

**Step two: check the obvious reading before acting on it.** Sweeping memory gave a clean curve, and a surprise:

| Memory | Import time | GB-seconds |
|---|---|---|
| 128 MB | 11,910 ms | 1.49 |
| 512 MB | 2,770 ms | 1.39 |
| 1,024 MB | 1,387 ms | 1.39 |

CPU scales almost exactly linearly with memory, so the *same work costs the same GB-seconds at every size*. That is worth internalising, because the instinct that "more memory costs more" is only half right. For CPU-bound work, more memory buys latency at roughly no extra cost. For time spent waiting on a network call or a database, duration does not shrink, so a bigger function is simply more expensive. Which kind of work a function does decides whether raising memory is free or wasteful.

**Step three: the actual fix was not memory at all.** Lambda splits an invocation into an init phase, which runs module-level code, and the handler. During init, Lambda gives the execution environment more CPU than the configured memory would normally buy. The probe was doing its imports lazily inside the handler, which is exactly where that boost does not apply.

Moving the identical imports to module scope, at the same 128 MB:

| Step | Lazy in handler | At module scope |
|---|---|---|
| Powertools | 1,737 ms | 109 ms |
| psycopg | 5,757 ms | 312 ms |
| boto3 | 2,755 ms | 133 ms |
| DSQL client | 1,661 ms | 158 ms |
| **Total** | **11,910 ms** | **712 ms** |

Sixteen times faster, same memory, no money. Raising memory to 1,024 MB would have bought a 1,387 ms handler-phase import; moving the code up a few lines bought 712 ms and left the function at 128 MB.

**The rule this turns into.** Every service imports its dependencies and constructs its AWS clients and database connections at module scope, never on first use inside the handler. It is now in CLAUDE.md as an architecture note rather than something each new service has to rediscover. The same reasoning is why connection reuse across invocations matters: anything built during init is paid for once per execution environment rather than once per request.

**What the probe answered on the way.** psycopg reported `binary, libpq 180006`, which is the check that mattered. `import psycopg` alone would have succeeded even if the compiled driver had failed and psycopg had fallen back to a pure-Python implementation, and that silent downgrade would have shown up much later as mysterious slowness. It also reported the runtime carrying boto3 1.42.97 with the `dsql` client and both auth token methods, which settled the open question about whether the layer needed to bundle boto3. It does not.

**One thing deliberately left unproven.** Whether init duration is billed for on-demand invocations. The platform report lines did not surface in CloudWatch in time to check, so COST.md records it as unverified and assumes the conservative answer, that it is billed. Guessing in the optimistic direction on a project whose first rule is $0.00 is not a habit worth forming.

### Testing the lock the way the hooks were tested

A guard that has never been seen to fire is a guess. One hash in the lock file was replaced with 64 zeros and the build was run again:

```
ERROR: THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE.
If you have updated the package versions, please update the hashes.
Otherwise, examine the package contents carefully; someone may have
tampered with them.
```

The lock was restored and the build reproduced the same sha256 as before.

### What is deliberately not in the layer

`boto3` and `botocore`. The Lambda Python runtime already provides them, and bundling them would add roughly 20 MB to be unpacked on every cold start for no benefit. AWS no longer publishes a table of which SDK version each runtime carries; the documented way to find out is to print `boto3.__version__` from a deployed function.

That leaves one open question, recorded in the requirements file rather than discovered later: Aurora DSQL's auth token methods live on a fairly recent boto3, so before the first DSQL code lands the runtime's bundled version has to be confirmed to expose the `dsql` client. If it does not, boto3 goes into the lock file and the layer grows.

`aws-lambda-powertools` is installed without extras on purpose. The `[tracer]` extra pulls the AWS X-Ray SDK, which is unsupported from 2027-02-25 and is not the path this project took.

### Commands

| Command | What it does |
|---|---|
| `python3 -m venv .venv` | Ubuntu's system Python has no `pip` module, so a virtual environment is how the build scripts get one. CI gets pip from `actions/setup-python` instead. |
| `.venv/bin/python scripts/lambda_deps.py lock` | Resolves `requirements/lambda-deps.in` for Python 3.14 on aarch64 and writes `lambda-deps.lock` with a sha256 per wheel. Run it only when a dependency changes. |
| `.venv/bin/python scripts/lambda_deps.py build` | Installs the locked wheels into `build/layer/python/`, prunes bytecode and console scripts, and writes a deterministic `build/nightshift-deps-layer.zip`. |

`build/` is gitignored. The lock file is committed, because it is the thing that makes a build reproducible.

### The data plane, and the free tier's opinions about it

Three stores, each picked partly for what it does and partly for what it costs.

**Aurora DSQL for orders.** PostgreSQL compatible, no instance to pay for, no VPC to put it in, and an idle cluster scales to zero so it costs nothing between sessions. What it is not is ordinary PostgreSQL, and the differences shape the code that comes next: Repeatable Read is the only isolation level, concurrency control is optimistic so conflicting transactions fail at commit with SQLSTATE 40001 rather than blocking, DDL and DML need separate transactions with one DDL each, and there are no triggers, no PL/pgSQL and no temp tables.

**DynamoDB for carts.** A cart is one document, read and written by key, which is what DynamoDB is cheapest and simplest at. It also gives the chaos framework a second store whose failure modes look nothing like a SQL database's.

**SQS between checkout and payment.** Checkout writes the order and returns; payment happens asynchronously. That split is not architectural decoration, it is what makes several later scenarios stageable at all. A poison message, a retry storm and a growing oldest-message age are only interesting when there is a queue in the middle.

**Provisioned, not on-demand.** DynamoDB's free allowance is 25 RCU and 25 WCU per region, and it applies only to provisioned capacity. On-demand has no free capacity tier at all, so the mode that looks cheaper and more serverless would have been the one that costs money from the first request. The cart table takes 5 and 5, and COST.md carries a ledger so the total across every table and index stays under 25.

**TTL instead of a cleanup job.** Carts expire. Setting `ttl { attribute_name = "expires_at" }` makes DynamoDB delete them, and those deletes do not consume write capacity. A scheduled Lambda doing the same work would burn both invocations and WCU.

### Queue settings that are not arbitrary

| Setting | Value | Why |
|---|---|---|
| Visibility timeout | 180 s | AWS guidance is at least six times the consumer's timeout, and the worker gets 30 s. Too short and a slow-but-succeeding message is redelivered and processed twice. |
| `maxReceiveCount` | 3 | Low enough that a poison message reaches the DLQ quickly, high enough to ride out a transient failure. |
| DLQ retention | 14 days | The maximum. A message only lands here after repeated failure, and the point is that a human, or later the agent, can still look at it. |
| Redrive allow policy | one source queue | Without it, any queue in the account could redrive into this DLQ. A dead letter queue holding messages from an unknown sender is worse than not having one. |

### Encryption, and the scanner earning its keep

The trivy config scan failed the build on `AWS-0096`, queue not encrypted, at HIGH. That is a real finding and the fix was free: `sqs_managed_sse_enabled = true` uses SSE-SQS with an AWS managed key at no charge. The alternative that most tutorials reach for, `kms_master_key_id` with a customer managed key, is billed per request and is on this project's forbidden list. Same outcome for the scanner, very different bill.

It is worth noticing the shape of that: the secure option was also the free one, and the insecure default was simply nobody having set anything.

A side note on reading tool output. The first run looked clean because the check was written as `trivy ... | tail -6; echo $?`, and `$?` in a pipeline is the *last* command's status, so it reported `tail`'s success rather than trivy's failure. The scan had found both queues and said so. A guard whose exit code you read wrong is not a guard.

### Service-linked roles, learned the hard way

The apply failed on the cluster, after the table and both queues had already been created:

```
AccessDeniedException: Insufficient permissions to create service-linked role.
Add the iam:CreateServiceLinkedRole permission to your IAM policy.
```

**What a service-linked role is.** Some AWS services need to make calls on your behalf, for example to publish metrics or manage infrastructure they own. Rather than asking you to build a role with the right trust policy, the service defines one, and creating your first resource of that type creates the role automatically. It lives under the reserved path `/aws-service-role/`, you cannot edit its permissions, and you can only delete it once every resource that uses it is gone.

**Why it broke here and not for DynamoDB or SQS.** Those services do not use one. DSQL does, so `CreateCluster` implicitly needs `iam:CreateServiceLinkedRole`, which an IAM policy scoped to `role/nightshift-*` was never going to grant.

**How it was scoped.** Two independent limits rather than one:

```hcl
actions   = ["iam:CreateServiceLinkedRole"]
resources = ["arn:aws:iam::${account}:role/aws-service-role/dsql.amazonaws.com/*"]

condition {
  test     = "StringEquals"
  variable = "iam:AWSServiceName"
  values   = ["dsql.amazonaws.com"]
}
```

The resource confines it to the reserved path, and the condition pins the service, so this grant cannot mint a service-linked role for anything else. A bare `iam:CreateServiceLinkedRole` on `*` would let the pipeline create roles for any AWS service that has one, which is a much larger door than it looks.

This is also a small argument for scoping by path and condition rather than by name. The role turned out to be called `AWSServiceRoleForAuroraDsql`, not the `AWSServiceRoleForDSQL` that guessing would have produced, and a name-based policy would have failed for a second, more confusing reason.

### Verifying a value the API does not give you

DSQL exposes no endpoint attribute. The hostname has to be built from the generated cluster identifier:

```
<identifier>.dsql.<region>.on.aws
```

That is a construction, not a fact the provider returned, so it was checked rather than assumed:

```
$ getent hosts yjudav....dsql.ca-central-1.on.aws
2600:1f11:e4a:df04:5cea:87a4:9c0d:bf2c   yjudav....dsql.ca-central-1.on.aws

$ python3 -c "import socket; socket.create_connection((EP, 5432), timeout=10)"
connected to port 5432
```

Note the AAAA record: DSQL resolved to IPv6 here. Worth remembering if anything later runs somewhere without IPv6 egress.

The rest was checked the same way rather than trusted from `terraform apply` output: cluster `ACTIVE` with `AWS_OWNED_KMS_KEY`, table `ACTIVE` at 5 and 5 with TTL `ENABLED` on `expires_at`, and the queue reporting `VisibilityTimeout 180`, `maxReceiveCount 3` pointing at the DLQ, and `SqsManagedSseEnabled true`.

### Connecting to DSQL, and three things that are not in the tutorial

**The password is not a password.** DSQL authenticates with an IAM auth token used in the password field. `generate_db_connect_admin_auth_token` makes no network call: boto3 signs a request with the caller's credentials and hands back the signed string. Nothing is stored, so there is nothing to rotate or leak. Tokens expire in 15 minutes by default, but an established connection outlives its token, so it only has to be valid at connect time. Authorisation happens on connect, against `dsql:DbConnectAdmin` for the `admin` role or `dsql:DbConnect` for any other database role.

**`aws login` credentials need an extra package.** The first connection attempt died with `MissingDependencyException: ... requires botocore[crt]`. The AWS CLI bundles the CRT extra; plain boto3 does not, and the credential provider that reads what `aws login` writes depends on it. This is a local development problem only. A Lambda gets credentials from its execution role, so the runtime never touches that provider, which is why `requirements/dev.txt` carries it and the Lambda layer does not.

**`sslrootcert="system"` does not work with the psycopg binary wheel.** The obvious way to say "use the operating system's trusted roots" produced:

```
SSL error: certificate verify failed
```

which reads like the server's fault. It is not. `psycopg-binary` ships its own OpenSSL, and that build's compiled-in CA directory is not where Ubuntu keeps certificates. The fix is to name the bundle, so the helper picks the first path that exists:

```
/etc/ssl/certs/ca-certificates.crt   Debian and Ubuntu
/etc/pki/tls/certs/ca-bundle.crt     Amazon Linux, which is what Lambda runs
```

`sslmode=verify-full` is kept rather than downgraded to `require`. `require` encrypts but does not check that the certificate matches the hostname, so it protects against passive eavesdropping and not against being pointed at a different server.

One more observation from the same failure: the endpoint resolved to IPv6, WSL2 has no IPv6 route, and libpq tried the AAAA record, failed, and fell back to the A record. Everything worked, slightly slower. Worth remembering anywhere without IPv6 egress.

### The rule that breaks migration tooling

DSQL will not mix DDL and DML in one transaction, and allows exactly one DDL statement per transaction. Those two sentences remove the foundation every migration tool is built on: **you cannot change the schema and record that you changed it in the same transaction.**

In ordinary PostgreSQL, a migration runner wraps the DDL and the `INSERT INTO schema_migrations` together, so either both happen or neither does. Here the insert is necessarily a separate transaction. A crash in the gap leaves a schema that is ahead of its own bookkeeping, and the next run tries to apply a migration that is already applied.

There is no way to close that window, so the design accepts it and makes the bad case harmless:

| Decision | Reason |
|---|---|
| Every migration uses `IF NOT EXISTS` | Re-applying is a no-op rather than an error |
| Exactly one statement per file, enforced | A file can never be half applied, and the DSQL rule is checked rather than remembered |
| Files are checksummed | Editing an applied migration is how databases silently diverge between machines |

That is the general lesson: when a constraint removes a guarantee you are used to, the useful move is usually to make the failure survivable rather than to fight for the guarantee.

### Testing the guards instead of trusting them

Three guards, three deliberate attempts to trip them.

Editing a migration that had already run:

```
These migrations have already been applied but their files have changed
since: 0003_orders
Applied migrations are history. Add a new migration instead of editing one
that has run.
```

Putting two statements in one file:

```
0099_two.sql contains 2 statements.
DSQL allows one DDL statement per transaction, so each migration file must
hold exactly one. Split it.
```

And running the whole thing twice, where the second run printed `Nothing to do.`

### A mistake worth recording

The first version of the runner called `ensure_bookkeeping()` before checking which mode it was in, so the command documented as "show what would run, change nothing" created the `schema_migrations` table on a fresh cluster. Harmless in effect, wrong in principle, and exactly the kind of thing that erodes trust in a dry run.

The fix was to make only `--apply` create anything, including the runner's own table, which meant `applied_versions()` had to tolerate the table not existing:

```sql
SELECT to_regclass('public.schema_migrations')
```

`to_regclass` returns NULL instead of raising when the relation is absent, so the check needs no exception handling and no `information_schema` join.

The broader point: a dry run that writes is worse than no dry run, because it teaches you to trust a claim that is not true.

### Why the schema looks the way it does

| Choice | Reason |
|---|---|
| UUID primary keys, generated by the application | DSQL distributes data by primary key. Sequential keys concentrate writes on one range; random ones spread them. Scenario 7 stages hot-row contention deliberately, and that is only a meaningful test if the default is not already contended. |
| `inventory` split from `products` | Opposite access patterns. A product row is read constantly and written almost never; a stock row is written on every checkout. Merged, two unrelated purchases of the same product would rewrite the same row and, under optimistic concurrency, conflict at commit. |
| `order_items` keyed on `(order_id, product_id)` | A product can appear at most once per order, enforced by the database rather than by checkout remembering to. |
| `unit_price_cents` copied onto the line item | An order records what was charged, not what the product costs today. |
| `idempotency_keys` as its own table, key as the primary key | A primary key is the one uniqueness guarantee every distributed SQL engine supports. Using a `UNIQUE` constraint on a column of `orders` would have made the design depend on whether DSQL supports unique secondary indexes, which it did not need to. |

### Retrying serialization failures

DSQL uses optimistic concurrency control. Conflicting transactions do not block each other; both proceed and the loser fails at commit with SQLSTATE `40001`. Retrying is not an optimisation, it is how the system expects to be used, so `retry_on_conflict` lives in the shared module from the start rather than being added to checkout later.

The jitter matters more than the backoff. Without it, every transaction that lost the same conflict waits the same interval and retries at the same instant, so one contended row becomes a synchronised stampede that keeps colliding. Picking the delay uniformly from `[0, delay)` spreads the retries out. The `on_retry` hook exists so every retry can be logged, which CLAUDE.md requires and which the agent will later need as evidence.

### Checkout, and one transaction that has to be right

Checkout is the only place in this project where being wrong costs money, so it is worth walking through what the transaction does and why each part is there.

```
price the cart -> take the stock -> write the order -> write the lines -> record the idempotency key -> commit
```

**The stock check happens twice, on purpose.** The `SELECT` that prices the cart also reads quantities, but DSQL runs at Repeatable Read, so that snapshot cannot see a concurrent purchase. Checking `quantity >= wanted` in Python against it would be theatre. What actually prevents overselling is the `UPDATE`:

```sql
UPDATE inventory SET quantity = quantity - %s, updated_at = now()
WHERE product_id = %s AND quantity >= %s
```

followed by checking `cur.rowcount != 1`. If someone else took the last unit, the predicate fails, no row updates, and the transaction aborts. The earlier check is only there to produce a good error message in the common case.

**The idempotency key is inserted last.** It is the primary key of its own table, so a duplicate request collides there, after the order and lines are already staged, and the entire transaction rolls back. The handler then reads back the original order and returns it with `replayed: true` and a 200 rather than a 201. Inserting it first would work too, but doing it last means the collision happens when everything else has already succeeded, which keeps the failure path to a single rollback.

**Publishing to SQS happens after the commit.** Inside the transaction it would be a message for an order that might still roll back, and consumers would chase orders that do not exist. After the commit, a crash in the gap loses a message instead, which leaves an order stuck in `placed` that can be found and replayed. Losing work you can find is better than inventing work that never happened.

**Retries are logged, not swallowed.** `retry_on_conflict` takes an `on_retry` hook, and orders logs every serialization conflict with the attempt number and the delay. A rising retry count is the earliest visible symptom of the hot-row contention scenario, and an agent that cannot see retries would have to infer contention from latency alone.

### A long failure, and what it cost

orders-service could not call cart-service. Every attempt returned 403 with `Forbidden. For troubleshooting Function URL authorization issues`, and cart's handler never ran, so the rejection happened at the function URL's auth layer.

What was tried, in order, and what each ruled out:

| Attempt | Result |
|---|---|
| Identity policy allowing `lambda:InvokeFunctionUrl` on cart | Denied |
| `aws iam simulate-principal-policy` | Reported **allowed**, with and without the resource policy |
| Granting the role `lambda:*` on `*`, waiting 75 seconds | Denied |
| Resource policy on cart naming the orders role | Denied |
| Resource policy without the `FunctionUrlAuthType` condition | Denied |
| Resolving credentials per request instead of once per environment | Denied |
| The same code, same URL, from a laptop | **200** |

The one real bug found along the way was genuine: `content-type` was being signed on bodyless GETs, and a client that drops that header for a request with no body invalidates the signature. That was worth fixing. It was not the cause.

**What made this take so long was reading fast results as evidence.** Twice, something looked fixed because a test passed within seconds of a change, and both times the pass came from a cached decision or from a deploy having replaced every warm execution environment. One of those wrong conclusions, that a resource policy was unnecessary, was committed to main and broke checkout. The rule now in CLAUDE.md exists because of this: Lambda and IAM cache authorization in both directions, so neither a fast pass nor a fast fail means anything, and when results alternate, the correct move is to stop changing things rather than to keep trying fixes.

**The eventual shape of the answer.** A request signed by an IAM **role** is rejected at these function URLs; the identical request signed by an IAM **user** succeeds. That held for the orders execution role and again, independently, for the GitHub Actions role when the smoke test first ran. It was not root-caused.

**The decision.** Internal service-to-service calls now go through the Lambda Invoke API. This is not a workaround dressed up as a design: an internal call has no reason to leave AWS, traverse the internet and come back, and boto3 signs correctly without sixty lines of hand-written SigV4 in the repository. What is kept is the dependency's shape. `service_client.call` sends a function-URL-shaped event, so cart has one handler whether it is reached from outside or from orders, and the caller still sets a read timeout, so a slow dependency still surfaces as a timeout rather than an unbounded wait. That matters for the scenarios in M4.

Function URLs remain the external entry point, where they work.

### The smoke test that should have existed first

`terraform apply` succeeding means the infrastructure matches the configuration. It says nothing about whether a customer can buy anything, and this milestone produced a long period where every plan and apply was green while every checkout returned 502.

So the apply workflow now ends by buying something:

```
ok   store a cart -> 200
ok   checkout -> 201
ok   total is 30700 cents
ok   replayed idempotency key -> 200
ok   replay returned the original order
ok   checkout without an idempotency key -> 400
```

It checks the total, because a cart priced wrongly is worse than one that fails. It checks that a replayed idempotency key returns the *same order id*, because idempotency that returns 200 with a new order is a double charge. And it fails the job, so a change that breaks checkout cannot sit on main unnoticed.

The first time it ran in CI it failed, correctly, and told us something new: the GitHub Actions role hit the same 403 the orders role did. A test that fails on its first run for a real reason is a test worth having.

### The asynchronous half, and why the queue is there

Checkout writes the order and returns. Payment happens behind a queue, so a slow or failing payment provider degrades fulfilment instead of checkout. That split is not decoration: it is what makes several of the M4 scenarios possible at all. A poison message, a retry storm and a growing oldest-message age only exist when there is a queue in the middle.

**The provider's behaviour is configuration, not code.** Latency and error rate are environment variables on a deployed function. The temptation is to put `if os.environ.get("CHAOS"): fail()` in the application, and that rehearses nothing, because production has no such branch. Changing an environment variable on a Lambda is an ordinary config change, which is exactly the mechanism M4 is allowed to use.

### Partial batch failure reporting, and what it is actually for

Lambda hands an SQS consumer up to ten messages at once. The naive contract is all-or-nothing: if the handler raises, the whole batch is treated as failed and all ten messages become visible again.

That is worse than it sounds. Nine messages that succeeded get processed a second time, and every one of them burns a delivery attempt against `maxReceiveCount`. With `maxReceiveCount` at 3, a single persistently bad message can push its nine innocent neighbours to the dead letter queue after a few cycles. The DLQ threshold stops meaning "this message is bad" and starts meaning "this message shared a batch with a bad one".

`function_response_types = ["ReportBatchItemFailures"]` changes the contract. The handler returns the identifiers of the messages that failed, and only those are redelivered:

```json
{"batchItemFailures": [{"itemIdentifier": "bad-1"}]}
```

Verified by invoking the worker with a batch of three, one of which had an unparseable body. Exactly one identifier came back, and it was the right one.

The handler side of that contract is a `try` around each record rather than around the loop. Catching broadly per message is usually a smell; here it is the whole point, because the alternative is one message deciding the fate of nine others.

### Idempotency, again, in a different shape

SQS is at-least-once. A message can be delivered twice, so settling an order has to be safe to do twice:

```sql
UPDATE orders SET status = 'paid', updated_at = now()
WHERE order_id = %s AND status = 'placed'
```

A redelivery matches no rows, `rowcount` is 0, and the worker logs that it was already paid and succeeds. Without the status predicate, a redelivery would charge the provider a second time.

Note this is the third distinct idempotency mechanism in the project, each suited to its layer: a primary key collision in checkout, a conditional update here, and DynamoDB's conditional writes for the agent's incident correlation in M5. They are not interchangeable.

**A missing order is dropped, not retried.** If the order does not exist, raising would put the message back on the queue to fail twice more and land in the DLQ having learned nothing. Retrying only helps when the failure might be transient, and "this row does not exist" is not.

### Cost is a design input, not an afterthought

The event source mapping ships `enabled = false`. An enabled mapping long-polls with five connections continuously, roughly 648,000 SQS requests a month, about two thirds of the free allowance spent on an idle queue producing nothing.

So the trigger is turned on for a run and off afterwards, which also means most of M3's pause command already exists. Verified both directions: enabling it drained 19 queued orders from `placed` to `paid` within 15 seconds, and disabling it returned the mapping to `Disabled` with a clean plan.

### Two more IAM lessons, quickly

Creating the mapping failed first time on `lambda:TagResource`, because `default_tags` tags the mapping too, and a mapping is a different resource type with an ARN of `event-source-mapping:<uuid>`. The function-scoped grant did not cover it.

Scoping that grant needed care. The create, update and delete actions take an `ArnLike` condition on `lambda:FunctionArn`, which works because that key is in the request context for them. `TagResource` gets no such condition, because the key is not present for it, and by now the rule is established: a condition on a key that is not in the request context is a deny, not a tighter allow.

### Reading the bill before running the load test

Step 7 of M2a was supposed to be arithmetic: run some checkouts, read the DPU metric, divide. COST.md carried an estimate of roughly 20,000 DPUs per busy month against a 100,000 DPU allowance, marked weak, and the whole 42-incident benchmark plan rested on it. DSQL is not covered by AWS free tier usage alerts, so if the estimate were wrong, nothing outside this repo would say so.

Before measuring anything it was worth looking at what the cluster had already billed. That is where it stopped being arithmetic.

**The number that did not fit.** Six minutes in the previous session's history carried far more compute than their neighbours: 126 DPU, 204 DPU, and four of almost exactly 315 DPU. Each of the 315s was one read-only transaction that had read **104 bytes**. That is the whole clue. No amount of CPU work reads 104 bytes, so whatever `ComputeDPU` counts, it is not work. Checking the two metrics against each other made it concrete: `ComputeDPU` is exactly `ComputeTime` in milliseconds over 1000, in every datapoint the cluster has ever published.

**Designing an experiment that can be wrong.** The inference was strong but it was still an inference, and COST.md is meant to hold measurements. The tempting experiment is to hold a transaction open and see what it bills. That alone proves nothing: a large number would be consistent with "time is billed" and also with "this cluster bills a lot for something". What makes it an experiment is the control. Two phases, identical query work, differing in one variable:

| Phase | Transactions | Query work | ComputeTime | ComputeDPU |
|---|---|---|---|---|
| committed immediately | 7 | `SELECT 1` each | 209 ms | 0.209 |
| one held open 60 s | 2 | `SELECT 1` | 60,062 ms | 60.062 |

Sixty seconds of doing nothing cost 287 times more than the same query committed at once. 60.062 DPU for 60.000 seconds is one DPU per transaction-second, within a tenth of a percent.

This changes the mental model of the free allowance. It is not 100,000 units of work. It is about **27.8 hours of open transaction time per month**, and a transaction is expensive for being open, not for being busy.

**The bug the bill found.** With the billing model understood, the 315s had an obvious shape: a transaction opened, left open, and eventually killed by DSQL's cap. The cause was ours. `dsql.connect` defaulted to `autocommit=False`, which is psycopg's default and the one most PostgreSQL code wants, and psycopg opens a transaction on the first statement and holds it until someone commits. Four code paths never did. The orders service rolled back a failed checkout and then ran a lookup it left open. The fulfilment worker left its lookup open on both early returns, and on the normal path held it open **across the call to the payment provider**.

Then Lambda freezes the execution environment with the transaction still open on the server, and nothing closes it until DSQL's cap does, 315 seconds later. About six requests wasted 1,590 DPU, 1.6 percent of a month.

The part worth remembering is that this bug had **no functional symptom whatsoever**. Every request returned the right status code. The smoke test passed. `terraform plan` was clean. Nothing was slow, nothing errored, nothing appeared in the logs. It was visible only in a billing metric, and only to someone who looked at a number they did not expect and refused to move on. For a project about an on-call agent, that is the whole thesis in one incident: the signal that matters is often not the one that pages you.

**Why the fix is a default, not four patches.** Adding `conn.rollback()` after each of the four reads would have worked today and failed the next time someone added a fifth read. Flipping the default to `autocommit=True` makes the leak structurally impossible: a lone statement is its own transaction and ends the moment it returns. Code that genuinely needs several statements to be atomic now has to say so, and checkout does:

```python
with conn.transaction(), conn.cursor() as cur:
    ...  # price the cart, decrement inventory, write the order, claim the key
```

Leaving that block commits, raising out of it rolls back, and the `UniqueViolation` that a replayed idempotency key triggers rolls the whole thing back before anything is charged. The general principle: when a mistake is both easy to make and invisible when made, change the default rather than remembering harder.

The inverse also matters. `mark_paid` is one `UPDATE`, so it gets no transaction block at all. On an autocommit connection a single statement is already atomic and already committed when `execute` returns, and wrapping it would add round trips and widen the window being billed for. Transactions are not free here in a way they are not free in ordinary PostgreSQL.

**A smaller lesson about CloudWatch.** DSQL reports a transaction's compute in the bucket *after* the one it finished in. The held transaction ended at 16:52:53 and was reported in the bucket starting 16:53:00. A window read with one minute of padding would have caught it by seven seconds, which is not a margin to build a measurement on, so `measure_dpu.py` pads two minutes at the end.

**What the measurement script does differently.** It runs batches of different sizes and fits a line through them, rather than running one batch and dividing. One batch size cannot separate the cost of a checkout from the cost of a batch happening at all, and the two behave differently in a benchmark: a fixed per-batch cost amortises over a long run, a per-checkout cost does not. The slope is what the projection needs.

### Testing for a bug that has no symptom

The transaction leak returned correct status codes, wrote correct rows, logged nothing unusual and passed the smoke test. No assertion about a response could have caught it. The assertion has to be about the connection: **after this handler returns, is a transaction still open?**

That needs a fake database connection, and a fake is where this kind of test usually goes wrong. A fake that reports "idle" unconditionally makes every test in the file pass, including against the broken code, and the suite becomes a decoration that costs CI minutes. So the fake models psycopg's actual state machine, including the part that caused the bug: with autocommit off, the first statement opens a transaction and it stays open until someone commits.

Two habits kept it honest.

**The harness proves it can fail.** The first two tests in the file do nothing but check the fake itself: autocommit off plus one lone statement must report `INTRANS`, and autocommit on must report `IDLE`. If those two ever stop distinguishing the cases, every assertion after them is worthless and the file says so out loud.

**The tests are wired to the real default, not to a convention.** The handler tests do not hard-code `autocommit=True`. They read the actual default off `dsql.connect`'s signature and build the fake with it. Flipping that default back in `src/common/dsql.py` is enough to make them fail, which means the test is attached to the fix rather than to a description of the fix.

Then the whole thing was checked the only way that really counts: the three fixed source files were reverted to the commit before the fix and the suite was run again. **Nine of fifteen failed**, including every one of the four leaked paths and the payment-provider assertion.

The six that still passed are the most interesting part. The successful checkout, the successful fulfilment and the queue publication all passed against the broken code, because the happy paths *did* commit. The leaks were only ever on the replay path and the two early returns. That is the whole reason the bug survived: the smoke test walks the happy path, and the happy path was fine. A suite that only covers what usually happens would have shipped this bug just as confidently.

**The sharpest test in the file** does not check the end state at all. It records `conn.info.transaction_status` at the moment the payment provider is invoked and asserts it is `IDLE`:

```python
assert calls == [TransactionStatus.IDLE], (
    "a transaction was open while waiting on the payment provider, "
    "so its latency is being billed as DSQL compute time"
)
```

That encodes a cost rule as an executable constraint: never hold a database transaction across a network call. Scenario 4 deliberately makes that provider slow, so without this rule a latency incident silently becomes a billing incident.

**Two smaller things.** All four services have their handler at `app.py`, because that is what each deployment zip contains, so importing them normally would collide on the name `app`. The tests load each one by path under its own module name, which is what Lambda effectively does anyway. And `conftest.py` sets obviously fake AWS credentials before anything imports boto3, so a test that escapes its mock fails with an authentication error instead of quietly creating something real in an account whose first rule is that it costs nothing. Unsetting `AWS_PROFILE` matters there too: botocore reads an empty one as a profile literally named `""` and raises `ProfileNotFound`.

### Two points always fit a line

The first DPU measurement used batches of 5 and 25 and reported 0.1189 DPU per checkout. That is a real number from real data, and it was wrong enough to matter.

Two points determine a line exactly. There is no residual, no error estimate, and no way for the data to disagree with the model, so the fit cannot tell you whether the model is right. It only tells you what the model says if you assume it is. Adding a batch of 50 moved the marginal cost to **0.1366, up 15%**, and pulled the fixed cost from 0.639 down to 0.425.

Fifteen percent sounds survivable until you notice which way it went. The marginal term is the one multiplied by 100,800 in the benchmark projection, and the fixed term is the one that amortises away. Two points understated the term that scales and overstated the term that does not. The error was in the direction that flatters the estimate, which is the direction errors usually run when nobody has checked.

The third point cost about 7 DPU and ten minutes of waiting.

The fulfilment measurement was done with two sizes for the same reason, 31 orders and 50, and there the linearity held perfectly: both gave 0.0768 DPU per order to four decimal places, with a fixed cost of -0.001, which is zero within noise. Worth noticing that this is what a genuinely linear result looks like, and that the checkout numbers never looked like that.

**A detail that fell out of having three points.** Every write DPU figure measured is an exact multiple of 0.05: the values 0.400, 1.550, 3.000 and 2.500 are 8, 31, 60 and 50 units. A drain of 31 orders billed exactly 31 units and a drain of 50 billed exactly 50, one per single-row update, while a checkout bills about 1.2 units despite writing six rows. So the quantum is not per row, and a very small write costs the same as a slightly larger one. The design consequence is to prefer fewer, fuller write transactions, which happens to be the same advice that transaction-duration billing gives.

**And the estimate that was right.** Fulfilment was guessed at 0.07 DPU per order before it was measured, and came in at 0.0768. That guess landed because the billing model underneath it had already been measured: it was built from the cost of a short committed transaction, times the two transactions the worker issues. A guess standing on a measurement is a different thing from a guess standing on nothing, which is what the original 20,000 DPU projection was. It also landed close, and it was still recorded as an estimate until it was checked, because the alternative is not knowing which of your numbers you are allowed to trust.

## M2b step 1: verifying the telemetry budget (2026-09-22)

M2b adds metrics, flags and tracing. Each has a free allowance that is easy to overspend without noticing, so before building anything the limits were re-read on AWS's own pages and checked against this account's own data. No AWS resources were created.

### What makes a custom metric

A CloudWatch metric is identified by namespace, metric name and dimensions. Every distinct set of dimension values is billed as its own metric. `CheckoutsRejected{service=orders}` is one metric. `CheckoutOutcome{service=orders, outcome=rejected}` looks like one too, but each new `outcome` value (`accepted`, `out_of_stock`, `invalid`) quietly creates another. That is why our five metrics are five separate names with `service` as the only dimension, and why the reason for a rejection goes in a log field instead of a dimension. The unit does not create a new metric. The 10 free metrics are shared with detailed monitoring, and charges are prorated by the hour, but the ledger in COST.md counts every metric as a whole one anyway.

### EMF costs twice

EMF (embedded metric format) is a JSON log line with a `_aws` block that tells CloudWatch to extract metrics from it. Powertools Metrics writes those lines. There is no `PutMetricData` call, so the function needs no `cloudwatch:PutMetricData` permission. The billing docs list the extracted metrics under `MetricStorage:AWS/Logs-EMF`, which counts toward the 10 custom metrics, and the line itself is billed as log ingestion. So every metric costs a slot in the metric count and some bytes in the Logs allowance.

A trap found in the log class docs: a log group in the Infrequent Access class does not extract EMF metrics. The line would still be ingested and billed, but no metric would appear, and nothing would report an error. Ours are all Standard, checked with `describe-log-groups`.

### Measuring log volume instead of guessing it

The old ~3.5 GB Logs projection had no derivation written down. It was replaced with a measurement, using two free APIs:

```bash
# bytes ingested per log group this month (AWS/Logs publishes this for free)
aws cloudwatch get-metric-statistics --namespace AWS/Logs --metric-name IncomingBytes \
  --dimensions Name=LogGroupName,Value=/aws/lambda/nightshift-orders \
  --start-time 2026-09-01T00:00:00Z --end-time 2026-09-23T00:00:00Z \
  --period 86400 --statistics Sum
# invocations per function over the same window
aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Invocations \
  --dimensions Name=FunctionName,Value=nightshift-orders ... --period 86400 --statistics Sum
```

Dividing one by the other gives bytes per invocation: 705 for orders, 385 for payments, about 1.8 KB for an order's whole lifecycle.

A mistake made along the way: the first attempt used one 30-day period (`--period 2592000`) and asked for three different end dates. All three returned the same total, because a period is only reported whole and ignores where the end date cuts it. Daily periods, summed, gave the real answer.

Once the ingestion is measured, it turns out not to be the problem: a full benchmark pass ingests about 0.22 GB. The budget goes on **Logs Insights scans**, which bill every byte in the time range queried, even if the query returns nothing. 126 investigations that query logs at 25 MB each is 3.15 GB. That makes the 25 MB cap per investigation a design requirement for M5, not a tuning knob.

### Reading free tier usage without paying for it

```bash
aws freetier get-free-tier-usage --region us-east-1
```

This lists every Always Free allowance the account is tracking, with actual usage this month. It costs nothing. The Cost Explorer API (`aws ce get-cost-and-usage`) looks similar and is billed $0.01 per request, so that one is used in the console only, where it is free. The `Global-` prefix on the usage types (for example `Global-DataProcessing-Bytes`) means the allowance is summed across all regions.

### The question the docs did not answer

Since May 2025, AWS has priced Lambda logs as "vended logs", with their own usage type (`VendedLog-Bytes`) and tiered prices. No page said whether the 5 GB free tier covers that usage type. If it did not, a benchmark month would cost about $0.21, over this project's $0.10 line.

The account's own bill was the best evidence available. Console: **Billing and Cost Management → Bills → September 2026 → Charges by service → CloudWatch → Canada (Central)**. It showed three line items, all $0.00: API requests, `CAN1-TimedStorage-ByteHrs` (log storage) and `PutLogEvents` ("First 5GB per month of log data ingested is free"). There was no vended logs line, and Lambda is the only thing in the account that writes logs.

That is evidence, not proof, for two reasons the owner raised in review. Both quantities read 0 GB, so at this volume the bill is consistent with rounding and cannot show how vended logs are categorized; a small vended line might simply not appear. And the account is on the Free plan, where nothing can be charged, so the bill may present usage differently after the upgrade to Paid. COST.md records it as unsettled, keeps the $0.21 worst case in view, and schedules a re-check once a month has tens of MB of Lambda logs and again on the first Paid-plan bill.

The general lesson: when the documentation is silent, the bill is useful evidence and free to read in the console. But a reading of 0 can hide a lot, and a conclusion drawn at tiny volume has to be re-checked at real volume.

### Interview questions

1. **How do you keep CloudWatch custom metrics from getting expensive?**
   Every unique combination of namespace, name and dimension values is one billed metric, so the risk is a dimension with many possible values. Ours have one dimension (`service`) with a fixed value per function, five metric names in total, and a ledger in COST.md that every metric enters before any code emits it. Anything with many possible values, such as a rejection reason, goes in a log field. Step 3 adds a test that asserts the exact dimension set of the emitted EMF document, so adding a dimension fails CI instead of showing up on the bill.
2. **Why EMF rather than `PutMetricData`?**
   EMF writes metrics as part of a log line the function already produces, asynchronously, with no extra API call in the request path and no `PutMetricData` permission. The cost is that each metric also uses Logs bytes, which I budgeted: about 440 bytes per order on top of 1.8 KB.
3. **Where does your observability budget actually go?**
   Into log queries, not log ingestion. Measured ingestion for a full benchmark is about 0.22 GB of 5. Logs Insights bills every byte in the queried time range, and 126 investigations at 25 MB each is 3.15 GB. So the agent carries a per-investigation scan cap, enforced from the `bytesScanned` statistic each query returns.
4. **The docs didn't say whether Lambda logs are covered by the free tier. How did you handle that?**
   I read the account's September bill. It had a free tier `PutLogEvents` line and no vended logs line, and Lambda is the only log producer in the account. But every quantity was 0 GB, and the account was on the Free plan, so I recorded that as evidence rather than a conclusion. The worst case is $0.21 in a benchmark month. The $0.01 tripwire budget fires after about 20 MB of paid ingestion, and the question is re-checked once there is real volume on the bill and again after the upgrade to Paid.
5. **Why not use the Infrequent Access log class to save money?**
   It is cheaper per GB ingested, but it does not extract EMF metrics, does not support metric filters, and does not support `GetLogEvents`. Our metrics would silently vanish. We are inside the free tier anyway, so the saving would be zero.

## M2b step 2: operational feature flags (2026-09-22)

A feature flag here is a switch an operator (and from M6, the agent) flips during an incident to change how a service behaves without deploying anything. There are two, stored as SSM Parameter Store parameters under `/nightshift/flags/`:

| Flag | Read by | What `true` / `N` does | Undo |
|---|---|---|---|
| `payments_degraded_mode` | fulfillment-worker | Skips the payment provider, leaves the order in `placed`, acknowledges the message | Set `false`, then `scripts/replay_placed_orders.py --apply` |
| `checkout_rate_limit` | orders-service | Allows N checkouts per second per execution environment; the rest get 429 with `retry-after: 1`. 0 is off | Set `0` |

### Why each flag behaves the way it does

**Degraded mode defers rather than refusing.** It exists for scenario 4, a slow payment provider. The two alternatives were worse. A checkout kill switch (return 503) hurts customers more than a slow provider does, since orders were still being accepted anyway. Failing fast into the DLQ spends delivery attempts and makes the DLQ-depth alarm fire, which is exactly the signal scenario 5 (poison message) depends on. Deferring keeps checkout working, stops calls to the sick dependency, and loses nothing, because the order is still in the database in `placed`.

Acknowledging the message is the important detail. Reporting it as a failure would redeliver it against the same slow provider, three times, and then park it in the DLQ.

**The replay script is careful about double charging.** Two messages for one order, processed at the same moment, can both read `placed` and both call the payment provider. The worker's conditional `UPDATE ... WHERE status = 'placed'` stops the second one from marking it paid, but not from charging. So the script refuses while the flag is still on, refuses while the queue has any messages (a `placed` order might still have its original one waiting), and skips orders younger than 10 minutes (they might be in flight). The same script also closes a gap orders-service already documented: a crash between committing an order and publishing its message.

**The rate limit is per environment, not global.** Each Lambda execution environment keeps its own token bucket. With reserved concurrency 2, a limit of N means at most 2N per second. An exact global limit would need a shared counter, such as a DynamoDB item updated on every checkout, which costs write capacity and adds a new way for every checkout to fail. For shedding load during an incident, the approximation is enough, and the 2N ceiling is written down.

A token bucket holds up to N tokens and refills at N per second. Each request takes one. That allows a burst of N, then holds the average at N per second. The check runs first in the handler, before the cart call or the database, because shedding load is only useful if a shed request is cheap.

### The flag reader

`src/common/flags.py` caches each value for 30 seconds per execution environment, so a checkout does not wait on SSM every time. Three details:

- **Fail open.** If SSM errors (throttled, down, or permission removed), the last value read is kept, and if there never was one, the default. A flag outage must not become a checkout outage.
- **Cache the failure too.** Otherwise an SSM outage adds one failing call, with its timeout, to every request.
- **Short timeouts.** boto3 defaults to a 60 second read timeout plus retries. The client uses 1 second and 2 total attempts.

A bug in that last line, caught by a test before it shipped: `retries={"max_attempts": 2}` makes **three** calls, because botocore reads `max_attempts` as the number of retries. The key that means "total calls" is `total_max_attempts`. The docstring said two, the config said three, and only the test that read back `client.meta.config.retries` noticed.

### Terraform owns the parameter, not its value

```hcl
resource "aws_ssm_parameter" "payments_degraded_mode" {
  name            = "/nightshift/flags/payments_degraded_mode"
  type            = "String"
  tier            = "Standard"
  value           = "false"
  allowed_pattern = "^(true|false)$"
  lifecycle { ignore_changes = [value] }
}
```

- `ignore_changes = [value]`: during an incident someone flips the flag in SSM directly. Without this, the next routine `terraform apply` would quietly flip it back while the incident was still going on.
- `allowed_pattern`: SSM itself rejects `ture` or `-1` at write time, so a typo fails at the keyboard rather than silently reading as the default inside the service.
- `tier = "Standard"` stated explicitly: advanced parameters are billed, and a parameter can never be moved from advanced back to standard, only deleted and recreated.

Each function's IAM policy allows `ssm:GetParameter` on its own flag only, which also makes the policy an exact record of which service reads which flag.

**A plan-time error worth knowing.** The first version of those IAM statements used `aws_ssm_parameter.checkout_rate_limit.arn`. An ARN is unknown until the parameter exists, which made the whole policy document unknown, and the Lambda module decides `count = var.extra_policy_json == null ? 0 : 1`. Terraform cannot plan a `count` that depends on an unknown value, so the plan failed. The fix builds the ARN from values known at plan time (region, account ID, name), the same trick already used for `cart_function_arn`.

**And one about how the CI role changes itself.** The CI apply role gained SSM permissions, but it has an explicit deny on changing its own policy. So that one resource was applied locally first:

```bash
terraform -chdir=terraform apply -target=aws_iam_role_policy.ci_apply
```

Then the PR was merged and `apply.yml` deployed the rest. The deny working as designed is the reason for the two steps.

### Verifying it live

**Rate limit.** Checkouts were sent through the Lambda API with no idempotency key. One that passes the limiter stops at the 400 immediately after it, before the cart or the database, so the test cost no DSQL at all: 400 means "let through", 429 means "shed".

```bash
aws ssm put-parameter --name /nightshift/flags/checkout_rate_limit --value 1 --overwrite
```

| Step | Result |
|---|---|
| Limit 0 | 10 of 10 let through |
| Set to 1 | First 429 at **26.5 s** after the write, inside the 30 s TTL |
| Limit 1, after 2 s idle | `[400, 429, 429]`: one through, then shed |
| Back to 0 | All let through again by **16.6 s** |

The two times differ because each depends on when that execution environment last refreshed its cache, which is anywhere in the 30 s window.

**Degraded mode**, first by invoking the worker directly with a synthetic SQS event, then through the real queue:

1. Flag `true`, wait 35 s, enable the consumer (a Terraform apply with `-var=queue_consumer_enabled=true`). The queued message from the deploy's smoke test was consumed and logged `payment deferred` with its original correlation ID. The payment provider's log group had no events in the window. The queue emptied and the order stayed `placed`.
2. Flag `false`, wait 35 s, `replay_placed_orders.py --apply`: "Republished 1". About 30 s later the worker logged `order paid` with correlation ID `replay-<order_id>`, and the payment provider was called exactly once.
3. Consumer disabled again through Terraform. `terraform plan -detailed-exitcode` returned 0.

Both refusal guards in the replay script fired during the check without being provoked on purpose: once for a non-empty queue, once for the flag still being on.

**A wrong assumption along the way.** The first `--apply` republished nothing, reporting "0 orders in 'placed' for more than 10 minutes". I had estimated the order was about 25 minutes old. Asking the database settled it: created 21:21:50, and the query ran at about 21:29, so the order was 7.5 minutes old and the age filter was right to skip it. The fix was to wait, not to change the code. The lesson: when a guard refuses, check the guard's inputs before doubting the guard.

**Two small shell lessons.**
- `export AWS_PROFILE=x DSQL_ENDPOINT=$(terraform output ...)` runs the command substitution before the export takes effect, so Terraform ran with no profile. Set the profile in its own `export` first.
- CI's lint job failed on EXE001 ("shebang present but file is not executable") for the new script. The other scripts are stored as mode `100755` in git (`git ls-files -s scripts/` shows it); this one was `100644`. My local `pre-commit run` on the staged files did not report it, and I have not worked out why; `pre-commit run --all-files` is what CI runs.

### Interview questions

1. **Why SSM Parameter Store for feature flags rather than environment variables?**
   In this project, changing an environment variable means publishing a new function version and moving the `live` alias, which is a deploy. A flag has to change in seconds during an incident without a deploy, and be readable by the agent as a separate fact. Standard parameters are free, each function can be limited to reading its own, and the 30 s cache keeps SSM out of the request path.
2. **What happens to checkout if SSM goes down?**
   Nothing visible to customers. The reader keeps the last value it read, or the default if it never read one, logs the failure, and caches the failure for 30 s so it is not retried on every request. Each call allows 1 s to connect and 1 s to read, with 2 attempts, so a failing read costs a few seconds at most, once per 30 s per environment, instead of boto3's default of a minute or more. There is a unit test for each of those cases.
3. **Your rate limit isn't exact. Why is that acceptable?**
   It is per execution environment, so the real ceiling is N times the number of environments, 2N here. An exact limit needs a shared counter on every checkout, which costs DynamoDB write capacity and makes the limiter itself a new dependency that can fail. Its job is shedding load off a struggling dependency during an incident, where roughly right in 30 seconds beats exactly right with a new failure mode.
4. **How do you stop `terraform apply` from undoing an operator's flag change?**
   `lifecycle { ignore_changes = [value] }`. Terraform owns that the parameter exists, its type, tier and allowed pattern, but not what it is currently set to. The value in the configuration is only the starting value.
5. **What stops a replayed order from being charged twice?**
   The replay refuses while the queue has any messages or the degraded flag is on, and skips orders younger than 10 minutes. Those three cover the ways a `placed` order can still have another message coming. The worker's conditional update means a duplicate can never mark an order paid twice, but it cannot un-send a second charge, so the prevention has to happen before the message is published.

## M2b step 3: EMF metrics (2026-09-22)

Five custom metrics, all budgeted in the COST.md ledger before any code emitted them:

| Service | Metric | When |
|---|---|---|
| orders | `CheckoutsPlaced` | A new order is written. A replayed idempotency key is not counted: it returns an order placed earlier. |
| orders | `CheckoutsRejected` | Any rejection: missing idempotency key (400), unknown cart or product (404), not enough stock (409), rate limited (429). |
| orders | `SerializationRetries` | Each retry after SQLSTATE 40001. The earliest visible sign of hot-row contention. |
| fulfillment | `OrdersPaid` | This delivery moved the order from `placed` to `paid`. |
| fulfillment | `PaymentFailures` | The payment call raised, error or timeout alike. |

Namespace `NightShift`, `service` as the only dimension, cold-start metric off.

### How the metrics get from Python to CloudWatch

Powertools `Metrics` collects metrics during an invocation, and `@metrics.log_metrics` on the handler prints them at the end as one JSON line in embedded metric format. A real one from orders:

```json
{"_aws":{"Timestamp":1790115111783,"CloudWatchMetrics":[{"Namespace":"NightShift","Dimensions":[["service"]],"Metrics":[{"Name":"CheckoutsPlaced","Unit":"Count"}]}]},"service":"orders","CheckoutsPlaced":[1.0]}
```

CloudWatch Logs recognises the `_aws` block and turns the line into a metric data point. There is no `PutMetricData` call and no extra IAM permission, which is why step 3 needed no Terraform change beyond new code.

**The risk checked first.** Our functions use Lambda's JSON log format. If Lambda wrapped that line in its own JSON envelope (`{"timestamp":..., "message": "{\"_aws\"...}"}`), CloudWatch would not see an `_aws` block, no metric would appear, and nothing would report an error. The Lambda docs say "Lambda doesn't double-encode any logs that are already JSON encoded", but they also warn that EMF can break under JSON format for Node.js and recommend testing. So the live check read the raw log line back: it arrived unwrapped, and `list-metrics` showed the metrics.

### Making the ledger executable

`tests/test_metrics.py` drives every handler path that emits a metric, captures the EMF lines printed to stdout, and checks them against the ledger table in COST.md, parsed with a regular expression. It fails if:

- a metric is emitted that has no ledger row;
- any dimension set is other than `["service"]` (the failure message says why: each extra dimension value is a separately billed metric);
- `ColdStart` appears;
- a ledger row names a metric nothing emits;
- the ledger stops parsing (it asserts exactly 5 rows, so a reformatted table cannot silently become an empty ledger that everything passes against).

Like the transaction hygiene tests, it was checked by breaking the code on purpose. Adding `metrics.add_dimension(name="reason", ...)` failed with the billing message, and renaming `CheckoutsPlaced` failed with "budgeted but never emitted".

The ledger test earned its keep before it was even committed. While PRs #11 and #13 were still unmerged, the branch was moved onto a `main` that did not have the ledger yet, and six tests failed with "metrics not in the COST.md ledger". That was correct, and it is how the unmerged PRs were noticed.

**One Powertools detail.** `Metrics` instances share their metric set at class level, so two `Metrics` objects in one process are really one. In Lambda each process runs one service, so it does not matter. In tests, where orders and fulfillment load side by side, it works only because every handler flushes (and clears) at the end of each call.

**One `SerializationRetries` rule.** fulfillment also retries on 40001, but the ledger budgets `SerializationRetries` for `service=orders` only. Emitting it from fulfillment would create `SerializationRetries{service=fulfillment}`, a sixth metric. So fulfillment logs its retries without a metric, and the code says why.

### Verified live (2026-09-22)

After `apply.yml` deployed it (smoke test green), with no extra traffic beyond the smoke test and one direct invoke of fulfillment for the smoke order:

| Check | Result |
|---|---|
| Raw EMF line in the log group | Unwrapped, namespace `NightShift`, `Dimensions: [["service"]]` |
| Line size | 210 to 214 bytes (orders), 204 bytes (fulfillment), against a 400-byte estimate |
| `aws cloudwatch list-metrics --namespace NightShift` | Exactly 3: `CheckoutsPlaced`, `CheckoutsRejected` (service=orders), `OrdersPaid` (service=fulfillment), one dimension each |
| `get-metric-statistics ... --statistics Sum` | 1.0 each, matching the smoke test |
| Custom metrics in the whole account | 3 |

`SerializationRetries` and `PaymentFailures` need real contention or a failing payment provider to appear. The unit tests cover their shape; they will show up live in the chaos scenarios that cause them.

The measured line size replaced the estimate in COST.md: about 2,013 bytes of log per order instead of 2,219, and the Logs budget total moved from 3.62 to 3.60 GB.

### A gap found for M3

When cart-service fails, orders catches the error and returns 502 without raising. Lambda only counts an invocation in `AWS/Lambda Errors` when the handler raises or times out, so orders' `Errors` stays at 0 during a cart outage. An M3 alarm on orders' `Errors` would not fire. To be decided with the alarm design in M3, not patched here.

### A GitHub lesson

After PR #11 was merged, GitHub marked #13 (which contained #11's commits) as conflicting. A local trial merge (`git merge --no-commit --no-ff`) into the new `main` was clean, and the commit merged as #11 was byte-for-byte the one #13 contained, so the flag was stale. Merging `origin/main` into #13's branch and pushing made GitHub recompute: `MERGEABLE`, CI green, merged. Check locally before believing a conflict report.

### Interview questions

1. **How do you know your metrics actually reach CloudWatch, given you use Lambda's JSON log format?**
   The docs say Lambda does not re-encode lines that are already JSON, but they also recommend testing EMF under JSON format. So after deploying I read the raw log line back with `filter-log-events` and confirmed the `_aws` block was at the top level, then confirmed with `list-metrics` that CloudWatch had created exactly the three metrics the smoke test should produce, each summing to 1.
2. **How do you stop a teammate from adding an expensive metric by accident?**
   A unit test parses the ledger table in COST.md and fails CI on any metric or dimension that is not budgeted there. Adding a metric means adding a ledger row in the same PR, where the cost is visible to the reviewer. I proved the test works by planting an extra dimension and a renamed metric; both failed.
3. **Why is `CheckoutsRejected` one metric instead of one per reason?**
   A dimension like `reason=out_of_stock` would make every reason a separately billed metric, and the set of reasons is open-ended. The count answers "is something wrong", and the reason is in the log line next to it, where a bounded Logs Insights query finds it.
4. **Why doesn't the orders error alarm catch a cart-service outage?**
   Found during this step: orders catches cart failures and returns 502, and Lambda only counts raised exceptions and timeouts as errors. So the fix belongs in the alarm design, for example alarming on cart's own errors, or counting 5xx responses, which would cost a metric and has to go through the ledger.
5. **What did EMF cost in log volume?**
   Measured, 204 to 214 bytes per line, one line per checkout and one per fulfilment batch of up to ten. About 234 bytes per order on top of about 1.8 KB of ordinary logs, or roughly 0.02 GB across a full benchmark pass.
