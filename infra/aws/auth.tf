resource "aws_cognito_user_pool" "operators" {
  name                     = "${local.prefix}-operators"
  user_pool_tier           = "LITE"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  deletion_protection      = var.protect_data ? "ACTIVE" : "INACTIVE"
  mfa_configuration        = "ON"
  software_token_mfa_configuration { enabled = true }
  admin_create_user_config { allow_admin_create_user_only = true }
  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }
  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}
resource "aws_cognito_user_group" "role" {
  for_each     = toset(["Viewer", "Operator", "Approver", "Admin"])
  name         = each.key
  user_pool_id = aws_cognito_user_pool.operators.id
}
resource "aws_cognito_resource_server" "api" {
  identifier   = "queuerescue"
  name         = "QueueRescue API"
  user_pool_id = aws_cognito_user_pool.operators.id
  scope {
    scope_name        = "access"
    scope_description = "Access QueueRescue with server-enforced role checks"
  }
}
resource "aws_cognito_user_pool_client" "dashboard" {
  name                                 = "${local.prefix}-dashboard"
  user_pool_id                         = aws_cognito_user_pool.operators.id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "queuerescue/access"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = local.operator_callback_urls
  logout_urls                          = local.operator_callback_urls
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  access_token_validity                = 15
  id_token_validity                    = 15
  refresh_token_validity               = 1
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
  depends_on = [aws_cognito_resource_server.api]
}
resource "aws_cognito_user_pool_domain" "login" {
  domain       = "${local.prefix}-${var.aws_account_id}"
  user_pool_id = aws_cognito_user_pool.operators.id
}

resource "aws_apigatewayv2_api" "api" {
  name          = local.prefix
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = distinct([for url in local.operator_callback_urls : regex("^(https?://[^/]+)", url)[0]])
    allow_methods = ["GET", "POST"]
    allow_headers = ["authorization", "content-type", "idempotency-key"]
  }
}
resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.api.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "CognitoAccessTokens"
  jwt_configuration {
    audience = [aws_cognito_user_pool_client.dashboard.id]
    issuer   = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.operators.id}"
  }
}
resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.function["api"].invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000
}
resource "aws_apigatewayv2_route" "authenticated" {
  api_id               = aws_apigatewayv2_api.api.id
  route_key            = "ANY /api/{proxy+}"
  target               = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.cognito.id
  authorization_scopes = ["queuerescue/access"]
}
resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/${local.prefix}"
  retention_in_days = local.minimal ? 1 : 7
}
resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = 10
    throttling_rate_limit  = 5
  }
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format          = jsonencode({ requestId = "$context.requestId", routeKey = "$context.routeKey", status = "$context.status" })
  }
}
resource "aws_lambda_permission" "api" {
  statement_id  = "AuthenticatedHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.function["api"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*/api/*"
}
