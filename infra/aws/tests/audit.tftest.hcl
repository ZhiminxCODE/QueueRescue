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


run "audit_is_absent_by_default" {
  command = plan
  assert {
    condition     = length(aws_cloudtrail.audit) == 0 && length(aws_s3_bucket.audit) == 0 && length(aws_lambda_function.audit_archive) == 0 && length(aws_lambda_event_source_mapping.audit_archive) == 0 && length(aws_cloudwatch_metric_alarm.audit_archive) == 0 && length(aws_cloudwatch_log_metric_filter.audit_archive) == 0 && !aws_dynamodb_table.data["audit"].stream_enabled
    error_message = "Fresh defaults must not provision chargeable audit capture or stream polling."
  }
}

run "protected_audit_capture" {
  command = plan
  override_resource {
    target          = aws_s3_bucket.audit[0]
    override_during = plan
    values          = { arn = "arn:aws:s3:::mock-audit", id = "mock-audit" }
  }
  variables {
    audit_logging_enabled    = true
    audit_collection_enabled = true
  }
  assert {
    condition     = aws_s3_bucket.audit[0].object_lock_enabled && !aws_s3_bucket.audit[0].force_destroy && one(one(aws_s3_bucket_object_lock_configuration.audit[0].rule).default_retention).mode == "GOVERNANCE" && one(one(aws_s3_bucket_object_lock_configuration.audit[0].rule).default_retention).days == 7
    error_message = "Audit archives need protected governance versions with a bounded reviewed retention, never forced cleanup."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.audit[0].block_public_acls && aws_s3_bucket_public_access_block.audit[0].block_public_policy && aws_s3_bucket_public_access_block.audit[0].ignore_public_acls && aws_s3_bucket_public_access_block.audit[0].restrict_public_buckets && one(aws_s3_bucket_versioning.audit[0].versioning_configuration).status == "Enabled" && one(one(aws_s3_bucket_server_side_encryption_configuration.audit[0].rule).apply_server_side_encryption_by_default).sse_algorithm == "AES256"
    error_message = "Audit data must remain private, versioned and encrypted without a new fixed-cost key."
  }
  assert {
    condition     = aws_cloudtrail.audit[0].enable_logging && aws_cloudtrail.audit[0].enable_log_file_validation && aws_cloudtrail.audit[0].include_global_service_events && !aws_cloudtrail.audit[0].is_multi_region_trail && aws_cloudtrail.audit[0].s3_key_prefix == "cloudtrail"
    error_message = "Capture the reviewed regional/global-service boundary and validate delivered log digests."
  }
  assert {
    condition = length(aws_cloudtrail.audit[0].advanced_event_selector) == 4 && alltrue([
      for selector in aws_cloudtrail.audit[0].advanced_event_selector :
      alltrue([for field in selector.field_selector :
        toset(field.equals) == toset([aws_dynamodb_table.data["audit"].arn]) if field.field == "resources.ARN"
        ]) && alltrue([for field in selector.field_selector :
        !contains(field.equals, "GetRecords") && !contains(field.equals, "GetShardIterator") if field.field == "eventName"
      ]) if selector.name == "Configured audit table writes only"
      ]) && alltrue([
      for selector in aws_cloudtrail.audit[0].advanced_event_selector :
      alltrue([for field in selector.field_selector :
        !contains(field.equals, "PutObject") && !contains(field.equals, "CompleteMultipartUpload") if field.field == "eventName"
      ]) if selector.name == "Trail object reads and retention changes without recursive writes"
    ])
    error_message = "Audit data events must scope the exact table, exclude polling and avoid recursively logging trail delivery."
  }
  assert {
    condition = alltrue([for selector in aws_cloudtrail.audit[0].advanced_event_selector :
      alltrue([for field in selector.field_selector : toset(field.starts_with) == toset([
        "${aws_s3_bucket.evidence.arn}/evidence/", "${aws_s3_bucket.audit[0].arn}/audit-records/", "${aws_s3_bucket.audit[0].arn}/aws/lambda/"
      ]) if field.field == "resources.ARN"])
      if selector.name == "Snapshot and audit object access excluding trail delivery"
      ]) && alltrue([for s in jsondecode(aws_iam_role_policy.audit_archive[0].policy).Statement : alltrue([
        for action in s.Action : contains(["dynamodb:DescribeStream", "dynamodb:GetRecords", "dynamodb:GetShardIterator", "dynamodb:ListStreams", "s3:PutObject", "s3:GetObject", "s3:ListBucket", "logs:CreateLogStream", "logs:PutLogEvents"], action)
    ])])
    error_message = "Capture must not expand to unrelated buckets, stream polling, or recovery/business mutation permissions."
  }
  assert {
    condition = length([for s in jsondecode(aws_s3_bucket_policy.audit[0].policy).Statement : s if s.Effect == "Allow"]) == 2 && alltrue([for s in jsondecode(aws_s3_bucket_policy.audit[0].policy).Statement :
      s.Condition.StringEquals["aws:SourceArn"] == "arn:aws:cloudtrail:us-east-1:123456789012:trail/queuerescue-dev-audit" if s.Effect == "Allow"
      ]) && alltrue([for s in jsondecode(aws_iam_role_policy.audit_archive[0].policy).Statement :
      s.Resource == "${aws_s3_bucket.audit[0].arn}/aws/lambda/*" && s.Condition.StringEquals["s3:ResourceAccount"] == "123456789012"
      if s.Action == ["s3:PutObject"]
      ]) && anytrue([for s in jsondecode(aws_s3_bucket_policy.audit[0].policy).Statement :
      s.Resource == "${aws_s3_bucket.audit[0].arn}/cloudtrail/AWSLogs/123456789012/*" && s.Condition.StringEquals["s3:x-amz-acl"] == "bucket-owner-full-control"
      if s.Sid == "TrailDelivery"
      ]) && anytrue([for s in jsondecode(aws_s3_bucket_policy.audit[0].policy).Statement :
      s.Effect == "Deny" && s.Condition.Bool["aws:SecureTransport"] == "false" if s.Sid == "RequireTLS"
    ])
    error_message = "CloudTrail delivery must bind its source ARN, and failed payloads must use Lambda's ESM-UUID prefix in this account's bucket."
  }
  assert {
    condition     = aws_dynamodb_table.data["audit"].stream_enabled && aws_dynamodb_table.data["audit"].stream_view_type == "NEW_AND_OLD_IMAGES" && aws_lambda_function.audit_archive[0].reserved_concurrent_executions == 1 && aws_lambda_event_source_mapping.audit_archive[0].starting_position == "TRIM_HORIZON" && contains(aws_lambda_event_source_mapping.audit_archive[0].function_response_types, "ReportBatchItemFailures") && aws_lambda_event_source_mapping.audit_archive[0].maximum_retry_attempts == 5 && aws_lambda_event_source_mapping.audit_archive[0].maximum_record_age_in_seconds == 3600 && one(one(aws_lambda_event_source_mapping.audit_archive[0].destination_config).on_failure).destination_arn == aws_s3_bucket.audit[0].arn
    error_message = "Archive full stream images with bounded retries and a full-payload S3 failure destination."
  }
  assert {
    condition     = alltrue([for s in jsondecode(aws_iam_role_policy.audit_archive[0].policy).Statement : !contains(s.Action, "s3:DeleteObject") && !contains(s.Action, "s3:DeleteObjectVersion") && !contains(s.Action, "s3:BypassGovernanceRetention") && !contains(s.Action, "dynamodb:PutItem") && !contains(s.Action, "sqs:SendMessage")]) && length(aws_cloudwatch_metric_alarm.audit_archive) == 4
    error_message = "Archiving must not mutate recovery/business state or bypass retention; failures need observable alarms."
  }
  assert {
    condition     = alltrue([for key, p in aws_iam_role_policy.function : alltrue([for s in jsondecode(p.policy).Statement : !strcontains(jsonencode(s.Resource), aws_s3_bucket.audit[0].arn)])])
    error_message = "Ordinary API, collector, replay and business roles must have no archive access."
  }
}

