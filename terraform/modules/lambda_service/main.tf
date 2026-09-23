# One Lambda function, published as a version, reachable through a `live`
# alias. Nothing ever points at $LATEST, so a deploy is "move the alias" and
# a rollback is "move it back".

# The zip is built at plan time. output_base64sha256 is what tells Lambda the
# code changed: without it, Terraform would see the same filename and skip the
# update.
#
# Files are listed explicitly rather than zipping the whole directory with
# source_dir. Zipping a directory ships whatever happens to be sitting in it,
# which on a developer machine means __pycache__ and .pyc files. That is both
# a leak of local junk into a deployed artifact and a source of drift: the
# same commit produces a different zip on a laptop than on a clean CI runner,
# so every CI plan wants to redeploy. An allowlist makes the artifact depend
# only on what is committed.
data "archive_file" "this" {
  type        = "zip"
  output_path = "${path.root}/.build/${var.name}.zip"

  dynamic "source" {
    for_each = toset(concat([
      for f in fileset(var.source_dir, "**/*.py") : f
    ], var.extra_files))

    content {
      content  = file("${var.source_dir}/${source.value}")
      filename = source.value
    }
  }

  # Shared code, zipped under its package directory. Copying it into each
  # function's artifact rather than putting it in the dependency layer is
  # deliberate: shared code changes with the services, and a layer rebuild on
  # every edit would republish 7.7 MiB of unchanged wheels to redeploy a few
  # kilobytes of Python.
  dynamic "source" {
    for_each = var.shared_source_dir == null ? toset([]) : toset([
      for f in fileset(var.shared_source_dir, "**/*.py") : f
    ])

    content {
      content  = file("${var.shared_source_dir}/${source.value}")
      filename = "${var.shared_package}/${source.value}"
    }
  }
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

# X-Ray has no resource-level permissions for these actions, so "*" is the
# only option the service accepts. Granted only when tracing is on, rather
# than attaching AWSXRayDaemonWriteAccess to every function by reflex.
data "aws_iam_policy_document" "xray" {
  statement {
    effect = "Allow"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
      "xray:GetSamplingStatisticSummaries",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "extra" {
  count = var.extra_policy_json == null ? 0 : 1

  name   = "service"
  role   = aws_iam_role.this.id
  policy = var.extra_policy_json
}

resource "aws_iam_role_policy" "xray" {
  count = var.tracing_mode == "Active" ? 1 : 0

  name   = "xray"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.xray.json
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

  # arm64 (Graviton) is cheaper per GB-second than x86_64 once past the free
  # tier. The free allowance itself is the same for both architectures, so
  # this does not buy extra free capacity. Every dependency must ship an
  # aarch64 wheel.
  architectures = ["arm64"]

  filename         = data.archive_file.this.output_path
  source_code_hash = data.archive_file.this.output_base64sha256

  memory_size = var.memory_size
  timeout     = var.timeout

  layers = var.layer_arns

  # Lambda's own sampling decides which invocations produce a trace. The rate
  # is fixed at 1 request/second plus 5% of the remainder and cannot be
  # configured, so the only lever on trace volume is how many requests we send.
  tracing_config {
    mode = var.tracing_mode
  }

  # Publish an immutable version on every code change, so the alias has
  # something to point at and an old version stays available to roll back to.
  publish = true

  reserved_concurrent_executions = var.reserved_concurrency

  logging_config {
    log_format            = "JSON"
    application_log_level = "INFO"
    # INFO, not WARN: Lambda writes its platform lines (START, REPORT with
    # duration and memory, and the init report on a cold start) at INFO. At
    # WARN none of them reach CloudWatch, so cold starts and slow invocations
    # are invisible in the logs the agent searches. Owner decision 2026-09-22:
    # worth the extra REPORT line per invocation.
    system_log_level = "INFO"
    log_group        = aws_cloudwatch_log_group.this.name
  }

  dynamic "environment" {
    for_each = length(var.environment) > 0 ? [1] : []
    content {
      variables = var.environment
    }
  }

  depends_on = [
    aws_iam_role_policy.logs,
    aws_iam_role_policy.xray,
    aws_iam_role_policy.extra,
    aws_cloudwatch_log_group.this,
  ]
}

resource "aws_lambda_alias" "live" {
  name             = "live"
  description      = "The version callers actually reach."
  function_name    = aws_lambda_function.this.function_name
  function_version = aws_lambda_function.this.version

  # Terraform creates the alias and publishes new versions, but never moves
  # it afterwards. scripts/deploy.py and scripts/rollback.py move it and
  # record every move in the deployments table. If Terraform owned this
  # value, a rollback done outside Terraform (by the rollback script, or by
  # the agent in M6) would be drift, and the next routine apply would
  # silently undo it.
  lifecycle {
    ignore_changes = [function_version]
  }
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
