mock_provider "aws" {
  override_during = plan
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock-role" }
  }
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-4000-8000-000000000000", key_id = "00000000-0000-4000-8000-000000000000" }
  }
  mock_resource "aws_dynamodb_table" {
    defaults = { arn = "arn:aws:dynamodb:us-east-1:123456789012:table/mock-table" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::mock-evidence" }
  }
  mock_resource "aws_sqs_queue" {
    defaults = { arn = "arn:aws:sqs:us-east-1:123456789012:mock-queue", url = "https://sqs.us-east-1.amazonaws.com/123456789012/mock-queue" }
  }
  mock_resource "aws_lambda_function" {
    defaults = { arn = "arn:aws:lambda:us-east-1:123456789012:function:mock-function", invoke_arn = "arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/arn:aws:lambda:us-east-1:123456789012:function:mock-function/invocations" }
  }
  mock_resource "aws_sfn_state_machine" {
    defaults = { arn = "arn:aws:states:us-east-1:123456789012:stateMachine:mock-workflow" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:123456789012:log-group:/aws/lambda/mock" }
  }
  mock_resource "aws_sns_topic" {
    defaults = { arn = "arn:aws:sns:us-east-1:123456789012:mock-alerts" }
  }
  mock_resource "aws_cognito_user_pool" {
    defaults = { id = "us-east-1_example123", arn = "arn:aws:cognito-idp:us-east-1:123456789012:userpool/us-east-1_example123" }
  }
  mock_resource "aws_apigatewayv2_api" {
    defaults = { execution_arn = "arn:aws:execute-api:us-east-1:123456789012:example123", api_endpoint = "https://example123.execute-api.us-east-1.amazonaws.com" }
  }
}

variables {
  aws_account_id         = "123456789012"
  aws_profile            = null
  aws_region             = "us-east-1"
  cost_profile           = "standard"
  protect_data           = true
  allow_destroy_evidence = false
  consumers_enabled      = true
  replay_enabled         = true
  budget_alert_email     = ""
}

run "safe_sandbox_plan" {
  command = plan
  assert {
    condition     = alltrue([for queue in aws_sqs_queue.queue : !queue.fifo_queue && queue.kms_master_key_id == aws_kms_key.application[0].arn])
    error_message = "Every queue must be standard and KMS encrypted."
  }
  assert {
    condition     = aws_sqs_queue.queue["source-dlq"].message_retention_seconds > aws_sqs_queue.queue["source"].message_retention_seconds
    error_message = "Source DLQ retention must exceed source queue retention."
  }
  assert {
    condition     = toset(keys(aws_lambda_event_source_mapping.shipment)) == toset(["replay", "source"])
    error_message = "Never attach a consuming Lambda mapping to the inspection DLQ."
  }
  assert {
    condition     = alltrue([for mapping in aws_lambda_event_source_mapping.shipment : contains(mapping.function_response_types, "ReportBatchItemFailures")])
    error_message = "Shipment consumers must return partial batch failures."
  }
  assert {
    condition     = !contains(flatten([for statement in jsondecode(aws_iam_role_policy.function["collector"].policy).Statement : statement.Action]), "sqs:DeleteMessage")
    error_message = "The collector must not be able to delete source messages."
  }
  assert {
    condition     = aws_sfn_state_machine.recovery.type == "STANDARD" && !can(jsondecode(aws_sfn_state_machine.recovery.definition).States.ReplayChunk.Retry)
    error_message = "Use Standard orchestration without automatic uncertain-send task retries."
  }
  assert {
    condition     = aws_apigatewayv2_route.authenticated.authorization_type == "JWT" && contains(aws_apigatewayv2_route.authenticated.authorization_scopes, "queuerescue/access")
    error_message = "Every AWS API route must require scoped authentication."
  }
  assert {
    condition     = contains(aws_cognito_user_pool_client.dashboard.callback_urls, "http://localhost:8000/") && contains(aws_apigatewayv2_api.api.cors_configuration[0].allow_origins, "http://localhost:8000")
    error_message = "The built local dashboard origin must be registered for both Cognito and API CORS."
  }
  assert {
    condition     = alltrue([for table in aws_dynamodb_table.data : table.deletion_protection_enabled]) && !aws_s3_bucket.evidence.force_destroy
    error_message = "Evidence and ledger destruction must be opt-in."
  }
  assert {
    condition     = length(aws_dynamodb_table.data["audit"].ttl) == 0 && length(aws_dynamodb_table.data["business"].ttl) == 0
    error_message = "Business idempotency and audit records must not inherit snapshot TTL."
  }
  assert {
    condition     = aws_lambda_function.function["consumer"].reserved_concurrent_executions == 4 && aws_lambda_function.function["replay"].reserved_concurrent_executions == 1
    error_message = "Active consumers and replay must retain their bounded reserved concurrency."
  }
}

run "replay_kill_switch" {
  command = plan
  variables { replay_enabled = false }
  assert {
    condition     = aws_lambda_function.function["replay"].reserved_concurrent_executions == 0
    error_message = "Disabling replay must prevent new replay worker invocations."
  }
}

run "minimal_profile_keeps_encryption_without_fixed_cost_extras" {
  command = plan
  variables {
    cost_profile       = "minimal"
    consumers_enabled  = false
    replay_enabled     = false
    budget_alert_email = "budget@example.com"
    monthly_budget_usd = 0.01
  }
  assert {
    condition     = length(aws_kms_key.application) == 0 && length(aws_kms_alias.application) == 0
    error_message = "Minimal mode must not create a customer-managed KMS key."
  }
  assert {
    condition     = alltrue([for queue in aws_sqs_queue.queue : queue.sqs_managed_sse_enabled])
    error_message = "Minimal queues must retain service-owned encryption."
  }
  assert {
    condition     = sum([for table in aws_dynamodb_table.data : table.read_capacity]) == 15 && sum([for table in aws_dynamodb_table.data : table.write_capacity]) == 15
    error_message = "Minimal DynamoDB capacity must stay fixed at 15 total reads and writes."
  }
  assert {
    condition     = alltrue([for table in aws_dynamodb_table.data : table.billing_mode == "PROVISIONED" && !table.point_in_time_recovery[0].enabled && length(table.server_side_encryption) == 0])
    error_message = "Minimal tables use default AWS-owned encryption without paid backup extras."
  }
  assert {
    condition     = one(one(aws_s3_bucket_server_side_encryption_configuration.evidence.rule).apply_server_side_encryption_by_default).sse_algorithm == "AES256"
    error_message = "Minimal evidence must retain SSE-S3 encryption."
  }
  assert {
    condition     = length(aws_cloudwatch_metric_alarm.backlog) + length(aws_cloudwatch_metric_alarm.function_errors) + length(aws_cloudwatch_metric_alarm.workflow_failure) == 0
    error_message = "Minimal mode must not create metric alarms."
  }
  assert {
    condition     = alltrue([for mapping in aws_lambda_event_source_mapping.shipment : !mapping.enabled]) && aws_lambda_function.function["replay"].reserved_concurrent_executions == 0 && aws_lambda_function.function["consumer"].reserved_concurrent_executions == 0
    error_message = "The idle settings must stop polling, consumer/replay invocations, and positive concurrency reservations."
  }
  assert {
    condition     = alltrue([for mapping in aws_lambda_event_source_mapping.shipment : length(mapping.scaling_config) == 0])
    error_message = "Disabled mappings must not request a positive maximum concurrency against a zero-reserved consumer."
  }
  assert {
    condition     = anytrue([for notice in aws_budgets_budget.sandbox[0].notification : notice.threshold == 0 && notice.threshold_type == "ABSOLUTE_VALUE"])
    error_message = "A zero-spend notification must be present."
  }
}

run "reject_production_environment" {
  command = plan
  variables { environment = "production" }
  expect_failures = [var.environment]
}

run "reject_unencrypted_remote_callback" {
  command = plan
  variables { callback_urls = ["http://untrusted.example/"] }
  expect_failures = [var.callback_urls]
}
