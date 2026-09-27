variable "domain" {
  description = "Apex domain name. The app is served at karaoke.<domain>."
  type        = string
  default     = "danwinnick.com"
}

variable "hosted_zone_id" {
  description = "Route53 hosted zone ID for var.domain."
  type        = string
  default     = "Z0011141118IUT41PGF3G"
}

variable "youtube_api_key" {
  description = "Server key for the YouTube Data API v3."
  type        = string
  sensitive   = true
}

variable "night_timezone" {
  description = "IANA timezone used to decide which 'night' a request belongs to. Nights roll over at 6am local time."
  type        = string
  default     = "America/Los_Angeles"
}

variable "aws_region" {
  description = "Primary AWS region."
  type        = string
  default     = "us-west-2"
}
