# Aurora DSQL: the orders database.
#
# Chosen because it is PostgreSQL compatible, has no instance to pay for, no
# VPC to put it in, and an Always Free allowance of 100,000 DPUs and 1 GB per
# month. An idle cluster scales to zero and costs nothing beyond storage, so
# leaving it up between sessions is free.
#
# What it is not: ordinary PostgreSQL. Repeatable Read is the only isolation
# level, concurrency control is optimistic so conflicting transactions fail at
# commit with SQLSTATE 40001 instead of blocking, DDL and DML need separate
# transactions with one DDL each, and there are no triggers, no PL/pgSQL and
# no temp tables. The schema and the checkout path in later steps are shaped
# by all of that.

resource "aws_dsql_cluster" "main" {
  # Off so `terraform destroy` works without a manual console step. This is a
  # portfolio project whose data is synthetic and regenerable; on anything
  # holding real data this would be true.
  deletion_protection_enabled = false

  # The AWS owned key is free. A customer managed KMS key would cost money
  # and is on the forbidden list in CLAUDE.md.
  kms_encryption_key = "AWS_OWNED_KMS_KEY"

  tags = {
    Name = "${var.project}-main"
  }
}

locals {
  # DSQL exposes no endpoint attribute; the hostname is built from the
  # generated cluster identifier. Verified against DNS after creation.
  dsql_endpoint = "${aws_dsql_cluster.main.identifier}.dsql.${local.region}.on.aws"
}
