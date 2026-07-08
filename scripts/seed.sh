#!/usr/bin/env bash
# Run migrations and seed the admin user + sample vehicles.
# Uses DATABASE_URL from the environment, or resolves it from Secrets Manager via
# the terraform output db_secret_arn when running against AWS.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/common.sh

require python3

if [[ -z "${DATABASE_URL:-}" ]]; then
  log "resolving DATABASE_URL from Secrets Manager"
  SECRET_ARN="$(tf_output db_secret_arn)"
  export DATABASE_URL="$(aws secretsmanager get-secret-value \
    --secret-id "$SECRET_ARN" --region "$AWS_REGION" \
    --query SecretString --output text)"
fi

log "running migrations"
( cd backend && python3 -m alembic upgrade head )

log "seeding admin user and sample vehicles"
python3 scripts/seed.py

log "seed complete"
