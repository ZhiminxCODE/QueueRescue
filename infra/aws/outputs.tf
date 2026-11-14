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
  }
}
