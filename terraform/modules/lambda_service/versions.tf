# A module declares which providers it uses and the minimum versions it needs.
# The root module pins the exact version; these constraints only stop the
# module from being used with something too old to work.
terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4"
    }
  }
}
