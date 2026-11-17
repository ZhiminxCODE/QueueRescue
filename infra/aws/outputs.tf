output "deployment" {
  description = "Non-secret sandbox endpoints and resource names for the deployment runbook."
  value = {
    account_id        = var.aws_account_id
    region            = var.aws_region
    api_url           = aws_apigatewayv2_api.api.api_endpoint
    cognito_user_pool = aws_cognito_user_pool.operators.id
    cognito_client_id = aws_cognito_user_pool_client.dashboard.id
    cognito_login_url = "https://${aws_cognito_user_pool_domain.login.domain}.auth.${var.aws_region}.amazoncognito.com"
    source_queue_url  = aws_sqs_queue.queue["source"].url
    dlq_url           = aws_sqs_queue.queue["source-dlq"].url
    replay_queue_url  = aws_sqs_queue.queue["replay"].url
    business_table    = aws_dynamodb_table.data["business"].name
    ledger_table      = aws_dynamodb_table.data["ledger"].name
    audit_table       = aws_dynamodb_table.data["audit"].name
    evidence_bucket   = aws_s3_bucket.evidence.id
    collector_lambda  = aws_lambda_function.function["collector"].function_name
    replay_lambda     = aws_lambda_function.function["replay"].function_name
    state_machine_arn = aws_sfn_state_machine.recovery.arn
    dashboard_hosting = {
      provisioned      = var.dashboard_hosting_enabled
      delivery_enabled = var.dashboard_delivery_enabled
      bucket           = try(aws_s3_bucket.dashboard[0].id, null)
      distribution_id  = try(aws_cloudfront_distribution.dashboard[0].id, null)
      domain_name      = try(aws_cloudfront_distribution.dashboard[0].domain_name, null)
      callback_url     = try(local.dashboard_callback_urls[0], null)
    }
    audit_controls = {
      provisioned       = var.audit_logging_enabled
      capture_enabled   = var.audit_collection_enabled
      retention_days    = var.audit_retention_days
      archive_bucket    = try(aws_s3_bucket.audit[0].id, null)
      trail_arn         = try(aws_cloudtrail.audit[0].arn, null)
      archive_lambda    = try(aws_lambda_function.audit_archive[0].function_name, null)
      stream_mapping_id = try(aws_lambda_event_source_mapping.audit_archive[0].uuid, null)
    }
  }
}
