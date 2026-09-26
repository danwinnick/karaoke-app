resource "aws_s3_bucket" "karaoke_frontend" {
  bucket        = "karaoke-frontend-${local.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "karaoke_frontend" {
  bucket                  = aws_s3_bucket.karaoke_frontend.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "karaoke_frontend" {
  bucket = aws_s3_bucket.karaoke_frontend.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "karaoke_frontend" {
  bucket = aws_s3_bucket.karaoke_frontend.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "karaoke_frontend" {
  statement {
    sid       = "AllowCloudFrontRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.karaoke_frontend.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.karaoke.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "karaoke_frontend" {
  bucket = aws_s3_bucket.karaoke_frontend.id
  policy = data.aws_iam_policy_document.karaoke_frontend.json
}

locals {
  frontend_dir = "${path.module}/../frontend"
  content_types = {
    html = "text/html; charset=utf-8"
    css  = "text/css; charset=utf-8"
    js   = "text/javascript; charset=utf-8"
    svg  = "image/svg+xml"
    png  = "image/png"
    ico  = "image/x-icon"
    json = "application/json"
  }
}

resource "aws_s3_object" "karaoke_frontend" {
  for_each = fileset(local.frontend_dir, "**")

  bucket        = aws_s3_bucket.karaoke_frontend.id
  key           = each.value
  source        = "${local.frontend_dir}/${each.value}"
  etag          = filemd5("${local.frontend_dir}/${each.value}")
  content_type  = lookup(local.content_types, reverse(split(".", each.value))[0], "application/octet-stream")
  cache_control = endswith(each.value, ".html") ? "no-cache" : "public, max-age=300"
}
