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
  default     = "minimal"
  validation {
    condition     = contains(["standard", "minimal"], var.cost_profile)
    error_message = "Choose standard or minimal."
  }
}
variable "consumers_enabled" {
  description = "Enable source/replay consumers during an explicit demo; false stops polling and sets consumer reserved concurrency to zero."
  type        = bool
  default     = false
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
  default     = false
}
variable "callback_urls" {
  type    = list(string)
  default = ["http://localhost:5173/", "http://localhost:8000/", "http://localhost:8765/callback"]
  validation {
    condition = length(var.callback_urls) > 0 && alltrue([for url in var.callback_urls :
      can(regex("^(https://([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\\.)*[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(:[0-9]{1,5})?|http://localhost:[0-9]{1,5})(/[A-Za-z0-9._~%/-]*)?$", url)) &&
      try(tonumber(regex(":([0-9]+)(/|$)", url)[0]), 443) >= 1 &&
      try(tonumber(regex(":([0-9]+)(/|$)", url)[0]), 443) <= 65535
    ])
    error_message = "Use an exact HTTPS hostname or http://localhost with an explicit valid port; callbacks cannot contain wildcards, userinfo, queries or fragments."
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
  default     = 0.01
  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "Budget notification threshold must be positive."
  }
}
variable "audit_logging_enabled" {
  description = "Provision the optional protected audit archive and trail only after reviewing their extra cost, permissions and retention."
  type        = bool
  default     = false
  validation {
    condition     = !var.audit_logging_enabled || var.aws_region == "us-east-1"
    error_message = "The reviewed single-region audit profile requires us-east-1 to receive global IAM/STS events. Other regions require a separately reviewed trail design."
  }
}
variable "audit_collection_enabled" {
  description = "Capture switch: starts CloudTrail and audit stream polling; false preserves archives and reserves zero archive concurrency."
  type        = bool
  default     = false
  validation {
    condition     = !var.audit_collection_enabled || var.audit_logging_enabled
    error_message = "Provision the reviewed audit controls before enabling capture."
  }
}
variable "audit_retention_days" {
  description = "Default Governance retention for new archive versions; changing this does not shorten existing version retention."
  type        = number
  default     = 7
  validation {
    condition     = var.audit_retention_days >= 1 && var.audit_retention_days <= 90 && floor(var.audit_retention_days) == var.audit_retention_days
    error_message = "Audit retention must be an integer from 1 to 90 days."
  }
}
