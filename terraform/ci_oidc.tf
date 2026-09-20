# GitHub Actions authenticates to AWS with OpenID Connect. GitHub mints a
# short-lived JWT for each workflow run, AWS validates it against GitHub's
# public keys, and STS hands back temporary credentials. No access keys are
# stored in GitHub.
#
# Bootstrapping note: CI cannot create the roles CI needs. The first
# `terraform apply` is run locally by the owner. After that, CI uses these
# roles, and the explicit deny below stops CI from changing them.

locals {
  # Immutable OIDC subject claim. Repositories created after 2026-07-15 embed
  # the numeric owner and repo IDs, so renaming the repo or the account does
  # not silently hand access to whoever claims the old name. The older
  # "repo:OWNER/REPO:..." format does not validate for these repositories.
  gh_subject = "repo:${var.github_owner}@${var.github_owner_id}/${var.github_repo}@${var.github_repo_id}"

  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  ci_plan_role_name  = "${var.project}-ci-plan"
  ci_apply_role_name = "${var.project}-ci-apply"

  ci_role_arns = [
    "arn:aws:iam::${local.account_id}:role/${local.ci_plan_role_name}",
    "arn:aws:iam::${local.account_id}:role/${local.ci_apply_role_name}",
  ]

  project_functions = "arn:aws:lambda:${local.region}:${local.account_id}:function:${var.project}-*"
  project_roles     = "arn:aws:iam::${local.account_id}:role/${var.project}-*"
  project_logs      = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${var.project}-*"
}

resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  # The audience. GitHub puts this in the token's aud claim, and the trust
  # policies below check it again.
  client_id_list = ["sts.amazonaws.com"]

  # thumbprint_list is deliberately not set. AWS verifies GitHub's JWKS
  # endpoint against its own library of trusted root CAs, and only falls back
  # to thumbprints when the certificate is not signed by one of them. When no
  # thumbprint is given at creation, IAM retrieves one itself. Pinning a
  # thumbprint here would mean broken deploys every time GitHub rotates its
  # certificate chain, which is the classic failure of the old guides that
  # hardcode 6938fd4d98bab03faadb97b34396831e3780aea1.
}

# ---------------------------------------------------------------------------
# Plan role: read-only, used on pull requests.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ci_plan_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Pull requests, plus manual runs on main. Exact matches, no wildcards:
    # a wildcard such as "repo:owner/repo:*" would also match every branch
    # and every fork's pull_request_target.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "${local.gh_subject}:pull_request",
        "${local.gh_subject}:ref:refs/heads/main",
      ]
    }
  }
}

resource "aws_iam_role" "ci_plan" {
  name                 = local.ci_plan_role_name
  description          = "GitHub Actions: terraform plan on pull requests. Read-only."
  assume_role_policy   = data.aws_iam_policy_document.ci_plan_trust.json
  max_session_duration = 3600
}

