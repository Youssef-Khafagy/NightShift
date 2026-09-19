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

### M0 concepts and interview questions

Added at the end of M0.
