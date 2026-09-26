# The WorkOS API key is created by hand in Secrets Manager before the first deploy.
# The secret value may be the raw key (sk_...) or JSON: {"api_key": "sk_..."}.
data "aws_secretsmanager_secret" "karaoke_workos_api_key" {
  name = "karaoke/workos_api_key"
}

# The WorkOS client ID is created by hand in SSM Parameter Store before the first deploy.
# It is the client_id the API uses for the AuthKit OAuth flow.
data "aws_ssm_parameter" "karaoke_workos_client_id" {
  name = "/karaoke/workos_client_id"
}

data "archive_file" "karaoke_workos_bootstrap" {
  type        = "zip"
  source_dir  = "${path.module}/../backend/workos-bootstrap"
  output_path = "${path.module}/build/karaoke-workos-bootstrap.zip"
}

resource "aws_iam_role" "karaoke_workos_bootstrap" {
  name               = "karaoke-workos-bootstrap"
  assume_role_policy = data.aws_iam_policy_document.karaoke_lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "karaoke_workos_bootstrap_logs" {
  role       = aws_iam_role.karaoke_workos_bootstrap.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "karaoke_workos_bootstrap" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [data.aws_secretsmanager_secret.karaoke_workos_api_key.arn]
  }
}

resource "aws_iam_role_policy" "karaoke_workos_bootstrap" {
  name   = "karaoke-workos-bootstrap"
  role   = aws_iam_role.karaoke_workos_bootstrap.id
  policy = data.aws_iam_policy_document.karaoke_workos_bootstrap.json
}

resource "aws_cloudwatch_log_group" "karaoke_workos_bootstrap" {
  name              = "/aws/lambda/karaoke-workos-bootstrap"
  retention_in_days = 30
}

resource "aws_lambda_function" "karaoke_workos_bootstrap" {
  function_name    = "karaoke-workos-bootstrap"
  role             = aws_iam_role.karaoke_workos_bootstrap.arn
  runtime          = "nodejs22.x"
  architectures    = ["arm64"]
  handler          = "index.handler"
  filename         = data.archive_file.karaoke_workos_bootstrap.output_path
  source_code_hash = data.archive_file.karaoke_workos_bootstrap.output_base64sha256
  timeout          = 30

  environment {
    variables = {
      WORKOS_API_KEY_SECRET = data.aws_secretsmanager_secret.karaoke_workos_api_key.arn
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.karaoke_workos_bootstrap,
    aws_iam_role_policy_attachment.karaoke_workos_bootstrap_logs,
    aws_iam_role_policy.karaoke_workos_bootstrap,
  ]
}

# Idempotently creates (or finds) the WorkOS organization and the first-party OAuth
# application for this deployment, and registers the callback URL on the application.
resource "aws_lambda_invocation" "karaoke_workos_bootstrap" {
  function_name = aws_lambda_function.karaoke_workos_bootstrap.function_name

  input = jsonencode({
    organizationName = "karaoke-${var.domain}"
    applicationName  = "karaoke-${var.domain}"
    redirectUri      = local.callback_url
  })

  triggers = {
    code = data.archive_file.karaoke_workos_bootstrap.output_base64sha256
  }
}

locals {
  workos = jsondecode(aws_lambda_invocation.karaoke_workos_bootstrap.result)

  # A client ID is not a secret, but the data source always marks the value sensitive.
  workos_client_id = nonsensitive(data.aws_ssm_parameter.karaoke_workos_client_id.value)
}
