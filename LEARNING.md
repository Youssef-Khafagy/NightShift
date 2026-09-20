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

One variable is deliberately left open. zlib's deflate output for a given level is stable in practice but is not guaranteed across versions, so a CI runner with a different zlib could in principle produce different bytes from identical inputs. That will show up the same way M1's bug did, as a Terraform plan that is not empty, and the fix is already chosen: `ZIP_STORED`. The layer is 28 MiB unpacked and 7.7 MiB compressed, so an uncompressed archive still sits well inside the 50 MiB limit.

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
