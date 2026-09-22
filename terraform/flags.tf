# Operational feature flags. Read by the services through src/common/flags.py,
# which caches each value for 30 seconds per execution environment.
#
# Terraform owns that these parameters exist, their type and their allowed
# values. It does not own their current value. During an incident an operator,
# and from M6 the agent, flips a flag directly in SSM, and a `terraform apply`
# that happened to run in the middle of that must not quietly flip it back.
# `ignore_changes = [value]` is what makes that true: the value below is only
# the starting value.
#
# Both are plain String parameters in the Standard tier: free, 4 KB maximum,
# no KMS involved. Advanced parameters are billed per parameter-month and can
# never be downgraded, so the tier is stated explicitly rather than left to the
# account default.

locals {
  flag_prefix = "/${var.project}/flags"

  # Built from names rather than read from the resources' arn attributes, for
  # the same reason as local.cart_function_arn in services.tf: an ARN that is
  # unknown until apply makes the services' policy documents unknown, and the
  # module's count on its policy cannot be planned. The name's leading slash
  # is dropped in the ARN: parameter/nightshift/flags/x.
  flag_arn_prefix = "arn:aws:ssm:${local.region}:${local.account_id}:parameter${local.flag_prefix}"
}

resource "aws_ssm_parameter" "payments_degraded_mode" {
  name        = "${local.flag_prefix}/payments_degraded_mode"
  description = "true: fulfillment-worker defers payment and leaves orders in placed. Recover with scripts/replay_placed_orders.py after clearing."
  type        = "String"
  tier        = "Standard"
  value       = "false"

  # SSM rejects any other value at write time, so a typo during an incident
  # fails loudly at the operator's keyboard instead of silently reading as
  # the default inside the service.
  allowed_pattern = "^(true|false)$"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "checkout_rate_limit" {
  name        = "${local.flag_prefix}/checkout_rate_limit"
  description = "Checkouts per second per orders-service execution environment. 0 means no limit. Reserved concurrency is 2, so the real ceiling is twice this."
  type        = "String"
  tier        = "Standard"
  value       = "0"

  allowed_pattern = "^[0-9]{1,4}$"

  lifecycle {
    ignore_changes = [value]
  }
}
