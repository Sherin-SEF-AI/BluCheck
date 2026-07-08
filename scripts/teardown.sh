#!/usr/bin/env bash
# Destroy all BluCheck AWS resources. Empties the S3 buckets first (terraform cannot
# delete non-empty buckets), then terraform destroy.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/common.sh

require aws; require terraform

log "This will DESTROY all BluCheck AWS resources in ${AWS_REGION}."
read -r -p "Type 'destroy' to continue: " confirm
[[ "$confirm" == "destroy" ]] || die "aborted"

# Empty buckets (including versioned objects) so destroy can remove them.
for out in media_bucket dashboard_bucket; do
  bucket="$(terraform -chdir="$INFRA_DIR" output -raw "$out" 2>/dev/null || true)"
  if [[ -n "$bucket" ]]; then
    log "emptying s3://${bucket}"
    aws s3 rm "s3://${bucket}" --recursive --region "$AWS_REGION" || true
    # Remove all versions and delete markers.
    aws s3api list-object-versions --bucket "$bucket" --region "$AWS_REGION" \
      --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json 2>/dev/null \
      | jq -e '.Objects != null and (.Objects | length) > 0' >/dev/null 2>&1 \
      && aws s3api delete-objects --bucket "$bucket" --region "$AWS_REGION" \
           --delete "$(aws s3api list-object-versions --bucket "$bucket" --region "$AWS_REGION" \
             --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json)" >/dev/null 2>&1 || true
  fi
done

log "terraform destroy"
terraform -chdir="$INFRA_DIR" destroy -auto-approve

log "teardown complete. No billable BluCheck resources remain."
