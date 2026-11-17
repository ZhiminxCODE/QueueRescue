locals {
  audit_trail_arn   = "arn:aws:cloudtrail:${var.aws_region}:${var.aws_account_id}:trail/${local.prefix}-audit"
  audit_writer_name = "${local.prefix}-audit-archive"
  # Lambda uses ESM-UUID/shard paths, not the function name. Restrict the
  # destination to this dedicated bucket and AWS's fixed failure prefix.
  audit_failure_path = "aws/lambda/"
}

resource "aws_s3_bucket" "audit" {
  count               = var.audit_logging_enabled ? 1 : 0
  bucket              = "${local.prefix}-${var.aws_account_id}-${var.aws_region}-audit"
  object_lock_enabled = true
  force_destroy       = false
  lifecycle { prevent_destroy = true }
}
resource "aws_s3_bucket_public_access_block" "audit" {
  count                   = var.audit_logging_enabled ? 1 : 0
  bucket                  = aws_s3_bucket.audit[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_ownership_controls" "audit" {
  count  = var.audit_logging_enabled ? 1 : 0
  bucket = aws_s3_bucket.audit[0].id
  rule { object_ownership = "BucketOwnerEnforced" }
}
resource "aws_s3_bucket_versioning" "audit" {
  count  = var.audit_logging_enabled ? 1 : 0
  bucket = aws_s3_bucket.audit[0].id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "audit" {
  count  = var.audit_logging_enabled ? 1 : 0
  bucket = aws_s3_bucket.audit[0].id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_object_lock_configuration" "audit" {
  count               = var.audit_logging_enabled ? 1 : 0
  bucket              = aws_s3_bucket.audit[0].id
  object_lock_enabled = "Enabled"
  rule {
    default_retention {
      mode = "GOVERNANCE"
      days = var.audit_retention_days
    }
  }
  depends_on = [aws_s3_bucket_versioning.audit]
}
resource "aws_s3_bucket_lifecycle_configuration" "audit" {
  count  = var.audit_logging_enabled ? 1 : 0
  bucket = aws_s3_bucket.audit[0].id
  rule {
    id     = "expire-after-protection"
    status = "Enabled"
    filter { prefix = "" }
    expiration { days = var.audit_retention_days + 1 }
    noncurrent_version_expiration { noncurrent_days = var.audit_retention_days + 1 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
  rule {
    id     = "remove-expired-markers"
    status = "Enabled"
    filter { prefix = "" }
    expiration { expired_object_delete_marker = true }
  }
  depends_on = [aws_s3_bucket_versioning.audit]
}
resource "aws_s3_bucket_policy" "audit" {
  count  = var.audit_logging_enabled ? 1 : 0
  bucket = aws_s3_bucket.audit[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "RequireTLS", Effect = "Deny", Principal = "*", Action = ["s3:*"], Resource = [aws_s3_bucket.audit[0].arn, "${aws_s3_bucket.audit[0].arn}/*"], Condition = { Bool = { "aws:SecureTransport" = "false" } } },
    { Sid = "TrailAclCheck", Effect = "Allow", Principal = { Service = "cloudtrail.amazonaws.com" }, Action = ["s3:GetBucketAcl"], Resource = aws_s3_bucket.audit[0].arn, Condition = { StringEquals = { "aws:SourceArn" = local.audit_trail_arn } } },
    { Sid = "TrailDelivery", Effect = "Allow", Principal = { Service = "cloudtrail.amazonaws.com" }, Action = ["s3:PutObject"], Resource = "${aws_s3_bucket.audit[0].arn}/cloudtrail/AWSLogs/${var.aws_account_id}/*", Condition = { StringEquals = { "aws:SourceArn" = local.audit_trail_arn, "s3:x-amz-acl" = "bucket-owner-full-control" } } }
  ] })
}
resource "aws_cloudtrail" "audit" {
  count                         = var.audit_logging_enabled ? 1 : 0
  name                          = "${local.prefix}-audit"
  s3_bucket_name                = aws_s3_bucket.audit[0].id
  s3_key_prefix                 = "cloudtrail"
  enable_logging                = var.audit_collection_enabled
  enable_log_file_validation    = true
  is_multi_region_trail         = false
  include_global_service_events = true
  advanced_event_selector {
    name = "Regional and global-service management writes"
    field_selector {
      field  = "eventCategory"
      equals = ["Management"]
    }
    field_selector {
      field  = "readOnly"
      equals = ["false"]
    }
  }
  advanced_event_selector {
    name = "Configured audit table writes only"
    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }
    field_selector {
      field  = "resources.type"
      equals = ["AWS::DynamoDB::Table"]
    }
    field_selector {
      field  = "resources.ARN"
      equals = [aws_dynamodb_table.data["audit"].arn]
    }
    field_selector {
      field  = "readOnly"
      equals = ["false"]
    }
    field_selector {
      field  = "eventName"
      equals = ["PutItem", "UpdateItem", "DeleteItem", "BatchWriteItem", "TransactWriteItems", "ExecuteStatement", "BatchExecuteStatement", "ExecuteTransaction"]
    }
  }
  advanced_event_selector {
    name = "Snapshot and audit object access excluding trail delivery"
    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }
    field_selector {
      field  = "resources.type"
      equals = ["AWS::S3::Object"]
    }
    field_selector {
      field       = "resources.ARN"
      starts_with = ["${aws_s3_bucket.evidence.arn}/evidence/", "${aws_s3_bucket.audit[0].arn}/audit-records/", "${aws_s3_bucket.audit[0].arn}/${local.audit_failure_path}"]
    }
  }
  advanced_event_selector {
    name = "Trail object reads and retention changes without recursive writes"
    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }
    field_selector {
      field  = "resources.type"
      equals = ["AWS::S3::Object"]
    }
    field_selector {
      field       = "resources.ARN"
      starts_with = ["${aws_s3_bucket.audit[0].arn}/cloudtrail/"]
    }
    field_selector {
      field  = "eventName"
      equals = ["GetObject", "DeleteObject", "DeleteObjects", "PutObjectRetention", "PutObjectLegalHold", "RestoreObject", "GetObjectAttributes"]
    }
  }
  depends_on = [aws_s3_bucket_policy.audit, aws_s3_bucket_object_lock_configuration.audit, aws_s3_bucket_server_side_encryption_configuration.audit, aws_s3_bucket_ownership_controls.audit, aws_s3_bucket_public_access_block.audit]
}

