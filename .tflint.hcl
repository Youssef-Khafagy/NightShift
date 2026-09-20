# TFLint catches Terraform mistakes that `terraform validate` does not:
# deprecated syntax, unused declarations, and AWS-specific errors such as an
# invalid instance type or a name that breaks a service's naming rules.

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "aws" {
  enabled = true
  version = "0.49.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}
