#!/usr/bin/env bash
# Shared helpers for deploy scripts. Sourced, not executed directly.
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-south-1}"
RESOURCE_PREFIX="${RESOURCE_PREFIX:-blucheck}"
INFRA_DIR="${INFRA_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../infra" && pwd)}"

log() { printf '\033[36m[blucheck]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[blucheck ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

require() { command -v "$1" >/dev/null 2>&1 || die "missing required tool: $1"; }

tf_output() {
  terraform -chdir="$INFRA_DIR" output -raw "$1" 2>/dev/null || die "terraform output '$1' unavailable; run 'make infra-up' first"
}

ecr_login() {
  local account_id
  account_id="$(aws sts get-caller-identity --query Account --output text)"
  aws ecr get-login-password --region "$AWS_REGION" \
    | docker login --username AWS --password-stdin "${account_id}.dkr.ecr.${AWS_REGION}.amazonaws.com"
}
