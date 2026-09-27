resource "random_password" "karaoke_session_secret" {
  length  = 64
  special = false
}

data "archive_file" "karaoke_api" {
  type        = "zip"
  source_dir  = "${path.module}/../backend/api"
  output_path = "${path.module}/build/karaoke-api.zip"
  excludes    = ["**/__pycache__/**"]
}

data "aws_iam_policy_document" "karaoke_lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "karaoke_api" {
  name               = "karaoke-api"
  assume_role_policy = data.aws_iam_policy_document.karaoke_lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "karaoke_api_logs" {
  role       = aws_iam_role.karaoke_api.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "karaoke_api" {
  statement {
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:BatchGetItem",
      "dynamodb:DeleteItem",
    ]
    resources = [
      aws_dynamodb_table.karaoke_dj.arn,
      aws_dynamodb_table.karaoke_singers.arn,
      aws_dynamodb_table.karaoke_requested_songs.arn,
      "${aws_dynamodb_table.karaoke_requested_songs.arn}/index/*",
      aws_dynamodb_table.karaoke_auth.arn,
    ]
  }

  statement {
    actions   = ["ses:SendEmail"]
    resources = [aws_sesv2_email_identity.karaoke.arn]
  }

  statement {
    actions   = ["ssm:GetParameter"]
    resources = [data.aws_ssm_parameter.karaoke_workos_api_key.arn]
  }
}

resource "aws_iam_role_policy" "karaoke_api" {
  name   = "karaoke-api"
  role   = aws_iam_role.karaoke_api.id
  policy = data.aws_iam_policy_document.karaoke_api.json
}

resource "aws_cloudwatch_log_group" "karaoke_api" {
  name              = "/aws/lambda/karaoke-api"
  retention_in_days = 30
}

resource "aws_lambda_function" "karaoke_api" {
  function_name    = "karaoke-api"
  role             = aws_iam_role.karaoke_api.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "index.handler"
  filename         = data.archive_file.karaoke_api.output_path
  source_code_hash = data.archive_file.karaoke_api.output_base64sha256
  memory_size      = 512
  timeout          = 15

  environment {
    variables = {
      PUBLIC_URL           = local.public_url
      DJ_TABLE             = aws_dynamodb_table.karaoke_dj.name
      SINGERS_TABLE        = aws_dynamodb_table.karaoke_singers.name
      SONGS_TABLE          = aws_dynamodb_table.karaoke_requested_songs.name
      SONGS_DJ_INDEX       = "djId-date-index"
      AUTH_TABLE           = aws_dynamodb_table.karaoke_auth.name
      SES_FROM_ADDRESS     = "Karaoke <no-reply@${local.app_domain}>"
      WORKOS_API_KEY_PARAM = data.aws_ssm_parameter.karaoke_workos_api_key.name
      WORKOS_CLIENT_ID     = local.workos_client_id
      WORKOS_ORG_ID        = local.workos.organization_id
      SESSION_SECRET       = random_password.karaoke_session_secret.result
      GOOGLE_MAPS_API_KEY  = var.google_maps_api_key
      YOUTUBE_API_KEY      = var.youtube_api_key
      NIGHT_TIMEZONE       = var.night_timezone
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.karaoke_api,
    aws_iam_role_policy_attachment.karaoke_api_logs,
  ]
}

resource "aws_lambda_function_url" "karaoke_api" {
  function_name      = aws_lambda_function.karaoke_api.function_name
  authorization_type = "AWS_IAM"
}

resource "aws_lambda_permission" "karaoke_api_cloudfront_url" {
  statement_id  = "karaoke-cloudfront-invoke-url"
  action        = "lambda:InvokeFunctionUrl"
  function_name = aws_lambda_function.karaoke_api.function_name
  principal     = "cloudfront.amazonaws.com"
  source_arn    = aws_cloudfront_distribution.karaoke.arn
}

resource "aws_lambda_permission" "karaoke_api_cloudfront_invoke" {
  statement_id  = "karaoke-cloudfront-invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.karaoke_api.function_name
  principal     = "cloudfront.amazonaws.com"
  source_arn    = aws_cloudfront_distribution.karaoke.arn
}
