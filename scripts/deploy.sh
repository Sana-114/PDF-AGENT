#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/deploy.sh <lite|standard|standard-gpu> <start|status|stop|logs|config>

Environment:
  DEPLOY_ENV_FILE          Production env file (default: .env.production)
  DEPLOY_BGE_ENV_FILE      BGE env file (default: .env.bge)
  DEPLOY_TIMEOUT_SECONDS   Compose startup wait timeout (default: 900)
EOF
}

fail() {
  echo "[error] $*" >&2
  exit 1
}

env_value() {
  local name="$1"
  local line
  line="$(grep -E "^[[:space:]]*${name}=" "$ENV_FILE" | tail -n 1 || true)"
  line="${line#*=}"
  line="${line%$'\r'}"
  if [[ "$line" == \'*\' || "$line" == \"*\" ]]; then
    line="${line:1:${#line}-2}"
  fi
  printf '%s' "$line"
}

require_secret() {
  local name="$1"
  local value
  value="$(env_value "$name")"
  [[ -n "$value" ]] || fail "$name is missing from $ENV_FILE"
  [[ "$value" != *CHANGE_ME* ]] || fail "$name still contains the CHANGE_ME placeholder"
  [[ "$value" != "change-me-in-production" ]] || fail "$name still uses the development default"
}

MODE="${1:-}"
ACTION="${2:-}"
[[ -n "$MODE" && -n "$ACTION" ]] || { usage; exit 2; }
[[ "$MODE" =~ ^(lite|standard|standard-gpu)$ ]] || fail "Unsupported mode: $MODE"
[[ "$ACTION" =~ ^(start|status|stop|logs|config)$ ]] || fail "Unsupported action: $ACTION"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ENV_FILE="${DEPLOY_ENV_FILE:-.env.production}"
BGE_ENV_FILE="${DEPLOY_BGE_ENV_FILE:-.env.bge}"
TIMEOUT_SECONDS="${DEPLOY_TIMEOUT_SECONDS:-900}"

[[ -f "$ENV_FILE" ]] || fail "$ENV_FILE does not exist; copy .env.production.example first"

for name in PUBLIC_DOMAIN FRONTEND_ORIGIN NEXT_PUBLIC_API_BASE_URL DEMO_USERNAME \
  DEMO_PASSWORD_HASH POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD COMPOSE_DATABASE_URL \
  MINIO_ROOT_USER MINIO_ROOT_PASSWORD; do
  require_secret "$name"
done

domain="$(env_value PUBLIC_DOMAIN)"
[[ "$domain" =~ ^[A-Za-z0-9.-]+$ ]] || fail "PUBLIC_DOMAIN must be a hostname without scheme, port, path, or whitespace"
[[ "$domain" != "localhost" && "$domain" != *.example.com ]] || fail "PUBLIC_DOMAIN still uses a local or example hostname"
[[ "$(env_value FRONTEND_ORIGIN)" == "https://${domain}" ]] || fail "FRONTEND_ORIGIN must equal https://${domain}"
[[ "$(env_value NEXT_PUBLIC_API_BASE_URL)" == "https://${domain}/api/v1" ]] || fail "NEXT_PUBLIC_API_BASE_URL must equal https://${domain}/api/v1"

expected_database_url="postgresql+psycopg://$(env_value POSTGRES_USER):$(env_value POSTGRES_PASSWORD)@postgres:5432/$(env_value POSTGRES_DB)"
[[ "$(env_value COMPOSE_DATABASE_URL)" == "$expected_database_url" ]] || \
  fail "COMPOSE_DATABASE_URL must match POSTGRES_USER, POSTGRES_PASSWORD, and POSTGRES_DB"

password_hash="$(env_value DEMO_PASSWORD_HASH)"
[[ "$password_hash" == \$2a\$* || "$password_hash" == \$2b\$* || "$password_hash" == \$2y\$* ]] || \
  fail "DEMO_PASSWORD_HASH must be a Caddy bcrypt hash enclosed in single quotes"

if [[ "$(env_value LLM_PROVIDER)" == "deepseek" ]]; then
  require_secret LLM_API_KEY
  [[ "$(env_value LLM_BASE_URL)" == https://* ]] || fail "LLM_BASE_URL must use HTTPS"
fi

command -v docker >/dev/null 2>&1 || fail "docker is not installed"
docker version >/dev/null 2>&1 || fail "Docker Engine is unavailable"
docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin is unavailable"

COMPOSE=(docker compose --env-file "$ENV_FILE")
if [[ "$MODE" == "lite" ]]; then
  COMPOSE+=( -f docker-compose.yml -f docker-compose.lite.yml -f docker-compose.prod.yml )
else
  [[ -f "$BGE_ENV_FILE" ]] || fail "$BGE_ENV_FILE does not exist; copy .env.bge.example first"
  COMPOSE+=( --env-file "$BGE_ENV_FILE" --profile local-bge -f docker-compose.yml -f docker-compose.prod.yml )
  if [[ "$MODE" == "standard-gpu" ]]; then
    command -v nvidia-smi >/dev/null 2>&1 || fail "nvidia-smi is unavailable; install the NVIDIA driver and Container Toolkit"
    COMPOSE+=( -f docker-compose.bge-gpu.yml )
  fi
fi

"${COMPOSE[@]}" config --quiet

case "$ACTION" in
  start)
    "${COMPOSE[@]}" up -d --build --wait --wait-timeout "$TIMEOUT_SECONDS"
    "${COMPOSE[@]}" ps
    echo "PaperPilot is ready at https://${domain}/"
    ;;
  status)
    "${COMPOSE[@]}" ps
    "${COMPOSE[@]}" exec -T backend python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=5).read(); print('backend health: ok')"
    ;;
  stop)
    "${COMPOSE[@]}" stop
    echo "Services stopped; named volumes and Caddy certificates were preserved."
    ;;
  logs)
    "${COMPOSE[@]}" logs --tail=200 -f
    ;;
  config)
    echo "Production Compose configuration is valid for mode: $MODE"
    ;;
esac
