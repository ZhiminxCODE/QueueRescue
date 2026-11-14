locals {
  handlers = { api = "cloud.api.handler", collector = "cloud.collector.handler", replay = "cloud.replay.handler", consumer = "cloud.shipment.consumer", verifier = "cloud.shipment.verifier" }
  environment = {
    QR_REGION            = var.aws_region
    QR_ACCOUNT_ID        = var.aws_account_id
    QR_LEDGER_TABLE      = aws_dynamodb_table.data["ledger"].name
    QR_BUSINESS_TABLE    = aws_dynamodb_table.data["business"].name
    QR_AUDIT_TABLE       = aws_dynamodb_table.data["audit"].name
    QR_EVIDENCE_BUCKET   = aws_s3_bucket.evidence.id
    QR_KMS_KEY_ARN       = local.kms_key_arn
    QR_SOURCE_QUEUE_URL  = aws_sqs_queue.queue["source"].url
    QR_DLQ_URL           = aws_sqs_queue.queue["source-dlq"].url
    QR_REPLAY_QUEUE_URL  = aws_sqs_queue.queue["replay"].url
    QR_REPLAY_QUEUE_ARN  = aws_sqs_queue.queue["replay"].arn
    QR_VERIFIER_ARN      = local.function_arn["verifier"]
    QR_COLLECTOR_ARN     = local.function_arn["collector"]
    QR_STATE_MACHINE_ARN = local.workflow_arn
    QR_CONSUMER_VERSION  = "shipment-${substr(filebase64sha256(var.lambda_package), 0, 16)}"
  }
  ledger_read   = { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:Query"], Resource = aws_dynamodb_table.data["ledger"].arn }
  ledger_write  = { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:ConditionCheckItem"], Resource = aws_dynamodb_table.data["ledger"].arn }
  audit_append  = { Effect = "Allow", Action = ["dynamodb:PutItem"], Resource = aws_dynamodb_table.data["audit"].arn }
  evidence_read = { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.evidence.arn}/evidence/*" }
  policies = {
    api = [local.ledger_write, local.audit_append, local.evidence_read,
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:Query"], Resource = aws_dynamodb_table.data["audit"].arn },
      { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = [local.function_arn["verifier"], local.function_arn["collector"]] },
      { Effect = "Allow", Action = ["states:StartExecution"], Resource = local.workflow_arn }
    ]
    collector = [local.ledger_write, local.audit_append, local.evidence_read,
      { Effect = "Allow", Action = ["s3:PutObject"], Resource = "${aws_s3_bucket.evidence.arn}/evidence/*" },
      { Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"], Resource = aws_sqs_queue.queue["source-dlq"].arn }
    ]
    replay = [local.ledger_write, local.audit_append, local.evidence_read,
      { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = aws_sqs_queue.queue["replay"].arn },
      { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = local.function_arn["verifier"] }
    ]
    consumer = [
      { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:UpdateItem"], Resource = aws_dynamodb_table.data["business"].arn },
      { Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"], Resource = [aws_sqs_queue.queue["source"].arn, aws_sqs_queue.queue["replay"].arn] }
    ]
    verifier = [{ Effect = "Allow", Action = ["dynamodb:GetItem"], Resource = aws_dynamodb_table.data["business"].arn }]
  }
}

resource "aws_iam_role" "function" {
  for_each = local.handlers
  name     = "${local.prefix}-${each.key}"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_role_policy" "function" {
  for_each = local.handlers
  role     = aws_iam_role.function[each.key].id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat(local.policies[each.key], [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/${local.prefix}-${each.key}:*" }
    ], local.minimal ? [] : [
    { Effect = "Allow", Action = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"], Resource = local.kms_key_arn }
  ]) })
}
resource "aws_cloudwatch_log_group" "function" {
  for_each          = local.handlers
  name              = "/aws/lambda/${local.prefix}-${each.key}"
  retention_in_days = local.minimal ? 1 : 7
}
resource "aws_lambda_function" "function" {
  for_each                       = local.handlers
  function_name                  = "${local.prefix}-${each.key}"
  role                           = aws_iam_role.function[each.key].arn
  handler                        = each.value
  runtime                        = "python3.13"
  architectures                  = ["arm64"]
  filename                       = var.lambda_package
  source_code_hash               = filebase64sha256(var.lambda_package)
  timeout                        = each.key == "api" ? 120 : each.key == "collector" ? 60 : each.key == "replay" ? 60 : 30
  memory_size                    = 256
  reserved_concurrent_executions = each.key == "replay" ? (var.replay_enabled ? 1 : 0) : each.key == "consumer" ? 4 : -1
  environment { variables = local.environment }
  depends_on = [aws_iam_role_policy.function, aws_cloudwatch_log_group.function]
}
resource "aws_lambda_event_source_mapping" "shipment" {
  for_each                = toset(["source", "replay"])
  event_source_arn        = aws_sqs_queue.queue[each.key].arn
  function_name           = aws_lambda_function.function["consumer"].arn
  enabled                 = var.consumers_enabled
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]
  scaling_config { maximum_concurrency = 2 }
}

resource "aws_iam_role" "workflow" {
  name = "${local.prefix}-workflow"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "states.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_role_policy" "workflow" {
  role = aws_iam_role.workflow.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = local.function_arn["replay"]
  }] })
}
resource "aws_sfn_state_machine" "recovery" {
  name     = "${local.prefix}-recovery"
  role_arn = aws_iam_role.workflow.arn
  type     = "STANDARD"
  definition = jsonencode({
    StartAt = "ReplayChunk", TimeoutSeconds = 1800,
    States = {
      ReplayChunk = {
        Type = "Task", Resource = aws_lambda_function.function["replay"].arn,
        Next = "Continue", Catch = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.workerError", Next = "Reconcile" }]
      }
      Continue = { Type = "Choice", Choices = [{ Variable = "$.continue", BooleanEquals = true, Next = "Throttle" }], Default = "Done" }
      Throttle = { Type = "Wait", Seconds = 1, Next = "ReplayChunk" }
      Reconcile = {
        Type       = "Task", Resource = aws_lambda_function.function["replay"].arn,
        Parameters = { "jobId.$" = "$.jobId", "generation.$" = "$.generation", action = "recover" },
        Retry      = [{ ErrorEquals = ["Lambda.ServiceException", "Lambda.TooManyRequestsException"], IntervalSeconds = 2, MaxAttempts = 3, BackoffRate = 2 }],
        End        = true
      }
      Done = { Type = "Succeed" }
    }
  })
  depends_on = [aws_iam_role_policy.workflow]
}
