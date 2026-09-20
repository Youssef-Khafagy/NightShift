provider "aws" {
  region = var.aws_region

  # Every resource that supports tags gets these automatically. The agent's
  # read-only IAM role is scoped by the Project tag later, so this is a
  # security control, not just bookkeeping.
  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
