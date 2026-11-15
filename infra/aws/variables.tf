variable "aws_account_id" {
  description = "Explicit target development account; provider rejects any other account."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Supply a 12-digit development AWS account ID."
  }
}
variable "aws_profile" {
  description = "Local temporary-credential profile. Set null when using an AWS workload identity."
  type        = string
  default     = "queuerescue-dev"
}
variable "aws_region" {
  type    = string
  default = "us-east-1"
  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]+$", var.aws_region))
    error_message = "Use an AWS commercial-region identifier."
  }
}
variable "environment" {
  type    = string
  default = "dev"
  validation {
    condition     = contains(["dev", "test"], var.environment)
    error_message = "This stack is restricted to development and test sandboxes."
  }
}
variable "cost_profile" {
  description = "standard uses customer-managed keys/backups; minimal uses service-owned encryption and fixed small capacity. Neither guarantees Free Tier eligibility."
  type        = string
  default     = "standard"
  validation {
    condition     = contains(["standard", "minimal"], var.cost_profile)
    error_message = "Choose standard or minimal."
  }
}
variable "consumers_enabled" {
  description = "Enable source/replay consumers during an explicit demo; false stops polling and sets consumer reserved concurrency to zero."
  type        = bool
  default     = true
}
variable "lambda_package" {
  description = "Deterministic deployment zip produced by tools/package_aws.py."
  type        = string
  default     = ".build/lambda.zip"
}
variable "protect_data" {
  description = "Enable DynamoDB deletion protection. Disable only after reviewing export and teardown."
  type        = bool
  default     = true
}
variable "allow_destroy_evidence" {
  description = "Explicitly permit Terraform to empty evidence storage on destruction."
  type        = bool
  default     = false
}
variable "replay_enabled" {
  description = "Kill switch: false sets the replay Lambda reserved concurrency to zero."
  type        = bool
  default     = true
}
variable "callback_urls" {
  type    = list(string)
  default = ["http://localhost:5173/", "http://localhost:8765/callback"]
  validation {
    condition     = length(var.callback_urls) > 0 && alltrue([for url in var.callback_urls : startswith(url, "https://") || startswith(url, "http://localhost:")])
    error_message = "Cognito callbacks must use HTTPS or an explicit localhost development port."
  }
}
variable "budget_alert_email" {
  description = "Optional budget and alarm email. Empty disables email notifications and budget creation."
  type        = string
  default     = ""
}
variable "monthly_budget_usd" {
  description = "Notification threshold, not an instantaneous spending cap."
  type        = number
  default     = 10
  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "Budget notification threshold must be positive."
  }
}
