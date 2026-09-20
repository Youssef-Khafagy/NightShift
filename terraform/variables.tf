variable "aws_region" {
  description = "Region for every resource in this project."
  type        = string
  default     = "ca-central-1"
}

variable "project" {
  description = "Name prefix and Project tag value for every resource."
  type        = string
  default     = "nightshift"
}

variable "state_bucket" {
  description = "S3 bucket holding Terraform state. Created by bootstrap/state/create-state-bucket.sh, not by Terraform, because Terraform cannot create its own backend."
  type        = string
  default     = "nightshift-tfstate-ca-central-1-2f0ad894"
}

variable "github_owner" {
  description = "GitHub account that owns the repo."
  type        = string
  default     = "Youssef-Khafagy"
}

variable "github_owner_id" {
  description = "Numeric GitHub account ID. Part of the immutable OIDC subject claim. Find it with: gh api users/OWNER --jq .id"
  type        = string
  default     = "232406487"
}

variable "github_repo" {
  description = "Repository name."
  type        = string
  default     = "NightShift"
}

variable "github_repo_id" {
  description = "Numeric GitHub repository ID. Part of the immutable OIDC subject claim. Find it with: gh api repos/OWNER/REPO --jq .id"
  type        = string
  default     = "1376738088"
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention. Kept short on purpose: the free allowance of 5 GB per month covers ingestion, storage and Logs Insights scans combined."
  type        = number
  default     = 3
}
