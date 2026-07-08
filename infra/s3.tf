# Media bucket (raw/frames/thumbs) and dashboard static site bucket.
# Media bucket is fully private, SSE-KMS, with lifecycle expiry. Dashboard bucket is
# private too and served only through CloudFront via Origin Access Control.

# ----- Media bucket -----
resource "aws_s3_bucket" "media" {
  bucket = local.media_bucket_name
  tags   = { Name = "${local.prefix}-media" }
}

resource "aws_s3_bucket_public_access_block" "media" {
  bucket                  = aws_s3_bucket.media.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  bucket = aws_s3_bucket.media.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.main.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "media" {
  bucket = aws_s3_bucket.media.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_cors_configuration" "media" {
  bucket = aws_s3_bucket.media.id

  # Allows the mobile client to PUT multipart parts directly to presigned URLs.
  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["PUT", "GET", "HEAD"]
    allowed_origins = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "media" {
  bucket = aws_s3_bucket.media.id

  rule {
    id     = "expire-raw-videos"
    status = "Enabled"
    filter { prefix = "raw/" }
    expiration { days = var.raw_retention_days }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }

  rule {
    id     = "expire-frames"
    status = "Enabled"
    filter { prefix = "frames/" }
    expiration { days = var.frame_retention_days }
  }

  rule {
    id     = "expire-thumbs"
    status = "Enabled"
    filter { prefix = "thumbs/" }
    expiration { days = var.frame_retention_days }
  }
}

# S3 -> SQS notification on the raw/ prefix triggers extraction.
resource "aws_s3_bucket_notification" "media" {
  bucket = aws_s3_bucket.media.id

  queue {
    queue_arn     = aws_sqs_queue.extraction.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "raw/"
  }

  depends_on = [aws_sqs_queue_policy.extraction]
}

# ----- Dashboard static site bucket -----
resource "aws_s3_bucket" "dashboard" {
  bucket = local.dashboard_bucket_name
  tags   = { Name = "${local.prefix}-dashboard" }
}

resource "aws_s3_bucket_public_access_block" "dashboard" {
  bucket                  = aws_s3_bucket.dashboard.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
