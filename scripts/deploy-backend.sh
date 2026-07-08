#!/usr/bin/env bash
# Build the API image, push to ECR, and force a new ECS deployment.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/common.sh

require aws; require docker
TAG="${IMAGE_TAG:-latest}"

REPO="$(tf_output api_ecr_repository)"
CLUSTER="$(tf_output ecs_cluster)"

log "logging in to ECR"
ecr_login

log "building API image ${REPO}:${TAG}"
docker build -t "${REPO}:${TAG}" ./backend
docker push "${REPO}:${TAG}"

log "forcing new deployment of ${RESOURCE_PREFIX}-api"
aws ecs update-service \
  --cluster "$CLUSTER" \
  --service "${RESOURCE_PREFIX}-api" \
  --force-new-deployment \
  --region "$AWS_REGION" >/dev/null

log "done. API rolling out."
