# The dependency layer shared by every NightShift function.
#
# The zip is built by scripts/lambda_deps.py, not by Terraform, because
# building it means running pip against a pinned lock file. Terraform only
# publishes what is already on disk, so `terraform plan` fails with a missing
# file error if the build step has not run. Both workflows build it first.
#
# A layer version is immutable. Changing the zip publishes a new version and
# Terraform points the functions at it in the same apply.

locals {
  deps_layer_zip = "${path.root}/../build/nightshift-deps-layer.zip"
}

resource "aws_lambda_layer_version" "deps" {
  layer_name  = "${var.project}-deps"
  description = "psycopg and Powertools, built from requirements/lambda-deps.lock"

  filename = local.deps_layer_zip

  # Without this Terraform sees the same filename and never updates the layer.
  source_code_hash = filebase64sha256(local.deps_layer_zip)

  compatible_runtimes      = ["python3.14"]
  compatible_architectures = ["arm64"]
}