run "audit_infrastructure_can_be_idle_without_destroying_retention" {
  command = plan
  variables { audit_logging_enabled = true }
  assert {
    condition     = length(aws_s3_bucket.audit) == 1 && !aws_cloudtrail.audit[0].enable_logging && !aws_lambda_event_source_mapping.audit_archive[0].enabled && aws_lambda_function.audit_archive[0].reserved_concurrent_executions == 0
    error_message = "Use the collection switch to stop new capture without deleting protected versions."
  }
}

run "standard_audit_key_access_is_decrypt_only" {
  command = plan
  variables {
    cost_profile          = "standard"
    audit_logging_enabled = true
  }
  assert {
    condition = contains(flatten([for s in jsondecode(aws_iam_role_policy.audit_archive[0].policy).Statement : s.Action]), "kms:Decrypt") && alltrue([for s in jsondecode(aws_iam_role_policy.audit_archive[0].policy).Statement :
      s.Action == ["kms:Decrypt"] && s.Resource == aws_kms_key.application[0].arn &&
      s.Condition.StringEquals["kms:ViaService"] == "dynamodb.us-east-1.amazonaws.com" &&
      s.Condition.StringEquals["kms:CallerAccount"] == "123456789012"
      if contains(s.Action, "kms:Decrypt")
    ])
    error_message = "Archive encryption access must stay bound to this account's DynamoDB key, without KMS writes."
  }
}

run "reject_capture_without_infrastructure" {
  command = plan
  variables { audit_collection_enabled = true }
  expect_failures = [var.audit_collection_enabled]
}

run "reject_fractional_retention" {
  command = plan
  variables { audit_retention_days = 1.5 }
  expect_failures = [var.audit_retention_days]
}

run "reject_unbounded_retention" {
  command = plan
  variables { audit_retention_days = 91 }
  expect_failures = [var.audit_retention_days]
}

run "reject_zero_retention" {
  command = plan
  variables { audit_retention_days = 0 }
  expect_failures = [var.audit_retention_days]
}

run "reject_audit_region_that_misses_global_events" {
  command = plan
  variables {
    audit_logging_enabled = true
    aws_region            = "us-east-2"
  }
  expect_failures = [var.audit_logging_enabled]
}

run "allow_reviewed_maximum_retention" {
  command = plan
  variables {
    audit_logging_enabled = true
    audit_retention_days  = 90
  }
  assert {
    condition     = one(one(aws_s3_bucket_object_lock_configuration.audit[0].rule).default_retention).days == 90
    error_message = "The selected retention must be applied exactly to newly delivered versions."
  }
}
