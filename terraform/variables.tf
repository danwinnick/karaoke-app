variable "domain" {
  description = "Apex domain, e.g. example.com. The app is served at karaoke.<domain>."
  type        = string
}

variable "hosted_zone_id" {
  description = "Route53 hosted zone ID for var.domain."
  type        = string
}

variable "workos_authkit_domain" {
  description = "AuthKit domain for the WorkOS environment (WorkOS dashboard > Domains), e.g. my-app.authkit.app. No scheme."
  type        = string
}

variable "google_maps_api_key" {
  description = "Browser key for the Google Maps JavaScript API with the Places API (New) enabled. Restrict it to https://karaoke.<domain>/* referrers."
  type        = string
  sensitive   = true
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
