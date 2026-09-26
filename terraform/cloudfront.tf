data "aws_cloudfront_cache_policy" "karaoke_caching_optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "karaoke_caching_disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "karaoke_all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

data "aws_cloudfront_response_headers_policy" "karaoke_security_headers" {
  name = "Managed-SecurityHeadersPolicy"
}

resource "aws_cloudfront_origin_access_control" "karaoke_s3" {
  name                              = "karaoke-s3"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# CloudFront signs requests to the Lambda function URL (auth type AWS_IAM), so the
# API is only reachable through the distribution. The browser must send an
# x-amz-content-sha256 header on requests with a body (see frontend/js/api.js).
resource "aws_cloudfront_origin_access_control" "karaoke_lambda" {
  name                              = "karaoke-lambda"
  origin_access_control_origin_type = "lambda"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

locals {
  api_origin_domain = replace(replace(aws_lambda_function_url.karaoke_api.function_url, "https://", ""), "/", "")
}

resource "aws_cloudfront_distribution" "karaoke" {
  enabled             = true
  is_ipv6_enabled     = true
  http_version        = "http2and3"
  comment             = "karaoke"
  default_root_object = "index.html"
  aliases             = [local.app_domain]
  price_class         = "PriceClass_100"

  origin {
    origin_id                = "karaoke-s3"
    domain_name              = aws_s3_bucket.karaoke_frontend.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.karaoke_s3.id
  }

  origin {
    origin_id                = "karaoke-api"
    domain_name              = local.api_origin_domain
    origin_access_control_id = aws_cloudfront_origin_access_control.karaoke_lambda.id

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id           = "karaoke-s3"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.karaoke_caching_optimized.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.karaoke_security_headers.id
  }

  dynamic "ordered_cache_behavior" {
    for_each = ["/api/*", "/auth/*"]
    content {
      path_pattern             = ordered_cache_behavior.value
      target_origin_id         = "karaoke-api"
      viewer_protocol_policy   = "redirect-to-https"
      allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
      cached_methods           = ["GET", "HEAD"]
      compress                 = true
      cache_policy_id          = data.aws_cloudfront_cache_policy.karaoke_caching_disabled.id
      origin_request_policy_id = data.aws_cloudfront_origin_request_policy.karaoke_all_viewer_except_host.id
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.karaoke.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }
}
