#!/usr/bin/env bash
# v0.10.5.3 static-grep checks (Pack M + Pack H).

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.5.3-static-greps :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.5.3-static-greps-$step"

case "$step" in

  chunk_buffer_trim)
    # Pack M: verify each platform's recording.ts has the splice-on-success
    # pattern after __vexaSaveRecordingChunk. The splice is what closes
    # the chunk-accumulation leak.
    bad=""
    for plat_path in \
        "services/vexa-bot/core/src/platforms/googlemeet/recording.ts" \
        "services/vexa-bot/core/src/platforms/msteams/recording.ts"; do
      f="$ROOT_DIR/$plat_path"
      if [ ! -f "$f" ]; then bad+=" missing:$(basename $(dirname $plat_path))"; continue; fi
      # Look for splice() of __vexaRecordedChunks within ondataavailable handler
      if ! grep -qE '__vexaRecordedChunks.*splice|buffer\.splice' "$f"; then
        bad+=" no-splice:$(basename $(dirname $plat_path))"
      fi
      # Look for the cap (VEXA_RECORDED_CHUNKS_CAP)
      if ! grep -q 'VEXA_RECORDED_CHUNKS_CAP' "$f"; then
        bad+=" no-cap:$(basename $(dirname $plat_path))"
      fi
    done
    if [ -z "$bad" ]; then
      step_pass BOT_RECORDING_CHUNK_BUFFER_TRIMMED "splice+cap present in gmeet+teams"
    else
      step_fail BOT_RECORDING_CHUNK_BUFFER_TRIMMED "missing in:$bad"
      exit 1
    fi
    ;;

  helm_replica_count_two)
    # Pack H: verify mcp.replicaCount: 2 (was 1 pre-fix)
    f="$ROOT_DIR/deploy/helm/charts/vexa/values.yaml"
    if [ ! -f "$f" ]; then
      step_fail HELM_REPLICA_COUNT_TWO_FOR_STATELESS "values.yaml missing"
      exit 1
    fi
    # Find mcp section, check replicaCount
    mcp_replica=$(awk '/^mcp:/{f=1} f && /^[a-z]/ && !/^mcp:/{f=0} f && /replicaCount:/{print $2; exit}' "$f")
    if [ "$mcp_replica" = "2" ]; then
      step_pass HELM_REPLICA_COUNT_TWO_FOR_STATELESS "mcp.replicaCount=2"
    else
      step_fail HELM_REPLICA_COUNT_TWO_FOR_STATELESS "mcp.replicaCount=$mcp_replica (want 2)"
      exit 1
    fi
    ;;

  *)
    step_fail "v0.10.5.3-static-greps" "unknown step: $step"
    exit 1
    ;;
esac

echo "  ──────────────────────────────────────────────"
echo ""
