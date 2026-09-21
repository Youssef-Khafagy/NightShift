# The cart store.
#
# DynamoDB rather than a second SQL table because a cart is a single document
# read and written by key, which is what DynamoDB is cheapest and simplest at.
# It also gives the chaos framework a second data store with an entirely
# different failure mode from DSQL.

resource "aws_dynamodb_table" "cart" {
  name = "${var.project}-cart"

  # PROVISIONED, not PAY_PER_REQUEST. The free allowance is 25 RCU and 25 WCU
  # per region; on-demand has no free capacity tier at all. Capacity is
  # tracked in COST.md so the total across every table stays inside 25.
  billing_mode   = "PROVISIONED"
  read_capacity  = var.cart_read_capacity
  write_capacity = var.cart_write_capacity

  hash_key = "cart_id"

  attribute {
    name = "cart_id"
    type = "S"
  }

  # Carts expire. TTL deletes are free and do not consume write capacity,
  # which keeps storage inside the 25 GB allowance without a cleanup job.
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  # Explicitly off. Point-in-time recovery is billed per GB of table size,
  # and every row here is synthetic and regenerable.
  point_in_time_recovery {
    enabled = false
  }

  # No auto scaling anywhere in this project: target tracking creates
  # CloudWatch alarms, and the free allowance is ten alarms that are already
  # spoken for in M3.
  deletion_protection_enabled = false
}
