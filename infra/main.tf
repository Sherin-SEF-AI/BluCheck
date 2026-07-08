# BluCheck infrastructure root.
# Region ap-south-1 (Mumbai), all resources prefixed with var.resource_prefix.
# State is local by default; see README for switching to an S3 + DynamoDB backend.

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "BluCheck"
      Application = "fleet-cleanliness-inspection"
      ManagedBy   = "terraform"
      Prefix      = var.resource_prefix
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  prefix     = var.resource_prefix
  account_id = data.aws_caller_identity.current.account_id
  azs        = slice(data.aws_availability_zones.available.names, 0, 2)

  media_bucket_name     = "${var.resource_prefix}-media-${local.account_id}"
  dashboard_bucket_name = "${var.resource_prefix}-dashboard-${local.account_id}"
}
