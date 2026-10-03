# Song lookup: the API writes searches/<searchId>.json and starts the state machine, which runs
# one Lambda per source in order (KaraFun, Stingray, YouTube) until one has the song. Each
# Lambda updates the file, and the singer's page re-reads it through CloudFront until its
# status changes.

# The keys each source's search needs, created by hand in SSM Parameter Store before the first
# deploy and handed to that source's Lambda (and no other) as environment variables. KaraFun is
# searched through its published song list and needs none.

# Server key for the YouTube Data API v3.
data "aws_ssm_parameter" "karaoke_youtube_api_key" {
  name = "/karaoke/youtube_api_key"

  lifecycle {
    postcondition {
      condition     = trimspace(self.value) != ""
      error_message = "SSM parameter /karaoke/youtube_api_key is empty."
    }
  }
}

# Client ID and secret for Stingray's Karaoke API, issued by Stingray Support. Only read when
# var.stingray_api is on; without them Stingray is searched through a song list instead.
data "aws_ssm_parameter" "karaoke_stingray" {
  for_each = var.stingray_api ? toset(["client_id", "client_secret"]) : toset([])

  name = "/karaoke/stingray_${each.key}"

  lifecycle {
    postcondition {
      condition     = trimspace(self.value) != ""
      error_message = "SSM parameter /karaoke/stingray_${each.key} is empty."
    }
  }
}

locals {
  # The order the sources are searched in. Keep in step with SOURCES in backend/api/lib/songs.py.
  search_sources = ["karafun", "stingray", "youtube"]

  # What each search Lambda gets on top of SEARCHES_BUCKET.
  search_keys = {
    youtube = {
      YOUTUBE_API_KEY = data.aws_ssm_parameter.karaoke_youtube_api_key.value
    }
    stingray = {
      for name, param in data.aws_ssm_parameter.karaoke_stingray : "STINGRAY_${upper(name)}" => param.value
    }
  }
}

# ---- searches bucket ---------------------------------------------------------

