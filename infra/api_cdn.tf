# HTTPS front door for the API via CloudFront using the default *.cloudfront.net
# certificate. This avoids needing a custom domain + ACM certificate while still giving
# the dashboard (browser mixed-content) and the mobile app (iOS ATS / Android cleartext)
# a TLS endpoint. Requests are not cached and all viewer headers (Authorization, Origin)
# are forwarded to the ALB.

resource "aws_cloudfront_distribution" "api" {
  enabled     = true
  comment     = "${local.prefix} API HTTPS front door"
  price_class = "PriceClass_100"

  origin {
    domain_name = aws_lb.api.dns_name
    origin_id   = "api-alb"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only" # ALB listener is HTTP; CloudFront terminates TLS
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "api-alb"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]

    # Managed policies: CachingDisabled + AllViewer (forwards all headers/query/cookies).
    cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    origin_request_policy_id = "216adef6-5c7f-47e4-b989-5492eafa07d3"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = { Name = "${local.prefix}-api-cdn" }
}

output "api_https_url" {
  description = "HTTPS URL of the API (CloudFront in front of the ALB). Use this for the dashboard and mobile app."
  value       = "https://${aws_cloudfront_distribution.api.domain_name}"
}
