#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/verify_public_demo.sh <https-base-url> <lite|standard>

Environment:
  PUBLIC_DEMO_USERNAME       Reviewer username (default: reviewer)
  PUBLIC_DEMO_PASSWORD_FILE  File containing only the reviewer password
  PUBLIC_DEMO_TIMEOUT        Per-request timeout in seconds (default: 20)

If PUBLIC_DEMO_PASSWORD_FILE is omitted, the password is requested without echo
from an interactive terminal. The script never prints or stores the password.
EOF
}

fail() {
  echo "[fail] $*" >&2
  exit 1
}

pass() {
  echo "[pass] $*"
}

require_match() {
  local file="$1"
  local pattern="$2"
  local message="$3"
  grep -Eiq "$pattern" "$file" || fail "$message"
}

request() {
  local url="$1"
  local body_file="$2"
  local header_file="$3"
  shift 3
  curl --silent --show-error --max-time "$TIMEOUT_SECONDS" \
    --output "$body_file" --dump-header "$header_file" --write-out '%{http_code}' \
    "$@" "$url"
}

BASE_URL="${1:-}"
MODE="${2:-}"
[[ -n "$BASE_URL" && -n "$MODE" ]] || { usage; exit 2; }
[[ "$MODE" =~ ^(lite|standard)$ ]] || fail "mode must be lite or standard"
[[ "$BASE_URL" =~ ^https://[A-Za-z0-9.-]+$ ]] || \
  fail "base URL must be an HTTPS origin without a port, path, query, or trailing slash"

command -v curl >/dev/null 2>&1 || fail "curl is required"

USERNAME="${PUBLIC_DEMO_USERNAME:-reviewer}"
TIMEOUT_SECONDS="${PUBLIC_DEMO_TIMEOUT:-20}"
[[ "$USERNAME" =~ ^[A-Za-z0-9._-]+$ ]] || fail "PUBLIC_DEMO_USERNAME contains unsupported characters"
[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail "PUBLIC_DEMO_TIMEOUT must be a positive integer"

if [[ -n "${PUBLIC_DEMO_PASSWORD_FILE:-}" ]]; then
  [[ -f "$PUBLIC_DEMO_PASSWORD_FILE" ]] || fail "PUBLIC_DEMO_PASSWORD_FILE does not exist"
  IFS= read -r PASSWORD < "$PUBLIC_DEMO_PASSWORD_FILE" || true
elif [[ -t 0 ]]; then
  read -r -s -p "Reviewer password: " PASSWORD
  echo
else
  fail "set PUBLIC_DEMO_PASSWORD_FILE or run interactively to enter the reviewer password"
fi
PASSWORD="${PASSWORD%$'\r'}"
[[ -n "${PASSWORD:-}" ]] || fail "reviewer password is empty"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf -- "$WORK_DIR"' EXIT

HOST="${BASE_URL#https://}"
NETRC_FILE="$WORK_DIR/reviewer.netrc"
NETRC_PASSWORD="${PASSWORD//\\/\\\\}"
NETRC_PASSWORD="${NETRC_PASSWORD//\"/\\\"}"
printf 'machine "%s"\nlogin "%s"\npassword "%s"\n' \
  "$HOST" "$USERNAME" "$NETRC_PASSWORD" > "$NETRC_FILE"
chmod 600 "$NETRC_FILE"
unset PASSWORD NETRC_PASSWORD

http_headers="$WORK_DIR/http.headers"
http_body="$WORK_DIR/http.body"
http_status="$(request "http://${HOST}/" "$http_body" "$http_headers")"
[[ "$http_status" =~ ^30[1278]$ ]] || fail "plain HTTP did not redirect to HTTPS (status: $http_status)"
require_match "$http_headers" '^location:[[:space:]]*https://' "HTTP redirect has no HTTPS Location header"
pass "HTTP redirects to HTTPS"

unauth_headers="$WORK_DIR/unauth.headers"
unauth_body="$WORK_DIR/unauth.body"
unauth_status="$(request "${BASE_URL}/api/v1/health" "$unauth_body" "$unauth_headers")"
[[ "$unauth_status" == "401" ]] || fail "unauthenticated API request was not rejected (status: $unauth_status)"
require_match "$unauth_headers" '^www-authenticate:[[:space:]]*Basic' "401 response has no Basic Auth challenge"
pass "reviewer gate rejects unauthenticated API access"

health_headers="$WORK_DIR/health.headers"
health_body="$WORK_DIR/health.body"
health_status="$(request "${BASE_URL}/api/v1/health" "$health_body" "$health_headers" \
  --netrc-file "$NETRC_FILE")"
[[ "$health_status" == "200" ]] || fail "authenticated health endpoint returned $health_status"
require_match "$health_body" '"status"[[:space:]]*:[[:space:]]*"ok"' "health response is not ok"
require_match "$health_headers" '^x-content-type-options:[[:space:]]*nosniff' "missing X-Content-Type-Options"
require_match "$health_headers" '^x-frame-options:[[:space:]]*SAMEORIGIN' "missing X-Frame-Options"
require_match "$health_headers" '^referrer-policy:[[:space:]]*strict-origin-when-cross-origin' "missing Referrer-Policy"
pass "authenticated backend health and security headers are valid"

frontend_headers="$WORK_DIR/frontend.headers"
frontend_body="$WORK_DIR/frontend.body"
frontend_status="$(request "${BASE_URL}/" "$frontend_body" "$frontend_headers" \
  --netrc-file "$NETRC_FILE")"
[[ "$frontend_status" == "200" ]] || fail "authenticated frontend returned $frontend_status"
require_match "$frontend_body" 'PaperPilot' "frontend response does not contain the PaperPilot application"
pass "authenticated frontend is available"

agent_headers="$WORK_DIR/agent.headers"
agent_body="$WORK_DIR/agent.body"
agent_status="$(request "${BASE_URL}/api/v1/agent/status" "$agent_body" "$agent_headers" \
  --netrc-file "$NETRC_FILE")"
[[ "$agent_status" == "200" ]] || fail "agent status endpoint returned $agent_status"
require_match "$agent_body" '"provider"[[:space:]]*:[[:space:]]*"deepseek"' "DeepSeek provider is not active"
require_match "$agent_body" '"model"[[:space:]]*:[[:space:]]*"deepseek-flash"' "deepseek-flash is not active"
require_match "$agent_body" '"llm_configured"[[:space:]]*:[[:space:]]*true' "LLM is not configured"

if [[ "$MODE" == "standard" ]]; then
  require_match "$agent_body" '"embedding_provider"[[:space:]]*:[[:space:]]*"openai-compatible"' \
    "standard mode is not using the BGE embedding service"
  require_match "$agent_body" '"reranker_provider"[[:space:]]*:[[:space:]]*"tei"' \
    "standard mode is not using the BGE reranker"
  require_match "$agent_body" '"retrieval_mode"[[:space:]]*:[[:space:]]*"[^"]*cross_encoder' \
    "standard mode does not report cross-encoder retrieval"
else
  require_match "$agent_body" '"embedding_provider"[[:space:]]*:[[:space:]]*"hash"' \
    "lite mode does not report the hash embedding provider"
  require_match "$agent_body" '"reranker_provider"[[:space:]]*:[[:space:]]*"none"' \
    "lite mode unexpectedly reports a reranker"
fi
pass "DeepSeek and ${MODE} retrieval mode are configured"

echo "Public demo transport and configuration gate passed for ${BASE_URL}."
echo "Next manual gate: upload -> parse -> grounded answer -> PDF citation jump -> refusal case."
