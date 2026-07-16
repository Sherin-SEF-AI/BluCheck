# Extraction queue between the S3 raw upload event and the frame worker, plus a DLQ.

resource "aws_sqs_queue" "extraction_dlq" {
  name                      = "${local.prefix}-extraction-dlq"
  message_retention_seconds = 1209600 # 14 days
  sqs_managed_sse_enabled   = true

  tags = { Name = "${local.prefix}-extraction-dlq" }
}

resource "aws_sqs_queue" "extraction" {
  name                       = "${local.prefix}-extraction"
  visibility_timeout_seconds = 300 # must exceed worker processing time per message
  message_retention_seconds  = 345600
  receive_wait_time_seconds  = 20 # long polling
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.extraction_dlq.arn
    maxReceiveCount     = 5
  })

  tags = { Name = "${local.prefix}-extraction" }
}

# Allow the media bucket to publish object-created events on the raw/ prefix.
resource "aws_sqs_queue_policy" "extraction" {
  queue_url = aws_sqs_queue.extraction.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowS3Publish"
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.extraction.arn
      Condition = {
        ArnLike      = { "aws:SourceArn" = "arn:aws:s3:::${local.media_bucket_name}" }
        StringEquals = { "aws:SourceAccount" = local.account_id }
      }
    }]
  })
}