resource "aws_iam_role" "audit_archive" {
  count                = var.audit_logging_enabled ? 1 : 0
  name                 = local.audit_writer_name
  permissions_boundary = var.execution_permissions_boundary_arn
  assume_role_policy   = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "audit_archive" {
  count = var.audit_logging_enabled ? 1 : 0
  role  = aws_iam_role.audit_archive[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["dynamodb:DescribeStream", "dynamodb:GetRecords", "dynamodb:GetShardIterator"], Resource = aws_dynamodb_table.data["audit"].stream_arn },
    { Effect = "Allow", Action = ["dynamodb:ListStreams"], Resource = "*", Condition = { StringEquals = { "aws:RequestedRegion" = var.aws_region } } },
    { Effect = "Allow", Action = ["s3:PutObject", "s3:GetObject"], Resource = "${aws_s3_bucket.audit[0].arn}/audit-records/*", Condition = { StringEquals = { "s3:ResourceAccount" = var.aws_account_id } } },
    { Effect = "Allow", Action = ["s3:PutObject"], Resource = "${aws_s3_bucket.audit[0].arn}/${local.audit_failure_path}*", Condition = { StringEquals = { "s3:ResourceAccount" = var.aws_account_id } } },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.audit[0].arn, Condition = { StringEquals = { "s3:ResourceAccount" = var.aws_account_id } } },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/${local.audit_writer_name}:*" }
    ], local.minimal ? [] : [
    { Effect = "Allow", Action = ["kms:Decrypt"], Resource = local.kms_key_arn, Condition = { StringEquals = { "kms:ViaService" = "dynamodb.${var.aws_region}.amazonaws.com", "kms:CallerAccount" = var.aws_account_id } } }
  ]) })
}
resource "aws_cloudwatch_log_group" "audit_archive" {
  count             = var.audit_logging_enabled ? 1 : 0
  name              = "/aws/lambda/${local.audit_writer_name}"
  retention_in_days = local.minimal ? 1 : 7
}
resource "aws_lambda_function" "audit_archive" {
  count                          = var.audit_logging_enabled ? 1 : 0
  function_name                  = local.audit_writer_name
  role                           = aws_iam_role.audit_archive[0].arn
  handler                        = "cloud.audit_archive.handler"
  runtime                        = "python3.13"
  architectures                  = ["arm64"]
  filename                       = var.lambda_package
  source_code_hash               = filebase64sha256(var.lambda_package)
  timeout                        = 60
  memory_size                    = 256
  reserved_concurrent_executions = var.audit_collection_enabled ? 1 : 0
  environment {
    variables = {
      QR_REGION               = var.aws_region
      QR_AUDIT_ARCHIVE_BUCKET = aws_s3_bucket.audit[0].id
      QR_AUDIT_STREAM_ARN     = aws_dynamodb_table.data["audit"].stream_arn
    }
  }
  depends_on = [aws_iam_role_policy.audit_archive, aws_cloudwatch_log_group.audit_archive, aws_s3_bucket_object_lock_configuration.audit, aws_s3_bucket_policy.audit, aws_s3_bucket_server_side_encryption_configuration.audit, aws_s3_bucket_ownership_controls.audit, aws_s3_bucket_public_access_block.audit]
}
resource "aws_lambda_event_source_mapping" "audit_archive" {
  count                              = var.audit_logging_enabled ? 1 : 0
  event_source_arn                   = aws_dynamodb_table.data["audit"].stream_arn
  function_name                      = aws_lambda_function.audit_archive[0].arn
  enabled                            = var.audit_collection_enabled
  starting_position                  = "TRIM_HORIZON"
  batch_size                         = 100
  maximum_batching_window_in_seconds = 1
  parallelization_factor             = 1
  maximum_retry_attempts             = 5
  maximum_record_age_in_seconds      = 3600
  bisect_batch_on_function_error     = true
  function_response_types            = ["ReportBatchItemFailures"]
  destination_config {
    on_failure { destination_arn = aws_s3_bucket.audit[0].arn }
  }
  depends_on = [aws_cloudwatch_log_metric_filter.audit_archive, aws_cloudwatch_metric_alarm.audit_archive]
}
resource "aws_cloudwatch_log_metric_filter" "audit_archive" {
  count          = var.audit_logging_enabled ? 1 : 0
  name           = "${local.prefix}-audit-archive-failures"
  log_group_name = aws_cloudwatch_log_group.audit_archive[0].name
  pattern        = "{ $.event = \"AUDIT_ARCHIVE_FAILED\" }"
  metric_transformation {
    name      = "PartialBatchFailures"
    namespace = "QueueRescue/${local.prefix}/Audit"
    value     = "1"
  }
}
resource "aws_cloudwatch_metric_alarm" "audit_archive" {
  for_each = var.audit_logging_enabled ? {
    errors      = { namespace = "AWS/Lambda", metric = "Errors", threshold = 0 }
    partial     = { namespace = "QueueRescue/${local.prefix}/Audit", metric = "PartialBatchFailures", threshold = 0 }
    age         = { namespace = "AWS/Lambda", metric = "IteratorAge", threshold = 300000 }
    destination = { namespace = "AWS/Lambda", metric = "DestinationDeliveryFailures", threshold = 0 }
  } : {}
  alarm_name          = "${local.prefix}-audit-${each.key}"
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = each.key == "age" ? "Maximum" : "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = each.key == "partial" ? {} : { FunctionName = local.audit_writer_name }
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
