# Customer-managed KMS key for encrypting media at rest (SSE-KMS), RDS, and secrets.

resource "aws_kms_key" "main" {
  description             = "${local.prefix} media, database, and secrets encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  # Default key policy grants the account root full control; additionally allow the
  # CloudWatch Logs service to use the key for encrypting BluCheck log groups.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccount"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowCloudWatchLogs"
        Effect    = "Allow"
        Principal = { Service = "logs.${var.aws_region}.amazonaws.com" }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/${local.prefix}/*"
          }
        }
      },
    ]
  })

  tags = { Name = "${local.prefix}-kms" }
}

resource "aws_kms_alias" "main" {
  name          = "alias/${local.prefix}"
  target_key_id = aws_kms_key.main.key_id
}
