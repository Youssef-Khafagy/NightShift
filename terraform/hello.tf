# M1's only workload: a function that proves the whole path works, from a
# pull request to a published version behind the live alias.

module "hello" {
  source = "./modules/lambda_service"

  name        = "${var.project}-hello"
  description = "M1 smoke test. Returns JSON and echoes the correlation ID."
  source_dir  = "${path.root}/../src/hello"

  environment = {
    SERVICE_NAME = "hello"
  }

  # Attached so that something exercises the shared layer on the real runtime
  # before the services that depend on it are written. A cross-built psycopg
  # wheel that cannot load should fail here, not inside checkout.
  layer_arns = [aws_lambda_layer_version.deps.arn]

  # Proves the tracing plumbing and the scoped X-Ray policy work. Traces are
  # free to 100,000 per month and this function is invoked by hand.
  tracing_mode = "Active"

  # The 5 second default was not enough for the layer probe's cold start at
  # 128 MB. Raised to measure rather than to paper over it; a timeout costs
  # nothing when unused, because Lambda bills actual duration.
  timeout = 30

  log_retention_days  = var.log_retention_days
  create_function_url = true
}
