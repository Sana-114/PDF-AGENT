#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf -- "$WORK_DIR"' EXIT

cat > "$WORK_DIR/curl" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail

body_file=""
header_file=""
netrc_file=""
url=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) body_file="$2"; shift 2 ;;
    --dump-header) header_file="$2"; shift 2 ;;
    --netrc-file) netrc_file="$2"; shift 2 ;;
    --max-time|--write-out) shift 2 ;;
    --silent|--show-error) shift ;;
    http://*|https://*) url="$1"; shift ;;
    *) echo "unexpected curl argument: $1" >&2; exit 90 ;;
  esac
done

[[ -n "$body_file" && -n "$header_file" && -n "$url" ]]
if [[ "$url" == http://* ]]; then
  printf 'HTTP/1.1 308 Permanent Redirect\r\nLocation: https://paper.test/\r\n\r\n' > "$header_file"
  : > "$body_file"
  printf '308'
elif [[ -z "$netrc_file" ]]; then
  printf 'HTTP/2 401\r\nWWW-Authenticate: Basic realm="restricted"\r\n\r\n' > "$header_file"
  : > "$body_file"
  printf '401'
elif [[ "$url" == */api/v1/health ]]; then
  printf 'HTTP/2 200\r\nX-Content-Type-Options: nosniff\r\nX-Frame-Options: SAMEORIGIN\r\nReferrer-Policy: strict-origin-when-cross-origin\r\n\r\n' > "$header_file"
  printf '{"status":"ok","service":"PaperPilot"}' > "$body_file"
  printf '200'
elif [[ "$url" == */api/v1/agent/status ]]; then
  : > "$header_file"
  if [[ "${FAKE_BAD_MODEL:-0}" == "1" ]]; then
    printf '{"provider":"deepseek","model":"wrong","llm_configured":true}' > "$body_file"
  else
    printf '{"provider":"deepseek","model":"deepseek-flash","llm_configured":true,"embedding_provider":"openai-compatible","reranker_provider":"tei","retrieval_mode":"hybrid_qdrant_rrf+cross_encoder"}' > "$body_file"
  fi
  printf '200'
else
  : > "$header_file"
  printf '<html><title>PaperPilot</title></html>' > "$body_file"
  printf '200'
fi
EOF
chmod +x "$WORK_DIR/curl"
printf 'test-only-password\n' > "$WORK_DIR/password"

PATH="$WORK_DIR:$PATH" \
PUBLIC_DEMO_PASSWORD_FILE="$WORK_DIR/password" \
  "$REPO_ROOT/scripts/verify_public_demo.sh" https://paper.test standard > "$WORK_DIR/pass.log"
grep -q 'Public demo transport and configuration gate passed' "$WORK_DIR/pass.log"

set +e
PATH="$WORK_DIR:$PATH" \
PUBLIC_DEMO_PASSWORD_FILE="$WORK_DIR/password" \
FAKE_BAD_MODEL=1 \
  "$REPO_ROOT/scripts/verify_public_demo.sh" https://paper.test standard \
  > "$WORK_DIR/fail.log" 2>&1
status=$?
set -e
[[ "$status" -ne 0 ]]
grep -q 'deepseek-flash is not active' "$WORK_DIR/fail.log"

echo "verify_public_demo.sh tests passed"
