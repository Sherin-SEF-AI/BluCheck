output "api_base_url" {
  description = "Public base URL of the API (ALB DNS name)."
  value       = "http://${aws_lb.api.dns_name}"
}

output "dashboard_url" {
  description = "CloudFront URL of the admin dashboard."
  value       = "https://${aws_cloudfront_distribution.dashboard.domain_name}"
}

output "media_bucket" {
  description = "S3 bucket holding raw videos, frames, and thumbnails."
  value       = aws_s3_bucket.media.id
}

output "dashboard_bucket" {
  description = "S3 bucket holding the static dashboard build."
  value       = aws_s3_bucket.dashboard.id
}

output "extraction_queue_url" {
  description = "SQS queue that drives frame extraction."
  value       = aws_sqs_queue.extraction.id
}

output "extraction_dlq_url" {
  description = "Dead-letter queue for failed extractions."
  value       = aws_sqs_queue.extraction_dlq.id
}

output "api_ecr_repository" {
  description = "ECR repository URL for the API image."
  value       = aws_ecr_repository.api.repository_url
}

output "worker_ecr_repository" {
  description = "ECR repository URL for the worker image."
  value       = aws_ecr_repository.worker.repository_url
}

output "ecs_cluster" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.main.name
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution id (for cache invalidation)."
  value       = aws_cloudfront_distribution.dashboard.id
}

output "db_secret_arn" {
  description = "Secrets Manager ARN of the database URL."
  value       = aws_secretsmanager_secret.db_url.arn
}

output "jwt_secret_arn" {
  description = "Secrets Manager ARN of the JWT signing key."
  value       = aws_secretsmanager_secret.jwt.arn
}
