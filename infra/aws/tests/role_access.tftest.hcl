mock_provider "aws" {
  override_during = plan
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock-role" }
  }
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-4000-8000-000000000000", key_id = "00000000-0000-4000-8000-000000000000" }
  }
  mock_resource "aws_dynamodb_table" {
    defaults = { arn = "arn:aws:dynamodb:us-east-1:123456789012:table/mock-table", stream_arn = "arn:aws:dynamodb:us-east-1:123456789012:table/mock-table/stream/2026-01-01T00:00:00.000" }
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
  protect_data           = true
  allow_destroy_evidence = false
  budget_alert_email     = ""
}


run "boundary_unset_preserves_existing_configuration" {
  command = plan
  assert {
    condition     = alltrue([for role in aws_iam_role.function : role.permissions_boundary == null]) && aws_iam_role.workflow.permissions_boundary == null
    error_message = "Existing plans must not silently attach or create a boundary."
  }
}
run "minimal_idle_plan_for_offline_review" {
  command = plan
  variables {
    execution_permissions_boundary_arn = "arn:aws:iam::123456789012:policy/queuerescue-dev-execution-boundary"
  }
  assert {
    condition     = var.cost_profile == "minimal" && !var.consumers_enabled && !var.replay_enabled && !var.audit_logging_enabled && !var.dashboard_hosting_enabled && alltrue([for role in aws_iam_role.function : role.permissions_boundary == var.execution_permissions_boundary_arn])
    error_message = "The concrete offline-review plan must retain the minimal idle profile with the reviewed external boundary."
  }
}
run "approved_boundary_covers_every_service_role" {
  command = plan
  variables {
    execution_permissions_boundary_arn = "arn:aws:iam::123456789012:policy/queuerescue-dev-execution-boundary"
    audit_logging_enabled              = true
  }
  assert {
    condition     = alltrue([for role in aws_iam_role.function : role.permissions_boundary == var.execution_permissions_boundary_arn]) && aws_iam_role.workflow.permissions_boundary == var.execution_permissions_boundary_arn && aws_iam_role.audit_archive[0].permissions_boundary == var.execution_permissions_boundary_arn
    error_message = "Every provisioned runtime role must use the reviewed external boundary."
  }
}
run "reject_cross_account_boundary" {
  command = plan
  variables { execution_permissions_boundary_arn = "arn:aws:iam::999999999999:policy/queuerescue-dev-execution-boundary" }
  expect_failures = [var.execution_permissions_boundary_arn]
}
run "reject_other_environment_boundary" {
  command = plan
  variables { execution_permissions_boundary_arn = "arn:aws:iam::123456789012:policy/queuerescue-test-execution-boundary" }
  expect_failures = [var.execution_permissions_boundary_arn]
}
run "reject_unreviewed_policy_name" {
  command = plan
  variables { execution_permissions_boundary_arn = "arn:aws:iam::123456789012:policy/AdministratorAccess" }
  expect_failures = [var.execution_permissions_boundary_arn]
}
