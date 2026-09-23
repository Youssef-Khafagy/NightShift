# The investigations table: the agent's checkpoints, one item per
# investigation ("checkpoint"), rewritten after every step so a crash or a
# Lambda timeout resumes where it stopped (agent/store.py).
#
# Budgeted in COST.md's capacity ledger at 5 RCU / 5 WCU. A step writes one
# item of a few KB every few seconds; burst capacity covers the odd larger
# one. No TTL and no streams: investigations are kept as benchmark evidence.

resource "aws_dynamodb_table" "investigations" {
  name           = "${var.project}-investigations"
  billing_mode   = "PROVISIONED"
  read_capacity  = 5
  write_capacity = 5

  hash_key  = "investigation_id"
  range_key = "item"

  attribute {
    name = "investigation_id"
    type = "S"
  }

  attribute {
    name = "item"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }

  deletion_protection_enabled = false
}
