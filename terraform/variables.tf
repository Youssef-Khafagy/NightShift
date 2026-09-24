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

variable "hello_memory_mb" {
  description = "Memory for the smoke-test function. CPU scales with memory, so this is the knob for import-heavy cold starts. Exists as a variable so it can be swept with -var during measurement."
  type        = number
  default     = 128
}

variable "cart_read_capacity" {
  description = "Provisioned RCU for the cart table. The free allowance is 25 RCU per region across every table and index, tracked in COST.md."
  type        = number
  default     = 5
}

variable "cart_write_capacity" {
  description = "Provisioned WCU for the cart table. See cart_read_capacity."
  type        = number
  default     = 5
}

variable "queue_consumer_enabled" {
  description = "The placed-orders event source mapping's state when it is first created. After that, scripts/consumer.py switches it (Terraform ignores changes, M6 decision A). Default false: an idle triggered queue spends about two thirds of the free SQS allowance doing nothing."
  type        = bool
  default     = false
}

variable "payment_latency_ms" {
  description = "Base latency the mock payment provider adds to every charge."
  type        = string
  default     = "40"
}

variable "payment_latency_jitter_ms" {
  description = "Extra random latency on top of payment_latency_ms."
  type        = string
  default     = "20"
}

variable "payment_error_rate" {
  description = "Fraction of charges the mock provider fails, 0 to 1."
  type        = string
  default     = "0"
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention. Kept short on purpose: the free allowance of 5 GB per month covers ingestion, storage and Logs Insights scans combined."
  type        = number
  default     = 3
}

variable "operator_user_name" {
  description = "The IAM user allowed to assume the Investigator role, so the agent runs locally with read-only rights instead of the owner's admin rights."
  type        = string
  default     = "youssef-admin"
}

variable "agent_trigger_enabled" {
  description = "Whether alarms start investigations. Default false: every chaos run would otherwise spend LLM quota. Turn on for a run, off afterwards, like the queue consumer."
  type        = bool
  default     = false
}

variable "agent_provider" {
  description = "LLM provider the investigator Lambda uses: groq, gemini or mistral. The model defaults per provider (agent/config.py); set agent_model to override."
  type        = string
  # Mistral ministral-14b (owner's choice for the M5 live check, 2026-09-23):
  # 25 to 40 s per investigation and no daily cap seen, where Groq's 200K
  # tokens a day holds about four investigations.
  default = "mistral"
  validation {
    condition     = contains(["groq", "gemini", "mistral"], var.agent_provider)
    error_message = "agent_provider must be groq, gemini or mistral."
  }
}

variable "agent_model" {
  description = "Model override for the investigator Lambda. Empty means the provider's default."
  type        = string
  default     = ""
}
