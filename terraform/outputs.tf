output "url" {
  value = local.public_url
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.karaoke.id
}

output "workos_organization_id" {
  value = local.workos.organization_id
}

output "workos_client_id" {
  value = local.workos_client_id
}

output "workos_redirect_uris" {
  value = local.workos.redirect_uris
}
