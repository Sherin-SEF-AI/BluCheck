#!/usr/bin/env bash
# Build the Next.js dashboard, sync to S3, and invalidate CloudFront.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/common.sh

require aws; require npm

BUCKET="$(tf_output dashboard_bucket)"
DIST_ID="$(tf_output cloudfront_distribution_id)"
API_URL="$(tf_output api_base_url)"

log "building dashboard against API ${API_URL}"
pushd dashboard >/dev/null
npm ci
NEXT_PUBLIC_API_BASE_URL="$API_URL" npm run build
popd >/dev/null

log "syncing to s3://${BUCKET}"
aws s3 sync dashboard/out "s3://${BUCKET}" --delete --region "$AWS_REGION"

log "invalidating CloudFront ${DIST_ID}"
aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*" >/dev/null

log "dashboard deployed: ${API_URL/http/https}"
