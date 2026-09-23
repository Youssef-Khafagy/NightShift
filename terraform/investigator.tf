# The Investigator role: what the on-call agent's read-only tools run as.
#
# Three layers, each doing a different job:
#
# 1. The inline policy grants exactly the reads the ten tools make, scoped to
#    this project's resources wherever the action supports it. Some actions
#    (GetMetricStatistics, GetTraceSummaries, LookupEvents,
#    ListEventSourceMappings) only accept "*".
# 2. Explicit denies: IAM, role chaining, the Terraform state bucket, every
#    Delete, invoking functions, and reading messages off a queue (a receive
#    changes a message's visibility, so it is not a read). An explicit deny
#    beats any allow, including one added later by mistake.
# 3. A permissions boundary: the most this role can ever do, whatever policy
#    is attached to it later. It lists the same read actions plus the two
#    checkpoint actions and nothing else, so attaching an admin policy to
#    this role would still grant only reads.
#
# The one write: GetItem and PutItem on nightshift-investigations, the
# agent's own checkpoints (M5 step 4). Every other DynamoDB write is denied.
#
# Locally the owner's user assumes it (agent/aws.py); in Lambda the agent
# function's own role assumes it (agent.tf).

locals {
  investigator_role_name = "${var.project}-investigator"

  alarm_arns     = "arn:aws:cloudwatch:${local.region}:${local.account_id}:alarm:${var.project}-*"
  topology_param = "arn:aws:ssm:${local.region}:${local.account_id}:parameter/${var.project}/topology"
  flags_params   = "${local.flag_arn_prefix}/*"

  # Every action any tool may call. The boundary allows these and nothing
  # else; the inline policy allows them on narrower resources.
  investigator_actions = [
    "cloudwatch:DescribeAlarms",
    "cloudwatch:DescribeAlarmHistory",
    "cloudwatch:GetMetricStatistics",
    "logs:StartQuery",
    "logs:GetQueryResults",
    "logs:StopQuery",
    "xray:GetTraceSummaries",
    "dynamodb:Query",
    "cloudtrail:LookupEvents",
    "sqs:GetQueueUrl",
    "sqs:GetQueueAttributes",
    "lambda:GetAlias",
    "lambda:GetFunctionConfiguration",
    "lambda:GetFunctionConcurrency",
    "lambda:ListEventSourceMappings",
    "ssm:GetParameter",
    "ssm:GetParameters",
    # The agent's own checkpoints, in the investigations table only.
    "dynamodb:GetItem",
    "dynamodb:PutItem",
  ]
}

data "aws_iam_policy_document" "investigator_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type = "AWS"
      identifiers = [
        "arn:aws:iam::${local.account_id}:user/${var.operator_user_name}",
        # The investigator Lambda's own role (agent.tf). Read from the module
        # so Terraform creates that role first: IAM rejects a trust policy
        # that names a principal which does not exist yet.
        module.agent.execution_role_arn,
      ]
    }
  }
}

data "aws_iam_policy_document" "investigator_boundary" {
  statement {
    sid       = "ReadsOnly"
    effect    = "Allow"
    actions   = local.investigator_actions
    resources = ["*"]
  }
}

resource "aws_iam_policy" "investigator_boundary" {
  name        = "${local.investigator_role_name}-boundary"
  description = "The most the Investigator role can ever do: the agent's read actions, nothing else."
  policy      = data.aws_iam_policy_document.investigator_boundary.json
}

resource "aws_iam_role" "investigator" {
  name                 = local.investigator_role_name
  assume_role_policy   = data.aws_iam_policy_document.investigator_trust.json
  permissions_boundary = aws_iam_policy.investigator_boundary.arn
  max_session_duration = 3600
}

