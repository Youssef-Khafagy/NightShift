variable "name" {
  description = "Function name. Must start with the project prefix, because the CI apply role is scoped to that prefix."
  type        = string
}

variable "description" {
  description = "What this function does."
  type        = string
  default     = ""
}

variable "source_dir" {
  description = "Directory containing the function's Python source. Every .py file under it is zipped, and nothing else unless listed in extra_files."
  type        = string
}

variable "extra_files" {
  description = "Non-Python files, relative to source_dir, that also belong in the zip. Text files only: they are read with file()."
  type        = list(string)
  default     = []
}

variable "shared_source_dir" {
  description = "Optional directory of code shared by several functions. Its .py files are zipped under shared_package/ so the function can import them. Null means the function has no shared code."
  type        = string
  default     = null
}

variable "shared_package" {
  description = "Directory name the shared code lands under inside the zip, which is also the package name it is imported as."
  type        = string
  default     = "common"
}

variable "extra_policy_json" {
  description = "Optional IAM policy document JSON attached to the execution role, for whatever this particular function needs to reach."
  type        = string
  default     = null
}

variable "handler" {
  description = "module.function of the entry point, relative to the zip root."
  type        = string
  default     = "app.handler"
}

variable "runtime" {
  description = "Lambda runtime identifier."
  type        = string
  default     = "python3.14"
}

variable "memory_size" {
  description = "MB of memory. CPU scales with this. Lambda bills GB-seconds, so more memory costs more per millisecond but can finish sooner."
  type        = number
  default     = 128
}

variable "timeout" {
  description = "Seconds before Lambda kills the invocation."
  type        = number
  default     = 5
}

variable "reserved_concurrency" {
  description = "Hard cap on simultaneous executions. This is a cost and blast-radius guard: a runaway caller cannot spend the whole account's free allowance. -1 disables the cap."
  type        = number
  default     = 2
}

variable "environment" {
  description = "Environment variables for the function."
  type        = map(string)
  default     = {}
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention in days."
  type        = number
  default     = 3
}

variable "layer_arns" {
  description = "Layer version ARNs to attach. Layers are unpacked to /opt and their python/ directory joins the import path."
  type        = list(string)
  default     = []
}

variable "tracing_mode" {
  description = "X-Ray tracing. \"Active\" makes Lambda emit a trace segment for every sampled invocation and grants the function X-Ray write permission. \"PassThrough\" only continues a trace someone upstream already started."
  type        = string
  default     = "PassThrough"

  validation {
    condition     = contains(["Active", "PassThrough"], var.tracing_mode)
    error_message = "tracing_mode must be Active or PassThrough."
  }
}

variable "create_function_url" {
  description = "Whether to attach a function URL. Always AWS_IAM auth: a NONE auth URL is a public endpoint anyone can invoke."
  type        = bool
  default     = false
}
