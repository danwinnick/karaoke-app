resource "aws_route53_record" "karaoke_a" {
  zone_id = var.hosted_zone_id
  name    = local.app_domain
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.karaoke.domain_name
    zone_id                = aws_cloudfront_distribution.karaoke.hosted_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "karaoke_aaaa" {
  zone_id = var.hosted_zone_id
  name    = local.app_domain
  type    = "AAAA"

  alias {
    name                   = aws_cloudfront_distribution.karaoke.domain_name
    zone_id                = aws_cloudfront_distribution.karaoke.hosted_zone_id
    evaluate_target_health = false
  }
}
