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

  log_retention_days  = var.log_retention_days
  create_function_url = true
}
