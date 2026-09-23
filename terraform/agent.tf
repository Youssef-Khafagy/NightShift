# The investigator: a CloudWatch alarm enters ALARM, EventBridge invokes the
# agent function, the agent investigates with read-only tools and writes a
# report. Nothing here can change the store.
#
# Two identities, on purpose:
# - the function's own role (this file) reads the LLM API keys, writes
#   checkpoints and reports, and may assume the Investigator role;
# - the Investigator role (investigator.tf) is what every tool runs as.
# So the tools never hold the keys, and the model's reach is identical in
# Lambda and on the laptop.

locals {
  agent_layer_zip = "${path.root}/../build/nightshift-agent-deps-layer.zip"
  secrets_prefix  = "/${var.project}/secrets"

  # Built from names, like cart_function_arn in services.tf. Reading them
  # from the resources makes this policy unknown whenever either resource
  # has a pending change (the Investigator role's trust policy does, in the
  # same apply), and the module's count on its policy then cannot be planned.
  investigator_role_arn    = "arn:aws:iam::${local.account_id}:role/${local.investigator_role_name}"
  investigations_table_arn = "arn:aws:dynamodb:${local.region}:${local.account_id}:table/${var.project}-investigations"
}

resource "aws_lambda_layer_version" "agent_deps" {
  layer_name  = "${var.project}-agent-deps"
  description = "Pydantic, built from requirements/agent-deps.lock"

  filename         = local.agent_layer_zip
  source_code_hash = filebase64sha256(local.agent_layer_zip)

  compatible_runtimes      = ["python3.14"]
  compatible_architectures = ["arm64"]
}

data "aws_iam_policy_document" "agent" {
  statement {
    sid       = "AssumeInvestigator"
    effect    = "Allow"
    actions   = ["sts:AssumeRole"]
    resources = [local.investigator_role_arn]
  }

  # The keys are SecureString parameters written by scripts/put_llm_keys.py,
  # not by Terraform, so their values never enter Terraform state.
  statement {
    sid       = "ReadKeys"
    effect    = "Allow"
    actions   = ["ssm:GetParameters"]
    resources = ["arn:aws:ssm:${local.region}:${local.account_id}:parameter${local.secrets_prefix}/*"]
  }

  # SecureString uses the AWS managed key (free). Decrypt only through SSM,
  # and only for our secrets: SSM puts the parameter's ARN in the encryption
  # context. Conditions rather than the key's ARN, which would need a KMS
  # lookup the CI roles are not allowed to make.
  statement {
    sid       = "DecryptKeysThroughSsm"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${local.region}.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "kms:EncryptionContext:PARAMETER_ARN"
      values   = ["arn:aws:ssm:${local.region}:${local.account_id}:parameter${local.secrets_prefix}/*"]
    }
  }

  # Checkpoints, reports, and the incident lock (a conditional PutItem, then
  # UpdateItem to join).
  statement {
    sid       = "Investigations"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [local.investigations_table_arn]
  }
}

module "agent" {
  source = "./modules/lambda_service"

  name        = "${var.project}-agent"
  description = "On-call investigator: read-only tools, an LLM, a report."

  source_dir        = "${path.root}/../agent_lambda"
  shared_source_dir = "${path.root}/../agent"
  shared_package    = "agent"
  handler           = "handler.handler"

  layer_arns   = [aws_lambda_layer_version.agent_deps.arn]
  tracing_mode = "PassThrough"

  # 15 minutes, Lambda's ceiling. The loop stops itself at 840 s
  # (agent/config.py) to save its checkpoint before Lambda would kill it.
  timeout = 900

  # One investigation at a time: the per-minute token limits are per
  # account, and incident correlation already folds related alarms into one.
  reserved_concurrency = 1

  environment = {
    AGENT_PROVIDER = var.agent_provider
    AGENT_MODEL    = var.agent_model
    SECRETS_PREFIX = local.secrets_prefix
  }

  extra_policy_json   = data.aws_iam_policy_document.agent.json
  log_retention_days  = var.log_retention_days
  create_function_url = false
}

# Retries after a timeout or crash resume from the checkpoint. Two is
# Lambda's maximum; an hour is plenty for an event to still be worth acting on.
resource "aws_lambda_function_event_invoke_config" "agent" {
  function_name                = module.agent.function_name
  qualifier                    = "live"
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
}

# Alarm state changes are AWS service events on the default bus: free.
resource "aws_cloudwatch_event_rule" "alarm_to_agent" {
  name        = "${var.project}-alarm-to-agent"
  description = "Start an investigation when a project alarm enters ALARM."
  state       = var.agent_trigger_enabled ? "ENABLED" : "DISABLED"

  event_pattern = jsonencode({
    source      = ["aws.cloudwatch"]
    detail-type = ["CloudWatch Alarm State Change"]
    detail = {
      alarmName = [{ prefix = "${var.project}-" }]
      state     = { value = ["ALARM"] }
    }
  })
}

resource "aws_cloudwatch_event_target" "agent" {
  rule = aws_cloudwatch_event_rule.alarm_to_agent.name
  arn  = module.agent.alias_arn
}

resource "aws_lambda_permission" "events_invoke_agent" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = module.agent.function_name
  qualifier     = "live"
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.alarm_to_agent.arn
}
