# The Actor: runs one owner-approved action from the allowlist, verifies it,
# and audits it (actor/). It never talks to a model.
#
# Who can make it act: only an identity allowed to invoke it. There is no
# resource policy on this function (no function URL, no EventBridge), so
# that is IAM principals in this account with lambda:InvokeFunction on it:
# today the owner. The agent's role has no invoke permission, and the
# Investigator role denies every invoke.
#
# What it can do: exactly the five actions, on exactly these resources.
# Three layers, as for the Investigator: a scoped policy, explicit denies,
# and a permissions boundary listing only these actions.

locals {
  actor_name = "${var.project}-actor"
  rollback_function_arns = flatten([
    for s in ["cart", "orders", "payments", "fulfillment"] : [
      "arn:aws:lambda:${local.region}:${local.account_id}:function:${var.project}-${s}",
      "arn:aws:lambda:${local.region}:${local.account_id}:function:${var.project}-${s}:*",
    ]
  ])
  fulfillment_arn     = "arn:aws:lambda:${local.region}:${local.account_id}:function:${var.project}-fulfillment"
  placed_orders_arn   = "arn:aws:sqs:${local.region}:${local.account_id}:${var.project}-placed-orders"
  placed_orders_dlq   = "arn:aws:sqs:${local.region}:${local.account_id}:${var.project}-placed-orders-dlq"
  deployments_arn     = "arn:aws:dynamodb:${local.region}:${local.account_id}:table/${var.project}-deployments"
  flag_parameter_arns = ["${local.flag_arn_prefix}/payments_degraded_mode", "${local.flag_arn_prefix}/checkout_rate_limit"]

  actor_actions = [
    "lambda:GetAlias",
    "lambda:UpdateAlias",
    "lambda:ListEventSourceMappings",
    "lambda:UpdateEventSourceMapping",
    "dynamodb:Query",
    "dynamodb:GetItem",
    "dynamodb:PutItem",
    "dynamodb:UpdateItem",
    "ssm:GetParameter",
    "ssm:PutParameter",
    "sqs:GetQueueUrl",
    "sqs:GetQueueAttributes",
    "sqs:StartMessageMoveTask",
    "sqs:ListMessageMoveTasks",
    "sqs:ReceiveMessage",
    "sqs:DeleteMessage",
    "sqs:SendMessage",
    "cloudwatch:DescribeAlarms",
    # The module's own logging policy, which the boundary must also allow.
    "logs:CreateLogStream",
    "logs:PutLogEvents",
  ]
}

data "aws_iam_policy_document" "actor_boundary" {
  statement {
    sid       = "AllowlistOnly"
    effect    = "Allow"
    actions   = local.actor_actions
    resources = ["*"]
  }
}

resource "aws_iam_policy" "actor_boundary" {
  name        = "${local.actor_name}-boundary"
  description = "The most the Actor can ever do: the five allowlisted actions and its own bookkeeping."
  policy      = data.aws_iam_policy_document.actor_boundary.json
}

