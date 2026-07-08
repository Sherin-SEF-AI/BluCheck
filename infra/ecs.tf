# ECS Fargate cluster with two services: api (behind the ALB) and worker (queue-driven).

resource "aws_security_group" "ecs_tasks" {
  name        = "${local.prefix}-tasks-sg"
  description = "BluCheck Fargate tasks"
  vpc_id      = aws_vpc.main.id

  # API accepts traffic only from the ALB.
  ingress {
    description     = "API from ALB"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.prefix}-tasks-sg" }
}

resource "aws_ecs_cluster" "main" {
  name = "${local.prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

locals {
  api_image    = "${aws_ecr_repository.api.repository_url}:${var.container_image_tag}"
  worker_image = "${aws_ecr_repository.worker.repository_url}:${var.container_image_tag}"

  common_env = [
    { name = "AWS_REGION", value = var.aws_region },
    { name = "RESOURCE_PREFIX", value = var.resource_prefix },
    { name = "MEDIA_BUCKET", value = aws_s3_bucket.media.id },
    { name = "DASHBOARD_BUCKET", value = aws_s3_bucket.dashboard.id },
    { name = "EXTRACTION_QUEUE_URL", value = aws_sqs_queue.extraction.id },
    { name = "KMS_KEY_ID", value = aws_kms_key.main.key_id },
    { name = "FRAME_FPS", value = tostring(var.frame_fps) },
    { name = "THUMB_WIDTH", value = tostring(var.thumb_width) },
  ]

  injected_secrets = [
    { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.db_url.arn },
    { name = "JWT_SECRET", valueFrom = aws_secretsmanager_secret.jwt.arn },
  ]
}

# ----- API task + service -----
resource "aws_ecs_task_definition" "api" {
  family                   = "${local.prefix}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.api_task.arn

  container_definitions = jsonencode([{
    name      = "api"
    image     = local.api_image
    essential = true
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment = concat(local.common_env, [
      { name = "DASHBOARD_ORIGIN", value = "https://${aws_cloudfront_distribution.dashboard.domain_name}" },
      { name = "ECS_CLUSTER", value = aws_ecs_cluster.main.name },
      { name = "WORKER_SERVICE", value = aws_ecs_service.worker.name },
    ])
    secrets = local.injected_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.api.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "api"
      }
    }
    healthCheck = {
      command     = ["CMD-SHELL", "python -c 'import urllib.request,sys; sys.exit(0 if urllib.request.urlopen(\"http://localhost:8000/healthz\").status==200 else 1)'"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }
  }])
}

resource "aws_ecs_service" "api" {
  name            = "${local.prefix}-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  health_check_grace_period_seconds = 60

  # Deploy scripts push a new image tag; ignore count drift from autoscaling.
  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [aws_lb_listener.api]
}

# ----- Worker task + service -----
resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.prefix}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.worker_task.arn

  container_definitions = jsonencode([{
    name      = "worker"
    image     = local.worker_image
    essential = true
    environment = concat(local.common_env, [
      { name = "RUNPOD_SECRET_NAME", value = "${var.resource_prefix}/runpod" },
      { name = "FRAME_RESIZE_LONG_SIDE", value = "1280" },
    ])
    secrets = [{ name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.db_url.arn }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.worker.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "worker"
      }
    }
  }])
}

resource "aws_ecs_service" "worker" {
  name            = "${local.prefix}-worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_min_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false
  }

  lifecycle {
    ignore_changes = [desired_count]
  }
}

# ----- Worker autoscaling on queue depth (scale-to-zero) -----
# Design: step scaling driven by SQS backlog, NOT target tracking. Target tracking cannot
# scale out from 0 (its backlog-per-task metric divides by a running-task count of 0) and
# would tear a worker down mid-extraction. Step scaling on (visible + in-flight) messages
# wakes from 0 when work arrives and only returns to 0 once the queue is fully drained,
# because an in-flight message keeps the "in-flight" count above zero until it is deleted.
resource "aws_appautoscaling_target" "worker" {
  max_capacity       = var.worker_max_count
  min_capacity       = var.worker_min_count
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.worker.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

# Scale OUT / wake from zero. Sizes the fleet to the backlog. scaling_adjustment is the
# absolute desired count (ExactCapacity); step bounds are measured from the alarm threshold (1).
resource "aws_appautoscaling_policy" "worker_scale_out" {
  name               = "${local.prefix}-worker-scale-out"
  policy_type        = "StepScaling"
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  service_namespace  = aws_appautoscaling_target.worker.service_namespace

  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    metric_aggregation_type = "Maximum"
    cooldown                = 30

    step_adjustment { # backlog 1-9 messages
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 9
      scaling_adjustment          = 1
    }
    step_adjustment { # backlog 10-29
      metric_interval_lower_bound = 9
      metric_interval_upper_bound = 29
      scaling_adjustment          = 2
    }
    step_adjustment { # backlog 30-59
      metric_interval_lower_bound = 29
      metric_interval_upper_bound = 59
      scaling_adjustment          = 3
    }
    step_adjustment { # backlog 60+
      metric_interval_lower_bound = 59
      scaling_adjustment          = var.worker_max_count
    }
  }
}

# Scale IN to zero once the queue is empty AND nothing is in flight.
resource "aws_appautoscaling_policy" "worker_scale_in" {
  name               = "${local.prefix}-worker-scale-in"
  policy_type        = "StepScaling"
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  service_namespace  = aws_appautoscaling_target.worker.service_namespace

  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    metric_aggregation_type = "Maximum"
    cooldown                = 120

    step_adjustment {
      metric_interval_upper_bound = 0
      scaling_adjustment          = 0
    }
  }
}

# Backlog = visible + in-flight messages. Drives both policies.
resource "aws_cloudwatch_metric_alarm" "worker_backlog_present" {
  alarm_name          = "${local.prefix}-worker-backlog-present"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_description    = "Wake / size the worker fleet to the SQS backlog."
  alarm_actions       = [aws_appautoscaling_policy.worker_scale_out.arn]

  metric_query {
    id          = "backlog"
    expression  = "visible + inflight"
    label       = "messages visible + in-flight"
    return_data = true
  }
  metric_query {
    id = "visible"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesVisible"
      period      = 60
      stat        = "Maximum"
      dimensions  = { QueueName = aws_sqs_queue.extraction.name }
    }
  }
  metric_query {
    id = "inflight"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesNotVisible"
      period      = 60
      stat        = "Maximum"
      dimensions  = { QueueName = aws_sqs_queue.extraction.name }
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "worker_backlog_empty" {
  alarm_name          = "${local.prefix}-worker-backlog-empty"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_description   = "Queue drained and nothing in flight: return the worker to zero."
  alarm_actions       = [aws_appautoscaling_policy.worker_scale_in.arn]

  metric_query {
    id          = "backlog"
    expression  = "visible + inflight"
    label       = "messages visible + in-flight"
    return_data = true
  }
  metric_query {
    id = "visible"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesVisible"
      period      = 60
      stat        = "Maximum"
      dimensions  = { QueueName = aws_sqs_queue.extraction.name }
    }
  }
  metric_query {
    id = "inflight"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesNotVisible"
      period      = 60
      stat        = "Maximum"
      dimensions  = { QueueName = aws_sqs_queue.extraction.name }
    }
  }
}
