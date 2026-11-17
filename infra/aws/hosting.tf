locals {
  dashboard_callback_urls = var.dashboard_hosting_enabled ? ["https://${aws_cloudfront_distribution.dashboard[0].domain_name}/callback"] : []
  operator_callback_urls  = distinct(concat(var.callback_urls, local.dashboard_callback_urls))
}

resource "aws_s3_bucket" "dashboard" {
  count         = var.dashboard_hosting_enabled ? 1 : 0
  bucket        = "${local.prefix}-${var.aws_account_id}-${var.aws_region}-dashboard"
  force_destroy = false
}
resource "aws_s3_bucket_public_access_block" "dashboard" {
  count                   = var.dashboard_hosting_enabled ? 1 : 0
  bucket                  = aws_s3_bucket.dashboard[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_ownership_controls" "dashboard" {
  count  = var.dashboard_hosting_enabled ? 1 : 0
  bucket = aws_s3_bucket.dashboard[0].id
  rule { object_ownership = "BucketOwnerEnforced" }
}
resource "aws_s3_bucket_versioning" "dashboard" {
  count  = var.dashboard_hosting_enabled ? 1 : 0
  bucket = aws_s3_bucket.dashboard[0].id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "dashboard" {
  count  = var.dashboard_hosting_enabled ? 1 : 0
  bucket = aws_s3_bucket.dashboard[0].id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_lifecycle_configuration" "dashboard" {
  count  = var.dashboard_hosting_enabled ? 1 : 0
  bucket = aws_s3_bucket.dashboard[0].id
  rule {
    id     = "old-release-versions"
    status = "Enabled"
    filter { prefix = "" }
    noncurrent_version_expiration { noncurrent_days = 14 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
  depends_on = [aws_s3_bucket_versioning.dashboard]
}
resource "aws_cloudfront_origin_access_control" "dashboard" {
  count                             = var.dashboard_hosting_enabled ? 1 : 0
  name                              = "${local.prefix}-dashboard"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}
resource "aws_cloudfront_function" "dashboard" {
  count   = var.dashboard_hosting_enabled ? 1 : 0
  name    = "${local.prefix}-dashboard-routing"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = file("${path.module}/functions/dashboard-routing.js")
}
resource "aws_cloudfront_cache_policy" "dashboard" {
  for_each    = var.dashboard_hosting_enabled ? toset(["shell", "assets"]) : toset([])
  name        = "${local.prefix}-dashboard-${each.key}"
  min_ttl     = 0
  default_ttl = each.key == "assets" ? 86400 : 0
  max_ttl     = each.key == "assets" ? 31536000 : 0
  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_gzip   = each.key == "assets"
    enable_accept_encoding_brotli = each.key == "assets"
    cookies_config { cookie_behavior = "none" }
    headers_config { header_behavior = "none" }
    query_strings_config { query_string_behavior = "none" }
  }
}
resource "aws_cloudfront_response_headers_policy" "dashboard" {
  for_each = var.dashboard_hosting_enabled ? toset(["shell", "assets"]) : toset([])
  name     = "${local.prefix}-dashboard-${each.key}"
  security_headers_config {
    # The release HTML supplies exact API/login origins. Keeping this header
    # independent of API/Cognito avoids a distribution -> CORS -> API cycle.
    content_security_policy {
      content_security_policy = "frame-ancestors 'none'"
      override                = true
    }
    content_type_options { override = true }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "no-referrer"
      override        = true
    }
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = false
      preload                    = false
      override                   = true
    }
  }
  dynamic "custom_headers_config" {
    for_each = each.key == "shell" ? [1] : []
    content {
      items {
        header   = "Cache-Control"
        value    = "no-store"
        override = true
      }
    }
  }
}
resource "aws_cloudfront_distribution" "dashboard" {
  count               = var.dashboard_hosting_enabled ? 1 : 0
  enabled             = var.dashboard_delivery_enabled
  comment             = "QueueRescue static dashboard; API authentication remains required"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  is_ipv6_enabled     = true
  http_version        = "http2"
  retain_on_delete    = false
  origin {
    domain_name              = aws_s3_bucket.dashboard[0].bucket_regional_domain_name
    origin_id                = "dashboard"
    origin_access_control_id = aws_cloudfront_origin_access_control.dashboard[0].id
    s3_origin_config { origin_access_identity = "" }
  }
  default_cache_behavior {
    target_origin_id           = "dashboard"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    viewer_protocol_policy     = "redirect-to-https"
    cache_policy_id            = aws_cloudfront_cache_policy.dashboard["shell"].id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.dashboard["shell"].id
    compress                   = true
    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.dashboard[0].arn
    }
  }
  ordered_cache_behavior {
    path_pattern               = "/assets/*"
    target_origin_id           = "dashboard"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    viewer_protocol_policy     = "redirect-to-https"
    cache_policy_id            = aws_cloudfront_cache_policy.dashboard["assets"].id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.dashboard["assets"].id
    compress                   = true
  }
  restrictions {
    geo_restriction { restriction_type = "none" }
  }
  viewer_certificate { cloudfront_default_certificate = true }
}
resource "aws_s3_bucket_policy" "dashboard" {
  count  = var.dashboard_hosting_enabled ? 1 : 0
  bucket = aws_s3_bucket.dashboard[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "RequireTLS", Effect = "Deny", Principal = "*", Action = ["s3:*"], Resource = [aws_s3_bucket.dashboard[0].arn, "${aws_s3_bucket.dashboard[0].arn}/*"], Condition = { Bool = { "aws:SecureTransport" = "false" } } },
    { Sid = "DistributionReadOnly", Effect = "Allow", Principal = { Service = "cloudfront.amazonaws.com" }, Action = ["s3:GetObject"], Resource = ["${aws_s3_bucket.dashboard[0].arn}/index.html", "${aws_s3_bucket.dashboard[0].arn}/connect-origins.json", "${aws_s3_bucket.dashboard[0].arn}/assets/*"], Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.dashboard[0].arn } } }
  ] })
}
