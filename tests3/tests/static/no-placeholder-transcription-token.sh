#!/usr/bin/env bash
# no-placeholder-transcription-token — transcription auth must never fall back
# to local/dev/example tokens in local deployment or handoff harness code.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$ROOT_DIR/tests3/lib/common.sh"

test_begin "no-placeholder-transcription-token"

BAD_PATTERNS=(
  "local-""test-token"
  "dev-""token"
  "TRANSCRIPTION_SERVICE_TOKEN=""your-token"
  "TRANSCRIPTION_SERVICE_TOKEN=""changeme"
  "TRANSCRIPTION_SERVICE_TOKEN=""dummy"
  "TRANSCRIPTION_SERVICE_TOKEN=""placeholder"
  "/home/dima/prod/prod-""transcription-service/.env"
  "PROD_""ENV="
  "grep -E '^API_""TOKEN='"
  "COMPOSE_""ENV="
  "grep -E '^TRANSCRIPTION_SERVICE_""TOKEN='"
  "TRANSCRIPTION_SERVICE_""URL:-"
  "TRANSCRIPTION_SERVICE_""TOKEN:-"
)

SEARCH_PATHS=(
  "$ROOT_DIR/tests3/lib/local-deploy.sh"
  "$ROOT_DIR/tests3/tests/smoke-bot-transcription-roundtrip.sh"
)

matches=""
for pattern in "${BAD_PATTERNS[@]}"; do
  found="$(rg -n --fixed-strings "$pattern" "${SEARCH_PATHS[@]}" 2>/dev/null || true)"
  if [ -n "$found" ]; then
    matches+="$found"$'\n'
  fi
done

if [ -z "$matches" ]; then
  step_pass TRANSCRIPTION_TOKEN_NO_PLACEHOLDER_FALLBACK "local deploy/smoke path uses deploy/compose/.env as SSOT and has no placeholder token or fallback source"
else
  step_fail TRANSCRIPTION_TOKEN_NO_PLACEHOLDER_FALLBACK "$(printf '%s' "$matches" | head -10 | tr '\n' ' ')"
fi

test_end
