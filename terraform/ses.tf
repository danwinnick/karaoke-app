# DJ login codes are sent from no-reply@karaoke.<domain>. The domain is verified with
# Easy DKIM. New AWS accounts start in the SES sandbox, which only delivers to verified
# addresses; request production access once (see README) so any DJ can get a code.
resource "aws_sesv2_email_identity" "karaoke" {
  email_identity = local.app_domain
}

resource "aws_route53_record" "karaoke_ses_dkim" {
  count   = 3
  zone_id = var.hosted_zone_id
  name    = "${aws_sesv2_email_identity.karaoke.dkim_signing_attributes[0].tokens[count.index]}._domainkey.${local.app_domain}"
  type    = "CNAME"
  ttl     = 600
  records = ["${aws_sesv2_email_identity.karaoke.dkim_signing_attributes[0].tokens[count.index]}.dkim.amazonses.com"]
}
