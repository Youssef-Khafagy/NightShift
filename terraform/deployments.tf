# The deployments table: one row every time a live alias moves, whether by a
# deploy, a rollback, or an automatic rollback after a failed smoke test.
#
# It is the history the on-call agent reads with list_recent_deployments in
# M5, so "what changed just before this started" has a real answer. Rows are
# written only by scripts/deploy.py and scripts/rollback.py.
#
# Keyed by service, then by the time of the move, so the recent history of
# one service is a single Query, newest first.

resource "aws_dynamodb_table" "deployments" {
  name = "${var.project}-deployments"

  # 1 RCU and 1 WCU, as budgeted in COST.md's capacity ledger. A deploy
  # writes at most one small row per service, a few times a day.
  billing_mode   = "PROVISIONED"
  read_capacity  = 1
  write_capacity = 1

  hash_key  = "service"
  range_key = "deployed_at"

  attribute {
    name = "service"
    type = "S"
  }

  # ISO 8601 in UTC with milliseconds, so string order is time order.
  attribute {
    name = "deployed_at"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }

  deletion_protection_enabled = false
}

# What deploy.py moves each alias to: exactly the versions this apply
# published, rather than whatever happens to be newest when it runs.
output "function_versions" {
  description = "Newest published version of each function, for scripts/deploy.py."
  value = {
    cart        = module.cart.published_version
    orders      = module.orders.published_version
    payments    = module.payments.published_version
    fulfillment = module.fulfillment.published_version
    hello       = module.hello.published_version
    agent       = module.agent.published_version
    actor       = module.actor.published_version
  }
}

output "deployments_table_name" {
  value = aws_dynamodb_table.deployments.name
}
