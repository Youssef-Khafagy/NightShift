# One Lambda function, published as a version, reachable through a `live`
# alias. Nothing ever points at $LATEST, so a deploy is "move the alias" and
# a rollback is "move it back".

# The zip is built from the source directory at plan time. output_base64sha256
# is what tells Lambda the code changed: without it, Terraform would see the
# same filename and skip the update.
data "archive_file" "this" {
  type        = "zip"
  source_dir  = var.source_dir
  output_path = "${path.root}/.build/${var.name}.zip"
}

data "aws_iam_policy_document" "assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = "${var.name}-exec"
  description        = "Execution role for ${var.name}."
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

# Scoped by hand instead of attaching AWSLambdaBasicExecutionRole, which
# allows logs:CreateLogGroup on every log group in the account. Terraform
# creates the log group, so the function only needs to write to it.
data "aws_iam_policy_document" "logs" {
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.this.arn}:*"]
  }
}

resource "aws_iam_role_policy" "logs" {
  name   = "logs"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.logs.json
}

# Created explicitly so retention is set from the start. If Lambda creates the
# group on first invocation it defaults to "never expire", and the 5 GB/month
# free allowance covers storage as well as ingestion.
resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "this" {
  function_name = var.name
  description   = var.description
  role          = aws_iam_role.this.arn
  handler       = var.handler
  runtime       = var.runtime

  # arm64 (Graviton) is cheaper per GB-second than x86_64 and the free
  # allowance is measured in GB-seconds, so it stretches further.
  architectures = ["arm64"]

  filename         = data.archive_file.this.output_path
  source_code_hash = data.archive_file.this.output_base64sha256

  memory_size = var.memory_size
  timeout     = var.timeout

  # Publish an immutable version on every code change, so the alias has
  # something to point at and an old version stays available to roll back to.
  publish = true

  reserved_concurrent_executions = var.reserved_concurrency

  logging_config {
    log_format            = "JSON"
    application_log_level = "INFO"
    system_log_level      = "WARN"
    log_group             = aws_cloudwatch_log_group.this.name
  }

  dynamic "environment" {
    for_each = length(var.environment) > 0 ? [1] : []
    content {
      variables = var.environment
    }
  }

  depends_on = [
    aws_iam_role_policy.logs,
    aws_cloudwatch_log_group.this,
  ]
}

resource "aws_lambda_alias" "live" {
  name             = "live"
  description      = "The version callers actually reach."
  function_name    = aws_lambda_function.this.function_name
  function_version = aws_lambda_function.this.version
}

# A function URL is an HTTPS endpoint managed by Lambda itself. It is free,
# unlike API Gateway or a load balancer. AWS_IAM means every request must be
# SigV4-signed by a principal allowed to invoke this alias.
resource "aws_lambda_function_url" "live" {
  count = var.create_function_url ? 1 : 0

  function_name      = aws_lambda_function.this.function_name
  qualifier          = aws_lambda_alias.live.name
  authorization_type = "AWS_IAM"
}
