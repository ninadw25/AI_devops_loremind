#!/usr/bin/env bash
# Remove every resource of the one-day demo (instance, bucket, role, secrets, DNS record).
set -euo pipefail
cd "$(dirname "$0")"
terraform destroy -input=false -auto-approve -var "bundle_dir=/dev/null" -var "engine_env=x" -var "caddy_env=x"