# A plan needs to read every resource type the configuration touches, and that
# set grows every milestone. AWS-managed ReadOnlyAccess is deliberately broad
# here: a role that cannot write cannot break anything, and scoping reads
# would mean a CI failure every time a new service is added. The apply role
# below, which can actually change things, is scoped by hand.
resource "aws_iam_role_policy_attachment" "ci_plan_readonly" {
  role       = aws_iam_role.ci_plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# ---------------------------------------------------------------------------
# Apply role: write, used only by the manually triggered apply workflow.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ci_apply_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # main only. A pull request from a fork can change workflow files, so
    # allowing pull_request here would let anyone who opens a PR run apply.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${local.gh_subject}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "ci_apply" {
  name                 = local.ci_apply_role_name
  description          = "GitHub Actions: terraform apply, manually triggered on main."
  assume_role_policy   = data.aws_iam_policy_document.ci_apply_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "ci_apply" {
  # Terraform state and its lock object.
  statement {
    sid       = "StateBucketList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.state_bucket}"]
  }

  statement {
    sid    = "StateObjects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["arn:aws:s3:::${var.state_bucket}/${var.project}/*"]
  }

  # Lambda, scoped to this project's function name prefix. Listed out rather
  # than "lambda:*" so that adding a dangerous new Lambda action to the API
  # does not silently widen this role.
  statement {
    sid    = "ProjectLambda"
    effect = "Allow"
    actions = [
      "lambda:CreateFunction",
      "lambda:DeleteFunction",
      "lambda:GetFunction",
      "lambda:GetFunctionConfiguration",
      "lambda:GetFunctionCodeSigningConfig",
      "lambda:GetFunctionConcurrency",
      "lambda:GetFunctionEventInvokeConfig",
      "lambda:GetRuntimeManagementConfig",
      "lambda:PutRuntimeManagementConfig",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
      "lambda:PutFunctionConcurrency",
      "lambda:DeleteFunctionConcurrency",
      "lambda:PublishVersion",
      "lambda:ListVersionsByFunction",
      "lambda:CreateAlias",
      "lambda:DeleteAlias",
      "lambda:GetAlias",
      "lambda:UpdateAlias",
      "lambda:ListAliases",
      "lambda:CreateFunctionUrlConfig",
      "lambda:UpdateFunctionUrlConfig",
      "lambda:DeleteFunctionUrlConfig",
      "lambda:GetFunctionUrlConfig",
      "lambda:AddPermission",
      "lambda:RemovePermission",
      "lambda:GetPolicy",
      "lambda:TagResource",
      "lambda:UntagResource",
      "lambda:ListTags",
    ]
    resources = [local.project_functions]
  }

  statement {
    sid    = "LambdaAccountReads"
    effect = "Allow"
    actions = [
      "lambda:GetAccountSettings",
      "lambda:ListFunctions",
    ]
    resources = ["*"]
  }

  # IAM, scoped to roles named nightshift-*. The explicit deny further down
  # carves the two CI roles back out.
  statement {
    sid    = "ProjectRoles"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:GetRole",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:PutRolePolicy",
      "iam:GetRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:UpdateAssumeRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:ListRoleTags",
      "iam:PassRole",
    ]
    resources = [local.project_roles]
  }

  statement {
    sid    = "ReadOidcProvider"
    effect = "Allow"
    actions = [
      "iam:GetOpenIDConnectProvider",
      "iam:ListOpenIDConnectProviderTags",
    ]
    resources = [aws_iam_openid_connect_provider.github.arn]
  }

  # CloudWatch Logs for this project's functions.
  statement {
    sid    = "ProjectLogGroups"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:PutRetentionPolicy",
      "logs:DeleteRetentionPolicy",
      "logs:TagResource",
      "logs:UntagResource",
      "logs:ListTagsForResource",
    ]
    # CloudWatch Logs has two ARN shapes for the same log group. Actions that
    # act on the group itself (ListTagsForResource, PutRetentionPolicy) expect
    # "log-group:NAME". Actions that reach the streams inside it expect
    # "log-group:NAME:*". Granting only the second form fails the first set,
    # which is what broke the first CI apply.
    resources = [
      local.project_logs,
      "${local.project_logs}:*",
    ]
  }

  statement {
    sid       = "DescribeLogGroups"
    effect    = "Allow"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"]
  }

  # --- Denies. An explicit deny always beats an allow. ---

  # CI must not be able to rewrite the roles that grant CI its access, or the
  # trust policy that decides which repo may assume them. Only Get/List are
  # left, because Terraform reads these resources on every refresh.
  statement {
    sid    = "DenyChangingCiRoles"
    effect = "Deny"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:PutRolePermissionsBoundary",
      "iam:DeleteRolePermissionsBoundary",
    ]
    resources = local.ci_role_arns
  }

  statement {
    sid    = "DenyChangingOidcProvider"
    effect = "Deny"
    actions = [
      "iam:CreateOpenIDConnectProvider",
      "iam:DeleteOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint",
      "iam:AddClientIDToOpenIDConnectProvider",
      "iam:RemoveClientIDFromOpenIDConnectProvider",
      "iam:TagOpenIDConnectProvider",
      "iam:UntagOpenIDConnectProvider",
    ]
    resources = ["*"]
  }

  # Identities that outlive a workflow run, and anything that could move the
  # account onto a paid plan or silence the cost alarms.
  statement {
    sid    = "DenyPersistentIdentitiesAndAccountChanges"
    effect = "Deny"
    actions = [
      "iam:CreateUser",
      "iam:CreateAccessKey",
      "iam:CreateLoginProfile",
      "iam:UpdateLoginProfile",
      "iam:CreateServiceSpecificCredential",
      "iam:DeactivateMFADevice",
      "organizations:*",
      "account:*",
      "budgets:ModifyBudget",
      "budgets:DeleteBudget",
      "ce:*",
    ]
    resources = ["*"]
  }

  # Terraform must never be able to delete the bucket holding its own state.
  statement {
    sid    = "DenyStateBucketDestruction"
    effect = "Deny"
    actions = [
      "s3:DeleteBucket",
      "s3:PutBucketPolicy",
      "s3:DeleteBucketPolicy",
      "s3:PutBucketVersioning",
      "s3:PutBucketPublicAccessBlock",
    ]
    resources = [
      "arn:aws:s3:::${var.state_bucket}",
      "arn:aws:s3:::${var.state_bucket}/*",
    ]
  }
}

resource "aws_iam_role_policy" "ci_apply" {
  name   = "${var.project}-ci-apply"
  role   = aws_iam_role.ci_apply.id
  policy = data.aws_iam_policy_document.ci_apply.json
}
