resource "aws_acm_certificate" "karaoke" {
  provider          = aws.us_east_1
  domain_name       = local.app_domain
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "karaoke_cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.karaoke.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  allow_overwrite = true
  zone_id         = var.hosted_zone_id
  name            = each.value.name
  type            = each.value.type
  ttl             = 60
  records         = [each.value.record]
}

resource "aws_acm_certificate_validation" "karaoke" {
  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.karaoke.arn
  validation_record_fqdns = [for r in aws_route53_record.karaoke_cert_validation : r.fqdn]
}
