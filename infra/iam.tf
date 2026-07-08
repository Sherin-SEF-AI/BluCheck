# Least-privilege IAM. One execution role (pull images, write logs, read secrets at
# launch) and two task roles scoped to exactly the S3 prefixes, queue, secrets, and
# KMS grants each service needs.

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# ----- Execution role (shared) -----
resource "aws_iam_role" "task_execution" {
  name               = "${local.prefix}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "task_execution_managed" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Execution role must read the two secrets injected into the task and decrypt them.
data "aws_iam_policy_document" "execution_secrets" {
  statement {
    sid       = "ReadSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.db_url.arn, aws_secretsmanager_secret.jwt.arn]
  }
  statement {
    sid       = "DecryptSecrets"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.main.arn]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "${local.prefix}-execution-secrets"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

# ----- API task role -----
resource "aws_iam_role" "api_task" {
  name               = "${local.prefix}-api-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "api_task" {
  # Presign uploads and reads across the media prefixes the API touches.
  statement {
    sid     = "MediaObjects"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
    resources = [
      "${aws_s3_bucket.media.arn}/raw/*",
      "${aws_s3_bucket.media.arn}/frames/*",
      "${aws_s3_bucket.media.arn}/thumbs/*",
    ]
  }
  statement {
    sid       = "MediaList"
    actions   = ["s3:ListBucket", "s3:ListBucketMultipartUploads"]
    resources = [aws_s3_bucket.media.arn]
  }
  # Fallback enqueue of extraction messages.
  statement {
    sid       = "EnqueueExtraction"
    actions   = ["sqs:SendMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.extraction.arn]
  }
  statement {
    sid       = "Secrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.db_url.arn, aws_secretsmanager_secret.jwt.arn, data.aws_secretsmanager_secret.runpod.arn, data.aws_secretsmanager_secret.fcm.arn]
  }
  statement {
    sid       = "Kms"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.main.arn]
  }
  statement {
    sid       = "PlateOcr"
    actions   = ["rekognition:DetectText"]
    resources = ["*"]
  }
  # Wake the scale-to-zero worker on enqueue so processing starts in seconds.
  statement {
    sid       = "WakeWorker"
    actions   = ["ecs:UpdateService", "ecs:DescribeServices"]
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.main.arn]
    }
  }
}

resource "aws_iam_role_policy" "api_task" {
  name   = "${local.prefix}-api-task"
  role   = aws_iam_role.api_task.id
  policy = data.aws_iam_policy_document.api_task.json
}

# ----- Worker task role -----
resource "aws_iam_role" "worker_task" {
  name               = "${local.prefix}-worker-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "worker_task" {
  statement {
    sid       = "ReadRaw"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.media.arn}/raw/*"]
  }
  statement {
    sid       = "WriteFrames"
    actions   = ["s3:PutObject", "s3:GetObject"]  # GetObject: scoring reads back selected frames
    resources = ["${aws_s3_bucket.media.arn}/frames/*", "${aws_s3_bucket.media.arn}/thumbs/*"]
  }
  statement {
    sid       = "MediaList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.media.arn]
  }
  statement {
    sid = "ConsumeQueue"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:ChangeMessageVisibility",
    ]
    resources = [aws_sqs_queue.extraction.arn]
  }
  statement {
    sid       = "Secrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.db_url.arn, data.aws_secretsmanager_secret.runpod.arn]
  }
  statement {
    sid       = "Kms"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.main.arn]
  }
}

resource "aws_iam_role_policy" "worker_task" {
  name   = "${local.prefix}-worker-task"
  role   = aws_iam_role.worker_task.id
  policy = data.aws_iam_policy_document.worker_task.json
}
