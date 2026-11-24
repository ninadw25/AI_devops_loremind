#!/usr/bin/env bash
# Deploy the one-day course demo in two reviewed steps:
#   deploy/oneday/up.sh          build the bundle from the local stack and write a saved Terraform plan
#   deploy/oneday/up.sh apply    apply exactly that saved plan, then print the URL and demo password
# Needs docker, terraform and the AWS profile "loremind". Destroy with deploy/oneday/down.sh.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ENGINE="$HERE/../.."; UI="$ENGINE/../lore-frontend/app"
B="${BUNDLE_DIR:-/tmp/lore-oneday}"; HOST="${DEMO_HOST:-lore-demo.loremind.in}"
mkdir -p "$B"; chmod 700 "$B"
PLAN="$B/oneday.tfplan"

banner() {
  echo
  echo "  URL       https://$HOST   (ready about 8 minutes after apply: image build + certificate)"
  echo "  Password  user lore / $(cat "$B/demo-password")     then the app login demo@lore.local / Lore@Demo2026"
  echo "  Logs      aws ssm start-session --target <instance_id>, then: sudo tail -f /var/log/lore-demo-boot.log"
  echo "  Destroy   deploy/oneday/down.sh   (runs until destroyed; HOURS via -var hours=N to self-terminate)"
}
if [ "${1:-}" = apply ]; then
  cd "$HERE" && terraform apply -input=false "$PLAN" && rm -f "$PLAN" && terraform output && banner
  exit 0
fi
val() { grep "^$1=" "$ENGINE/.env" | tail -1 | cut -d= -f2-; }

# 1. bundle (each piece is skipped when it already exists)
if [ ! -f "$B/neo4j-data.tgz" ]; then   # clean shutdown gives a consistent copy of the graph store
  docker stop -t 60 lore-neo4j
  docker run --rm --entrypoint tar -v lore-ingestion_neo4j_data:/data:ro -v "$B":/out neo4j:5.26-community \
    czf /out/neo4j-data.tgz --numeric-owner -C /data .
  docker start lore-neo4j
fi
[ -f "$B/lore_memory.sql.gz" ] || docker exec lore-postgres pg_dump -U lore --no-owner --no-privileges lore_memory | gzip > "$B/lore_memory.sql.gz"
[ -f "$B/src.tgz" ] || (cd "$ENGINE" && git ls-files -z | tar czf "$B/src.tgz" --null -T -)
tar czf "$B/stack.tgz" -C "$HERE" docker-compose.yml Caddyfile
if [ ! -f "$B/ui.tgz" ]; then
  printf 'VITE_API_URL=https://%s\nVITE_CORE_URL=https://%s\nVITE_INTERNAL_AUTH_SECRET=\nVITE_DEMO_MODE=true\nVITE_TOKEN_HEADER=X-Lore-Token\n' \
    "$HOST" "$HOST" > "$UI/.env.oneday"
  (cd "$UI" && npx vite build --mode oneday --outDir "$B/ui-dist" --emptyOutDir)
  tar czf "$B/ui.tgz" -C "$B/ui-dist" . && rm -rf "$B/ui-dist"
fi

# 2. secrets: fresh internal secret and Postgres password; the demo password is shown once below
INTERNAL=$(openssl rand -hex 24); PG=$(openssl rand -hex 16)
DEMO_PASS="${DEMO_PASS:-lore-$(openssl rand -hex 4)}"; (umask 077; echo "$DEMO_PASS" > "$B/demo-password")
HASH=$(docker run --rm caddy:2 caddy hash-password --plaintext "$DEMO_PASS" | tr -d '\n' | base64 -w0)   # base64: no '$' for compose
export TF_VAR_engine_env="LLM_PROVIDER=bedrock
LLM_MODEL=$(val LLM_MODEL)
BEDROCK_REGION=$(val BEDROCK_REGION)
LLM_MAX_TOKENS=$(val LLM_MAX_TOKENS)
LLM_TEMPERATURE=$(val LLM_TEMPERATURE)
EMBEDDING_PROVIDER=$(val EMBEDDING_PROVIDER)
EMBEDDING_MODEL=$(val EMBEDDING_MODEL)
EMBEDDING_DIM=$(val EMBEDDING_DIM)
NVIDIA_API_KEY=$(val NVIDIA_API_KEY)
NEO4J_URI=bolt://neo4j:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=$(val NEO4J_PASSWORD)
PG_PASSWORD=$PG
MEMORY_POSTGRES_URI=postgresql://lore:$PG@postgres:5432/lore_memory
INTERNAL_AUTH_SECRET=$INTERNAL
TOKEN_EXPIRY_HOURS=$(val TOKEN_EXPIRY_HOURS)
ANALYSIS_TIMEOUT_MS=4000
CORS_ORIGINS=https://$HOST
ENVIRONMENT=$(val ENVIRONMENT)
PORT=8000"
export TF_VAR_caddy_env="DEMO_HOST=$HOST
DEMO_USER=lore
DEMO_HASH=$HASH
INTERNAL_AUTH_SECRET=$INTERNAL"

# 3. infrastructure: plan only; review it, then run `up.sh apply`
cd "$HERE"
terraform init -input=false >/dev/null
terraform plan -input=false -out "$PLAN" -var "bundle_dir=$B" -var "host=$HOST"
echo; echo "  Plan saved to $PLAN. Review it, then run: deploy/oneday/up.sh apply"
