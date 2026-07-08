# In-cloud image builder. Because the operator environment has no Docker, images are
# built by AWS CodeBuild from source zips uploaded to a build bucket, then pushed to ECR.
# One project builds either image; the source and env vars are overridden per start-build.

resource "aws_s3_bucket" "build" {
  bucket        = "${var.resource_prefix}-build-${local.account_id}"
  force_destroy = true
  tags          = { Name = "${local.prefix}-build" }
}

resource "aws_s3_bucket_public_access_block" "build" {
  bucket                  = aws_s3_bucket.build.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "codebuild_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "codebuild" {
  name               = "${local.prefix}-codebuild"
  assume_role_policy = data.aws_iam_policy_document.codebuild_assume.json
}

data "aws_iam_policy_document" "codebuild" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["*"]
  }
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.api.arn, aws_ecr_repository.worker.arn]
  }
  statement {
    sid       = "BuildSource"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.build.arn}/*"]
  }
  statement {
    sid       = "Kms"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.main.arn]
  }
}

resource "aws_iam_role_policy" "codebuild" {
  name   = "${local.prefix}-codebuild"
  role   = aws_iam_role.codebuild.id
  policy = data.aws_iam_policy_document.codebuild.json
}

resource "aws_codebuild_project" "image" {
  name          = "${local.prefix}-image-build"
  service_role  = aws_iam_role.codebuild.arn
  build_timeout = 20

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type    = "BUILD_GENERAL1_SMALL"
    image           = "aws/codebuild/standard:7.0"
    type            = "LINUX_CONTAINER"
    privileged_mode = true # required to run docker build
  }

  source {
    type      = "S3"
    location  = "${aws_s3_bucket.build.bucket}/source/placeholder.zip"
    buildspec = <<-EOT
      version: 0.2
      phases:
        pre_build:
          commands:
            - aws ecr get-login-password --region "$AWS_DEFAULT_REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY"
        build:
          commands:
            - docker build -t "$ECR_REPO:latest" .
        post_build:
          commands:
            - docker push "$ECR_REPO:latest"
    EOT
  }

  logs_config {
    cloudwatch_logs {
      group_name = "/${local.prefix}/codebuild"
    }
  }
}

output "build_bucket" {
  description = "S3 bucket holding CodeBuild source zips."
  value       = aws_s3_bucket.build.id
}

output "codebuild_project" {
  description = "CodeBuild project that builds the API and worker images."
  value       = aws_codebuild_project.image.name
}