data "aws_iam_policy_document" "actor" {
  statement {
    sid       = "RollbackAlias"
    effect    = "Allow"
    actions   = ["lambda:GetAlias", "lambda:UpdateAlias"]
    resources = local.rollback_function_arns
  }

  statement {
    sid       = "DeploymentsRecord"
    effect    = "Allow"
    actions   = ["dynamodb:Query", "dynamodb:PutItem"]
    resources = [local.deployments_arn]
  }

  statement {
    sid       = "OperationalFlags"
    effect    = "Allow"
    actions   = ["ssm:GetParameter", "ssm:PutParameter"]
    resources = local.flag_parameter_arns
  }

  statement {
    sid       = "FindQueueTrigger"
    effect    = "Allow"
    actions   = ["lambda:ListEventSourceMappings"]
    resources = ["*"]
  }

  # Pause and resume: only the mapping whose function is fulfillment.
  statement {
    sid       = "PauseResumeConsumer"
    effect    = "Allow"
    actions   = ["lambda:UpdateEventSourceMapping"]
    resources = ["arn:aws:lambda:${local.region}:${local.account_id}:event-source-mapping:*"]
    condition {
      test     = "ArnLike"
      variable = "lambda:FunctionArn"
      values   = ["${local.fulfillment_arn}*"]
    }
  }

  statement {
    sid       = "QueueState"
    effect    = "Allow"
    actions   = ["sqs:GetQueueUrl", "sqs:GetQueueAttributes"]
    resources = [local.placed_orders_arn, local.placed_orders_dlq]
  }

  # A DLQ redrive, with the minimum permissions AWS documents: the move task
  # receives and deletes on the DLQ and sends to the source queue.
  statement {
    sid    = "RedriveFromDlq"
    effect = "Allow"
    actions = [
      "sqs:StartMessageMoveTask",
      "sqs:ListMessageMoveTasks",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
    ]
    resources = [local.placed_orders_dlq]
  }

  statement {
    sid       = "RedriveToSource"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [local.placed_orders_arn]
  }

  statement {
    sid       = "WatchAlarms"
    effect    = "Allow"
    actions   = ["cloudwatch:DescribeAlarms"]
    resources = ["arn:aws:cloudwatch:${local.region}:${local.account_id}:alarm:*"]
  }

  # Approvals (consume), the lock, the rate budget and the audit log.
  statement {
    sid       = "ApprovalsAndAudit"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [local.investigations_table_arn]
  }

  statement {
    sid    = "DenyEverythingDangerous"
    effect = "Deny"
    actions = [
      "iam:*",
      "sts:AssumeRole",
      "sts:AssumeRoleWithWebIdentity",
      "sts:AssumeRoleWithSAML",
      "lambda:CreateFunction",
      "lambda:DeleteFunction",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
      "lambda:PublishVersion",
      "lambda:AddPermission",
      "lambda:RemovePermission",
      "lambda:DeleteAlias",
      "lambda:CreateEventSourceMapping",
      "lambda:DeleteEventSourceMapping",
      "lambda:InvokeFunction",
      "lambda:InvokeFunctionUrl",
      "dynamodb:DeleteTable",
      "dynamodb:UpdateTable",
      "dynamodb:DeleteItem",
      "sqs:DeleteQueue",
      "sqs:PurgeQueue",
      "sqs:SetQueueAttributes",
      "ssm:DeleteParameter",
      "ssm:DeleteParameters",
      "cloudwatch:DeleteAlarms",
      "cloudwatch:PutMetricAlarm",
      "cloudwatch:SetAlarmState",
      "events:*",
      "dsql:*",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "DenyTerraformState"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = ["arn:aws:s3:::${var.state_bucket}", "arn:aws:s3:::${var.state_bucket}/*"]
  }
}

module "actor" {
  source = "./modules/lambda_service"

  name        = local.actor_name
  description = "Runs one owner-approved action, verifies it, audits it."

  source_dir = "${path.root}/../actor_lambda"
  extra_packages = {
    actor = "${path.root}/../actor"
    agent = "${path.root}/../agent"
    ops   = "${path.root}/../ops"
  }
  handler = "handler.handler"

  tracing_mode = "PassThrough"
  # Verification watches the alarms for up to 10 minutes.
  timeout              = 900
  reserved_concurrency = 1

  extra_policy_json    = data.aws_iam_policy_document.actor.json
  permissions_boundary = aws_iam_policy.actor_boundary.arn
  log_retention_days   = var.log_retention_days
  create_function_url  = false
}

# An action is never retried by Lambda: a retry would find the approval
# already used and refuse, but there is nothing to gain from trying.
resource "aws_lambda_function_event_invoke_config" "actor" {
  function_name                = module.actor.function_name
  qualifier                    = "live"
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 900
}
