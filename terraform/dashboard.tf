# The dashboard's live views (M8): what the owner's signed-in pages on Vercel
# may do in this account.
#
# How Vercel gets credentials, with no keys stored anywhere: every Vercel
# function receives a short-lived OIDC token signed by Vercel that names the
# team, the project and the environment. AWS trusts Vercel's issuer for this
# one role, and STS exchanges the token for one-hour credentials. It is the
# same mechanism as GitHub Actions in ci_oidc.tf, with a different issuer.
#
# Three layers, as for the Investigator and the Actor:
#
# 1. The trust policy admits exactly one subject: this project's production
#    environment. A preview deployment of a pull request gets a token whose
#    subject ends in ":environment:preview", which does not match.
# 2. The inline policy grants what the live pages read and the two things
#    they may do: reject an approval (an UpdateItem limited to the approval's
#    status fields) and approve one (invoke the Actor, which re-checks
#    everything itself). Explicit denies cover the rest, including Scan and
#    GetMetricData, which would cost capacity or money.
# 3. A permissions boundary: the most this role can ever do.
#
# Applied locally by the owner: the CI apply role is denied every change to
# OIDC providers (ci_oidc.tf), so CI can read this provider but never create
# or alter it.

locals {
  dashboard_role_name = "${var.project}-dashboard"
  vercel_issuer_host  = "oidc.vercel.com/${var.vercel_team}"

  # The Actor runs only through its live alias, as scripts/approve.py calls it.
  actor_live_arn = "arn:aws:lambda:${local.region}:${local.account_id}:function:${var.project}-actor:live"

  dashboard_actions = [
    "cloudwatch:DescribeAlarms",
    "cloudwatch:GetMetricStatistics",
    "dynamodb:GetItem",
    "dynamodb:Query",
    "dynamodb:UpdateItem",
    "lambda:InvokeFunction",
  ]

  # Every attribute a rejection touches (agent/approvals.py, reject). With
  # dynamodb:Attributes, the key attributes must be listed too, and so must
  # any attribute a condition reads: status is both.
  rejection_attributes = ["investigation_id", "item", "status", "rejected_by", "rejected_at"]
}

resource "aws_iam_openid_connect_provider" "vercel" {
  url = "https://${local.vercel_issuer_host}"

  # The token's default audience. The trust policy checks it again.
  client_id_list = ["https://vercel.com/${var.vercel_team}"]

  # No thumbprint, for the same reason as the GitHub provider: IAM verifies
  # the issuer against its trusted root CAs and fetches one itself if needed.
}

data "aws_iam_policy_document" "dashboard_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.vercel.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.vercel_issuer_host}:aud"
      values   = ["https://vercel.com/${var.vercel_team}"]
    }

    # Exact match, production only. Renaming the team or the project changes
    # this claim, and access stops until this value is updated.
    condition {
      test     = "StringEquals"
      variable = "${local.vercel_issuer_host}:sub"
      values   = ["owner:${var.vercel_team}:project:${var.vercel_project}:environment:production"]
    }
  }
}

data "aws_iam_policy_document" "dashboard_boundary" {
  statement {
    sid       = "LiveViewsOnly"
    effect    = "Allow"
    actions   = local.dashboard_actions
    resources = ["*"]
  }
}

resource "aws_iam_policy" "dashboard_boundary" {
  name        = "${local.dashboard_role_name}-boundary"
  description = "The most the dashboard role can ever do: read alarms, metrics and investigations, reject an approval, invoke the Actor."
  policy      = data.aws_iam_policy_document.dashboard_boundary.json
}

resource "aws_iam_role" "dashboard" {
  name                 = local.dashboard_role_name
  assume_role_policy   = data.aws_iam_policy_document.dashboard_trust.json
  permissions_boundary = aws_iam_policy.dashboard_boundary.arn
  max_session_duration = 3600
}

data "aws_iam_policy_document" "dashboard" {
  # Listing alarms is checked against alarm:*, not against each alarm's ARN
  # (found in M5 step 3), so a prefix cannot be enforced here; the pages ask
  # for the nightshift- prefix themselves.
  statement {
    sid       = "Alarms"
    effect    = "Allow"
    actions   = ["cloudwatch:DescribeAlarms"]
    resources = ["arn:aws:cloudwatch:${local.region}:${local.account_id}:alarm:*"]
  }

  # GetMetricStatistics accepts no resource but "*". GetMetricData, which is
  # always billed, is denied below.
  statement {
    sid       = "Metrics"
    effect    = "Allow"
    actions   = ["cloudwatch:GetMetricStatistics"]
    resources = ["*"]
  }

  # The current investigation: the incident lock, its checkpoint, report and
  # approval items, each read by key or by one partition. Never Scan.
  statement {
    sid       = "ReadInvestigations"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.investigations.arn]
  }

  # Rejecting an approval, and nothing else: the update may touch only the
  # rejection's attributes, and may not return the item's other attributes.
  statement {
    sid       = "RejectApprovals"
    effect    = "Allow"
    actions   = ["dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.investigations.arn]

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:Attributes"
      values   = local.rejection_attributes
    }

    condition {
      test     = "StringEquals"
      variable = "dynamodb:ReturnValues"
      values   = ["NONE"]
    }
  }

  # Approving: hand the Actor the investigation, the item and the hash of
  # what the owner was shown. The Actor checks the hash, expiry and status in
  # one conditional write, and refuses anything that does not fit.
  statement {
    sid       = "InvokeActor"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [local.actor_live_arn]
  }

  statement {
    sid    = "DenyEverythingElse"
    effect = "Deny"
    actions = [
      "iam:*",
      "sts:AssumeRole",
      "sts:AssumeRoleWithSAML",
      "cloudwatch:GetMetricData",
      "cloudwatch:GetMetricWidgetImage",
      "cloudwatch:GetInsightRuleReport",
      "cloudwatch:PutMetricAlarm",
      "cloudwatch:DeleteAlarms",
      "cloudwatch:SetAlarmState",
      "dynamodb:Scan",
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:DeleteTable",
      "dynamodb:UpdateTable",
      "lambda:CreateFunction",
      "lambda:DeleteFunction",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
      "lambda:UpdateAlias",
      "lambda:PublishVersion",
      "lambda:AddPermission",
      "lambda:RemovePermission",
      "lambda:InvokeFunctionUrl",
      "ssm:*",
      "sqs:*",
      "dsql:*",
      "logs:*",
      "events:*",
    ]
    resources = ["*"]
  }

  # Invoke is allowed on the Actor's live alias only; every other function
  # (the agent, the store) is denied by name, not just left unallowed.
  statement {
    sid           = "DenyInvokingAnythingButTheActor"
    effect        = "Deny"
    actions       = ["lambda:InvokeFunction"]
    not_resources = [local.actor_live_arn]
  }

  statement {
    sid       = "DenyTerraformState"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = ["arn:aws:s3:::${var.state_bucket}", "arn:aws:s3:::${var.state_bucket}/*"]
  }
}

resource "aws_iam_role_policy" "dashboard" {
  name   = "${local.dashboard_role_name}-live-views"
  role   = aws_iam_role.dashboard.id
  policy = data.aws_iam_policy_document.dashboard.json
}