# One JSON file per search. They are only useful for the few seconds a search takes, so they
# expire after a day. Anyone who knows a search's random id can read its file through
# CloudFront; it holds nothing but the query and the songs found.
resource "aws_s3_bucket" "karaoke_searches" {
  bucket        = "karaoke-searches-${local.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "karaoke_searches" {
  bucket                  = aws_s3_bucket.karaoke_searches.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "karaoke_searches" {
  bucket = aws_s3_bucket.karaoke_searches.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "karaoke_searches" {
  bucket = aws_s3_bucket.karaoke_searches.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "karaoke_searches" {
  bucket = aws_s3_bucket.karaoke_searches.id

  rule {
    id     = "karaoke-expire-searches"
    status = "Enabled"

    filter {
      prefix = "searches/"
    }

    expiration {
      days = 1
    }
  }
}

data "aws_iam_policy_document" "karaoke_searches" {
  statement {
    sid       = "AllowCloudFrontRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.karaoke_searches.arn}/searches/*"]

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

resource "aws_s3_bucket_policy" "karaoke_searches" {
  bucket = aws_s3_bucket.karaoke_searches.id
  policy = data.aws_iam_policy_document.karaoke_searches.json
}

# ---- search Lambdas ----------------------------------------------------------
# One per source plus `finish`, all built from the API's zip; each runs the handler of the
# same name in backend/api/search.py.

resource "aws_iam_role" "karaoke_search" {
  name               = "karaoke-search"
  assume_role_policy = data.aws_iam_policy_document.karaoke_lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "karaoke_search_logs" {
  role       = aws_iam_role.karaoke_search.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "karaoke_search" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.karaoke_searches.arn}/searches/*"]
  }
}

resource "aws_iam_role_policy" "karaoke_search" {
  name   = "karaoke-search"
  role   = aws_iam_role.karaoke_search.id
  policy = data.aws_iam_policy_document.karaoke_search.json
}

resource "aws_cloudwatch_log_group" "karaoke_search" {
  for_each = toset(concat(local.search_sources, ["finish"]))

  name              = "/aws/lambda/karaoke-search-${each.key}"
  retention_in_days = 30
}

resource "aws_lambda_function" "karaoke_search" {
  for_each = toset(concat(local.search_sources, ["finish"]))

  function_name    = "karaoke-search-${each.key}"
  role             = aws_iam_role.karaoke_search.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "search.${each.key}"
  filename         = data.archive_file.karaoke_api.output_path
  source_code_hash = data.archive_file.karaoke_api.output_base64sha256
  memory_size      = 512
  timeout          = 15

  environment {
    variables = merge(
      { SEARCHES_BUCKET = aws_s3_bucket.karaoke_searches.bucket },
      try(local.search_keys[each.key], {}),
    )
  }

  depends_on = [
    aws_cloudwatch_log_group.karaoke_search,
    aws_iam_role_policy_attachment.karaoke_search_logs,
    aws_iam_role_policy.karaoke_search,
  ]
}

# ---- state machine -----------------------------------------------------------

data "aws_iam_policy_document" "karaoke_song_search_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "karaoke_song_search" {
  name               = "karaoke-song-search"
  assume_role_policy = data.aws_iam_policy_document.karaoke_song_search_assume.json
}

data "aws_iam_policy_document" "karaoke_song_search" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [for fn in aws_lambda_function.karaoke_search : fn.arn]
  }

  # What Step Functions needs to deliver an Express state machine's logs to CloudWatch.
  statement {
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "karaoke_song_search" {
  name   = "karaoke-song-search"
  role   = aws_iam_role.karaoke_song_search.id
  policy = data.aws_iam_policy_document.karaoke_song_search.json
}

resource "aws_cloudwatch_log_group" "karaoke_song_search" {
  name              = "/aws/vendedlogs/states/karaoke-song-search"
  retention_in_days = 30
}

locals {
  # The state after each source's: the next source's search, or Finish after the last.
  search_next = {
    for i, source in local.search_sources :
    source => i + 1 < length(local.search_sources) ? "Search ${local.search_sources[i + 1]}" : "Finish"
  }

  search_retry = [{
    ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"]
    IntervalSeconds = 1
    MaxAttempts     = 2
    BackoffRate     = 2
  }]
}

# Express, because a search is short and singers start one every few keystrokes. Every path
# ends with the search's file out of 'searching': a source that has the song says so itself,
# and a source that has nothing, fails, or times out falls through to the next and finally to
# Finish, which settles the file as not found or failed.
resource "aws_sfn_state_machine" "karaoke_song_search" {
  name     = "karaoke-song-search"
  role_arn = aws_iam_role.karaoke_song_search.arn
  type     = "EXPRESS"

  definition = jsonencode({
    Comment        = "Looks a song up on each karaoke source in turn, stopping at the first that has it."
    StartAt        = "Search ${local.search_sources[0]}"
    TimeoutSeconds = 90
    States = merge(
      {
        for source in local.search_sources : "Search ${source}" => {
          Type           = "Task"
          Resource       = aws_lambda_function.karaoke_search[source].arn
          TimeoutSeconds = 20
          Retry          = local.search_retry
          # The Lambda answers {searchId, found}. A crash keeps the input and moves on.
          Catch = [{
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = local.search_next[source]
          }]
          Next = "Found on ${source}?"
        }
      },
      {
        for source in local.search_sources : "Found on ${source}?" => {
          Type = "Choice"
          Choices = [{
            Variable      = "$.found"
            BooleanEquals = true
            Next          = "Done"
          }]
          Default = local.search_next[source]
        }
      },
      {
        Finish = {
          Type           = "Task"
          Resource       = aws_lambda_function.karaoke_search["finish"].arn
          TimeoutSeconds = 20
          Retry = [{
            ErrorEquals     = ["States.ALL"]
            IntervalSeconds = 1
            MaxAttempts     = 3
            BackoffRate     = 2
          }]
          Next = "Done"
        }
        Done = {
          Type = "Succeed"
        }
      },
    )
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.karaoke_song_search.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  depends_on = [aws_iam_role_policy.karaoke_song_search]
}
