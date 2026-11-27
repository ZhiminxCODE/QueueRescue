locals {
  prefix       = "queuerescue-${var.environment}"
  arn_prefix   = "arn:aws"
  function_arn = { for name in ["api", "collector", "replay", "consumer", "verifier"] : name => "arn:aws:lambda:${var.aws_region}:${var.aws_account_id}:function:${local.prefix}-${name}" }
  workflow_arn = "arn:aws:states:${var.aws_region}:${var.aws_account_id}:stateMachine:${local.prefix}-recovery"
  minimal      = var.cost_profile == "minimal"
  kms_key_arn  = local.minimal ? "" : aws_kms_key.application[0].arn
}

# Preserve existing standard-profile key addresses after introducing optional keys.
moved {
  from = aws_kms_key.application
  to   = aws_kms_key.application[0]
}
moved {
  from = aws_kms_alias.application
  to   = aws_kms_alias.application[0]
}

resource "aws_kms_key" "application" {
  count                   = local.minimal ? 0 : 1
  description             = "QueueRescue sandbox evidence, queues, tables and logs"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}
resource "aws_kms_alias" "application" {
  count         = local.minimal ? 0 : 1
  name          = "alias/${local.prefix}"
  target_key_id = aws_kms_key.application[0].key_id
}

resource "aws_dynamodb_table" "data" {
  for_each                    = toset(["ledger", "business", "audit"])
  name                        = "${local.prefix}-${each.key}"
  billing_mode                = local.minimal ? "PROVISIONED" : "PAY_PER_REQUEST"
  read_capacity               = local.minimal ? 5 : null
  write_capacity              = local.minimal ? 5 : null
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = var.protect_data
  stream_enabled              = each.key == "audit" && var.audit_logging_enabled
  stream_view_type            = each.key == "audit" && var.audit_logging_enabled ? "NEW_AND_OLD_IMAGES" : null
  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  point_in_time_recovery { enabled = !local.minimal }
  dynamic "server_side_encryption" {
    for_each = local.minimal ? [] : [1]
    content {
      enabled     = true
      kms_key_arn = local.kms_key_arn
    }
  }
  # Business and audit records must never disappear through evidence TTL.
  ttl {
    attribute_name = each.key == "ledger" ? "expires_at" : null
    enabled        = each.key == "ledger"
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
      sse_algorithm     = local.minimal ? "AES256" : "aws:kms"
      kms_master_key_id = local.minimal ? null : local.kms_key_arn
    }
    bucket_key_enabled = !local.minimal
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
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Sid = "RequireTLS", Effect = "Deny", Principal = "*", Action = "s3:*", Resource = [aws_s3_bucket.evidence.arn, "${aws_s3_bucket.evidence.arn}/*"], Condition = { Bool = { "aws:SecureTransport" = "false" } } },
    { Sid = "RequireEncryption", Effect = "Deny", Principal = "*", Action = "s3:PutObject", Resource = "${aws_s3_bucket.evidence.arn}/*", Condition = { StringNotEquals = { "s3:x-amz-server-side-encryption" = local.minimal ? "AES256" : "aws:kms" } } }
    ], local.minimal ? [] : [
    { Sid = "RequireCustomerKey", Effect = "Deny", Principal = "*", Action = "s3:PutObject", Resource = "${aws_s3_bucket.evidence.arn}/*", Condition = { StringNotEquals = { "s3:x-amz-server-side-encryption-aws-kms-key-id" = local.kms_key_arn } } }
  ]) })
}

resource "aws_sqs_queue" "queue" {
  for_each                   = toset(["source", "source-dlq", "replay", "replay-dlq"])
  name                       = "${local.prefix}-${each.key}"
  fifo_queue                 = false
  visibility_timeout_seconds = 180
  message_retention_seconds  = endswith(each.key, "dlq") ? 1209600 : 345600
  kms_master_key_id          = local.minimal ? null : local.kms_key_arn
  sqs_managed_sse_enabled    = local.minimal ? true : null
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
