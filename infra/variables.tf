variable "aws_region" {
  description = "AWS region. Locked to ap-south-1 for in-India data residency."
  type        = string
  default     = "ap-south-1"
}

variable "resource_prefix" {
  description = "Prefix applied to every resource name to isolate BluCheck from other stacks."
  type        = string
  default     = "blucheck"
}

variable "vpc_cidr" {
  description = "CIDR block for the BluCheck VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t3.micro"
}

variable "db_allocated_storage" {
  description = "RDS allocated storage in GB."
  type        = number
  default     = 20
}

variable "db_multi_az" {
  description = "Run RDS Multi-AZ. Off by default to control cost; enable for production."
  type        = bool
  default     = false
}

variable "db_backup_retention_days" {
  description = "RDS automated backup retention in days. Set to 0 only on throwaway/free-tier stacks."
  type        = number
  default     = 7
}

variable "db_deletion_protection" {
  description = "Block accidental deletion of the database. Keep true in production; teardown sets false."
  type        = bool
  default     = true
}

variable "db_skip_final_snapshot" {
  description = "Skip the final snapshot on delete. Keep false in production so a destroy still leaves a backup."
  type        = bool
  default     = false
}

variable "db_name" {
  description = "Initial database name."
  type        = string
  default     = "blucheck"
}

variable "db_username" {
  description = "Master database username."
  type        = string
  default     = "blucheck"
}

variable "api_cpu" {
  description = "Fargate CPU units for the API task."
  type        = number
  default     = 512
}

variable "api_memory" {
  description = "Fargate memory (MiB) for the API task."
  type        = number
  default     = 1024
}

variable "api_desired_count" {
  description = "Desired number of API tasks."
  type        = number
  default     = 1
}

variable "worker_cpu" {
  description = "Fargate CPU units for the worker task (ffmpeg is CPU heavy)."
  type        = number
  default     = 1024
}

variable "worker_memory" {
  description = "Fargate memory (MiB) for the worker task."
  type        = number
  default     = 2048
}

variable "worker_min_count" {
  description = "Minimum worker tasks. 0 enables scale-to-zero: the worker is torn down when the queue is idle and woken by a CloudWatch alarm when work arrives (adds ~1 min cold-start to the first inspection after idle)."
  type        = number
  default     = 0
}

variable "worker_max_count" {
  description = "Maximum worker tasks (scales on queue depth)."
  type        = number
  default     = 4
}

variable "frame_fps" {
  description = "Frames per second extracted from each video. Drives storage cost."
  type        = number
  default     = 2
}

variable "thumb_width" {
  description = "Thumbnail width in pixels."
  type        = number
  default     = 480
}

variable "raw_retention_days" {
  description = "Days to retain raw uploaded videos before expiry."
  type        = number
  default     = 90
}

variable "frame_retention_days" {
  description = "Days to retain extracted frames before expiry."
  type        = number
  default     = 365
}

variable "container_image_tag" {
  description = "Image tag deployed to ECS. Deploy scripts push this tag to ECR."
  type        = string
  default     = "latest"
}

variable "log_retention_days" {
  description = "CloudWatch log retention."
  type        = number
  default     = 30
}

variable "alarm_email" {
  description = "Optional email for SNS alarm notifications. Empty disables the subscription."
  type        = string
  default     = ""
}
