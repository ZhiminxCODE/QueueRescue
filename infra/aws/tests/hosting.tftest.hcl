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
  mock_resource "aws_cloudfront_distribution" {
    defaults = { domain_name = "d123example.cloudfront.net", arn = "arn:aws:cloudfront::123456789012:distribution/EHOSTED123" }
  }
  mock_resource "aws_cloudfront_function" {
    defaults = { arn = "arn:aws:cloudfront::123456789012:function/mock-routing" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::mock-evidence", bucket_regional_domain_name = "mock-dashboard.s3.us-east-1.amazonaws.com" }
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


run "hosting_absent_by_default" {
  command = plan
  assert {
    condition     = length(aws_s3_bucket.dashboard) == 0 && length(aws_cloudfront_distribution.dashboard) == 0 && length(aws_cloudfront_function.dashboard) == 0
    error_message = "Fresh defaults must not provision or publish dashboard hosting."
  }
}

run "private_hosting_prepared_but_not_serving" {
  command = plan
  variables { dashboard_hosting_enabled = true }
  assert {
    condition     = !aws_cloudfront_distribution.dashboard[0].enabled && !aws_s3_bucket.dashboard[0].force_destroy && one(aws_s3_bucket_versioning.dashboard[0].versioning_configuration).status == "Enabled" && aws_s3_bucket_public_access_block.dashboard[0].block_public_acls && aws_s3_bucket_public_access_block.dashboard[0].block_public_policy && aws_s3_bucket_public_access_block.dashboard[0].ignore_public_acls && aws_s3_bucket_public_access_block.dashboard[0].restrict_public_buckets && one(aws_s3_bucket_ownership_controls.dashboard[0].rule).object_ownership == "BucketOwnerEnforced"
    error_message = "Provisioning must leave delivery disabled and the versioned origin private."
  }
  assert {
    condition     = aws_cloudfront_origin_access_control.dashboard[0].signing_behavior == "always" && aws_cloudfront_origin_access_control.dashboard[0].signing_protocol == "sigv4" && aws_cloudfront_origin_access_control.dashboard[0].origin_access_control_origin_type == "s3"
    error_message = "Origin access must use signed HTTPS S3 requests, never a public website origin."
  }
  assert {
    condition     = length([for s in jsondecode(aws_s3_bucket_policy.dashboard[0].policy).Statement : s if s.Effect == "Allow"]) == 1 && alltrue([for s in jsondecode(aws_s3_bucket_policy.dashboard[0].policy).Statement : s.Action == ["s3:GetObject"] && s.Principal.Service == "cloudfront.amazonaws.com" && s.Condition.StringEquals["AWS:SourceArn"] == aws_cloudfront_distribution.dashboard[0].arn if s.Effect == "Allow"])
    error_message = "Only the exact distribution may read the allowed public release objects; no public or write grant."
  }
  assert {
    condition     = contains(aws_cognito_user_pool_client.dashboard.callback_urls, "https://d123example.cloudfront.net/callback") && contains(aws_cognito_user_pool_client.dashboard.logout_urls, "https://d123example.cloudfront.net/callback") && contains(aws_apigatewayv2_api.api.cors_configuration[0].allow_origins, "https://d123example.cloudfront.net") && contains(aws_cognito_user_pool_client.dashboard.callback_urls, "http://localhost:8000/")
    error_message = "Register the exact hosted callback/logout and CORS origin while preserving local callbacks."
  }
  assert {
    condition     = aws_cloudfront_cache_policy.dashboard["shell"].min_ttl == 0 && aws_cloudfront_cache_policy.dashboard["shell"].default_ttl == 0 && aws_cloudfront_cache_policy.dashboard["shell"].max_ttl == 0 && alltrue([for p in aws_cloudfront_cache_policy.dashboard : one(p.parameters_in_cache_key_and_forwarded_to_origin).query_strings_config[0].query_string_behavior == "none" && one(p.parameters_in_cache_key_and_forwarded_to_origin).cookies_config[0].cookie_behavior == "none" && one(p.parameters_in_cache_key_and_forwarded_to_origin).headers_config[0].header_behavior == "none"])
    error_message = "Do not cache HTML or forward/cache OAuth parameters, cookies or bearer headers at the static origin."
  }
  assert {
    condition     = one(aws_cloudfront_distribution.dashboard[0].default_cache_behavior).viewer_protocol_policy == "redirect-to-https" && toset(one(aws_cloudfront_distribution.dashboard[0].default_cache_behavior).allowed_methods) == toset(["GET", "HEAD"]) && length(aws_cloudfront_distribution.dashboard[0].custom_error_response) == 0 && length(aws_cloudfront_distribution.dashboard[0].logging_config) == 0 && aws_cloudfront_function.dashboard[0].code == file("${path.module}/functions/dashboard-routing.js")
    error_message = "Serve read-only HTTPS with the tested callback router, no blanket error fallback or OAuth-query access logs."
  }
  assert {
    condition     = alltrue([for p in aws_cloudfront_response_headers_policy.dashboard : one(p.security_headers_config).content_type_options[0].override && one(p.security_headers_config).frame_options[0].frame_option == "DENY" && one(p.security_headers_config).referrer_policy[0].referrer_policy == "no-referrer" && one(p.security_headers_config).content_security_policy[0].content_security_policy == "frame-ancestors 'none'"])
    error_message = "Enforce static security headers; the release HTML supplies the exact-origin resource CSP without an infrastructure cycle."
  }
}

run "hosting_delivery_requires_explicit_switch" {
  command = plan
  variables {
    dashboard_hosting_enabled  = true
    dashboard_delivery_enabled = true
  }
  assert {
    condition     = aws_cloudfront_distribution.dashboard[0].enabled
    error_message = "Only the reviewed delivery switch should enable viewer access."
  }
}

run "reject_delivery_without_hosting" {
  command = plan
  variables { dashboard_delivery_enabled = true }
  expect_failures = [var.dashboard_delivery_enabled]
}
