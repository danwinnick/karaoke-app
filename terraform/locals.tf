data "aws_caller_identity" "karaoke" {}

locals {
  app_domain   = "karaoke.${var.domain}"
  public_url   = "https://${local.app_domain}"
  callback_url = "${local.public_url}/auth/callback"
  account_id   = data.aws_caller_identity.karaoke.account_id
}
