#!/usr/bin/env bash
# =============================================================================
# model-catalog-boot.sh — agent-api boots in the Lite image with the model
# catalog unset, and with one set (ADR-0043).
#
# WHY: the catalog's schema was missing from Dockerfile.lite, and agent-api
# exited at import on every Lite boot, catalog or no catalog. This runs the
# exact interpreter, tree and module supervisord serves (`control_plane.api`)
# in the built image, offline, twice:
#   * VEXA_MODEL_CATALOG unset  → the module loads and there is no catalog;
#   * VEXA_MODEL_CATALOG set    → the module loads and the catalog parses,
#     its secret_ref resolved from a VEXA_MODEL_SECRET_* variable.
#
#   deploy/lite/tests/model-catalog-boot.sh [image]     # default vexa-lite:dev
# =============================================================================
set -euo pipefail
IMAGE="${1:-vexa-lite:dev}"
CATALOG='{"providers":{"lab-vllm":{"adapter":"openai_compatible","base_url":"http://10.0.0.5:8000/v1","auth":"none"},"openrouter":{"adapter":"openrouter","auth":"secret","secret_ref":"env:VEXA_MODEL_SECRET_OPENROUTER"}},"models":[{"id":"qwen3-32b","display_name":"Qwen 3 32B (self-hosted)","provider":"lab-vllm","model":"Qwen/Qwen3-32B","default":true},{"id":"or-sonnet","display_name":"Claude Sonnet via OpenRouter","provider":"openrouter","model":"anthropic/claude-sonnet-4.5"}]}'
PROBE='import control_plane.api  # what supervisord serves
from control_plane.model_providers import load
print(",".join(load().ids))'

boot() {  # $1 = label, $2 = catalog value (empty = unset), $3 = expected ids
  local out
  out=$(docker run --rm --network none --entrypoint sh \
          -e VEXA_MODEL_CATALOG="$2" -e VEXA_MODEL_SECRET_OPENROUTER=boot-probe-value "$IMAGE" \
          -c "cd /app/agent && PYTHONPATH=/app/agent /opt/venvs/agent/bin/python -c '$PROBE'" 2>&1 | tail -1)
  if [ "$out" = "$3" ]; then echo "  ✓ agent-api loads with the catalog $1"
  else echo "  ✗ agent-api with the catalog $1: expected models [$3], got: $out"; return 1; fi
}

fail=0
boot "unset" "" "" || fail=1
boot "set" "$CATALOG" "qwen3-32b,or-sonnet" || fail=1
exit "$fail"