data "aws_iam_policy_document" "investigator" {
  statement {
    sid       = "AlarmHistory"
    effect    = "Allow"
    actions   = ["cloudwatch:DescribeAlarmHistory"]
    resources = [local.alarm_arns]
  }

  # Listing alarms by prefix is authorised against alarm:*, not against the
  # alarms it returns, so a grant on nightshift-* alarms denies the list
  # (found live, 2026-09-23). Every alarm in this account is the project's,
  # and reading an alarm's definition changes nothing.
  statement {
    sid       = "Alarms"
    effect    = "Allow"
    actions   = ["cloudwatch:DescribeAlarms"]
    resources = ["arn:aws:cloudwatch:${local.region}:${local.account_id}:alarm:*"]
  }

  # GetMetricStatistics, never GetMetricData: GetMetricData is always billed.
  statement {
    sid       = "Metrics"
    effect    = "Allow"
    actions   = ["cloudwatch:GetMetricStatistics"]
    resources = ["*"]
  }

  statement {
    sid       = "LogsInsights"
    effect    = "Allow"
    actions   = ["logs:StartQuery", "logs:GetQueryResults", "logs:StopQuery"]
    resources = [local.project_logs, "${local.project_logs}:*"]
  }

  statement {
    sid    = "UnscopableReads"
    effect = "Allow"
    actions = [
      "xray:GetTraceSummaries",
      "cloudtrail:LookupEvents",
      "lambda:ListEventSourceMappings",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "DeploymentsTable"
    effect    = "Allow"
    actions   = ["dynamodb:Query"]
    resources = [aws_dynamodb_table.deployments.arn]
  }

  # The one write this role makes: its own checkpoints. No tool exposes it
  # to the model; agent/store.py is the only caller.
  statement {
    sid       = "OwnCheckpoints"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.investigations.arn]
  }

  statement {
    sid       = "Queues"
    effect    = "Allow"
    actions   = ["sqs:GetQueueUrl", "sqs:GetQueueAttributes"]
    resources = [local.project_queues]
  }

  statement {
    sid    = "Functions"
    effect = "Allow"
    actions = [
      "lambda:GetAlias",
      "lambda:GetFunctionConfiguration",
      "lambda:GetFunctionConcurrency",
    ]
    resources = [local.project_functions]
  }

  statement {
    sid       = "TopologyAndFlags"
    effect    = "Allow"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = [local.topology_param, local.flags_params]
  }

  # --- Denies. They restate what the allows already leave out, on purpose:
  # a future edit that widens an allow cannot silently reach any of these.

  statement {
    sid    = "DenyIdentityAndChaining"
    effect = "Deny"
    actions = [
      "iam:*",
      "sts:AssumeRole",
      "sts:AssumeRoleWithWebIdentity",
      "sts:AssumeRoleWithSAML",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "DenyTerraformState"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = ["arn:aws:s3:::${var.state_bucket}", "arn:aws:s3:::${var.state_bucket}/*"]
  }

  statement {
    sid    = "DenyChangesAndSideEffects"
    effect = "Deny"
    actions = [
      "lambda:Delete*",
      "lambda:Update*",
      "lambda:Put*",
      "lambda:Invoke*",
      "lambda:Publish*",
      "sqs:Delete*",
      "sqs:Purge*",
      "sqs:Send*",
      "sqs:ReceiveMessage",
      "sqs:ChangeMessageVisibility*",
      "sqs:StartMessageMoveTask",
      "ssm:Delete*",
      "ssm:Put*",
      "logs:Delete*",
      "logs:Put*",
      "cloudwatch:Delete*",
      "cloudwatch:Put*",
      "cloudwatch:SetAlarmState",
      "events:*",
      "dsql:*",
      "sns:*",
    ]
    resources = ["*"]
  }

  # Every DynamoDB write is denied except on the investigations table, so
  # the deployments and cart tables stay out of reach whatever else changes.
  statement {
    sid    = "DenyTableWritesExceptCheckpoints"
    effect = "Deny"
    actions = [
      "dynamodb:Delete*",
      "dynamodb:Put*",
      "dynamodb:Update*",
      "dynamodb:BatchWrite*",
      "dynamodb:PartiQL*",
      "dynamodb:TransactWrite*",
    ]
    not_resources = [aws_dynamodb_table.investigations.arn]
  }
}

resource "aws_iam_role_policy" "investigator" {
  name   = local.investigator_role_name
  role   = aws_iam_role.investigator.id
  policy = data.aws_iam_policy_document.investigator.json
}
