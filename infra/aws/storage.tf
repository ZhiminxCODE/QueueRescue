locals {
  prefix       = "queuerescue-${var.environment}"
  arn_prefix   = "arn:aws"
  function_arn = { for name in ["api", "collector", "replay", "consumer", "verifier"] : name => "arn:aws:lambda:${var.aws_region}:${var.aws_account_id}:function:${local.prefix}-${name}" }
  workflow_arn = "arn:aws:states:${var.aws_region}:${var.aws_account_id}:stateMachine:${local.prefix}-recovery"
}

resource "aws_kms_key" "application" {
  description             = "QueueRescue sandbox evidence, queues, tables and logs"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}
resource "aws_kms_alias" "application" {
  name          = "alias/${local.prefix}"
  target_key_id = aws_kms_key.application.key_id
}

resource "aws_dynamodb_table" "data" {
  for_each                    = toset(["ledger", "business", "audit"])
  name                        = "${local.prefix}-${each.key}"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = var.protect_data
  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  point_in_time_recovery { enabled = true }
  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.application.arn
  }
  # Business and audit records must never disappear through evidence TTL.
  dynamic "ttl" {
    for_each = each.key == "ledger" ? [1] : []
    content {
      attribute_name = "expires_at"
      enabled        = true
    }
  }
}

resource "aws_s3_bucket" "evidence" {
  bucket        = "${local.prefix}-${var.aws_account_id}-${var.aws_region}-evidence"
  force_destroy = var.allow_destroy_evidence
}
resource "aws_s3_bucket_public_access_block" "evidence" {
  bucket                  = aws_s3_bucket.evidence.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_versioning" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.application.arn
    }
    bucket_key_enabled = true
  }
}
resource "aws_s3_bucket_lifecycle_configuration" "evidence" {
  bucket     = aws_s3_bucket.evidence.id
  depends_on = [aws_s3_bucket_versioning.evidence]
  rule {
    id     = "short-evidence-retention"
    status = "Enabled"
    filter { prefix = "evidence/" }
    expiration { days = 7 }
    noncurrent_version_expiration { noncurrent_days = 1 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}
resource "aws_s3_bucket_policy" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "RequireTLS", Effect = "Deny", Principal = "*", Action = "s3:*", Resource = [aws_s3_bucket.evidence.arn, "${aws_s3_bucket.evidence.arn}/*"], Condition = { Bool = { "aws:SecureTransport" = "false" } } },
    { Sid = "RequireKMS", Effect = "Deny", Principal = "*", Action = "s3:PutObject", Resource = "${aws_s3_bucket.evidence.arn}/*", Condition = { StringNotEquals = { "s3:x-amz-server-side-encryption" = "aws:kms" } } },
    { Sid = "RequireCustomerKey", Effect = "Deny", Principal = "*", Action = "s3:PutObject", Resource = "${aws_s3_bucket.evidence.arn}/*", Condition = { StringNotEquals = { "s3:x-amz-server-side-encryption-aws-kms-key-id" = aws_kms_key.application.arn } } }
  ] })
}

resource "aws_sqs_queue" "queue" {
  for_each                   = toset(["source", "source-dlq", "replay", "replay-dlq"])
  name                       = "${local.prefix}-${each.key}"
  fifo_queue                 = false
  visibility_timeout_seconds = 180
  message_retention_seconds  = endswith(each.key, "dlq") ? 1209600 : 345600
  kms_master_key_id          = aws_kms_key.application.arn
}
resource "aws_sqs_queue_redrive_policy" "source" {
  for_each  = toset(["source", "replay"])
  queue_url = aws_sqs_queue.queue[each.key].url
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.queue["${each.key}-dlq"].arn
    maxReceiveCount     = 3
  })
}
resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  for_each  = toset(["source", "replay"])
  queue_url = aws_sqs_queue.queue["${each.key}-dlq"].url
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue", sourceQueueArns = [aws_sqs_queue.queue[each.key].arn]
  })
}
resource "aws_sqs_queue_policy" "tls" {
  for_each  = aws_sqs_queue.queue
  queue_url = each.value.url
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Deny", Principal = "*", Action = "sqs:*", Resource = each.value.arn,
    Condition = { Bool = { "aws:SecureTransport" = "false" } }
  }] })
}
