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
    # Pack M: verify the chunk-buffer trim discipline. Pre-Pack-U the
    # cap=10 + splice-on-upload pattern lived in each platform's
    # recording.ts (duplicated). v0.10.6 Pack U.2 unified GMeet + Teams
    # capture into BrowserMediaRecorderPipeline (utils/browser.ts);
    # browser.ts is now the canonical home for the discipline. Pack M
    # intent (no chunk-accumulation leak) is preserved either way.
    f="$ROOT_DIR/services/vexa-bot/core/src/utils/browser.ts"
    if [ ! -f "$f" ]; then
      step_fail BOT_RECORDING_CHUNK_BUFFER_TRIMMED "browser.ts missing"
      exit 1
    fi
    bad=""
    if ! grep -q 'class BrowserMediaRecorderPipeline' "$f"; then
      bad+=" no-pipeline-class"
    fi
    # Splice removes uploaded chunks from the in-flight buffer
    if ! grep -qE '\.splice\(' "$f"; then
      bad+=" no-splice"
    fi
    # Defensive cap (numeric or named — accept current naming variants)
    if ! grep -qE 'CHUNKS_CAP|CHUNK_CAP|chunksCap|chunkCap|=\s*10' "$f"; then
      bad+=" no-cap"
    fi
    # Belt-and-braces: confirm platform recording.ts files don't carry
    # a duplicate buffer (would mean Pack U.2/U.3 unification regressed).
    for plat in googlemeet msteams; do
      pf="$ROOT_DIR/services/vexa-bot/core/src/platforms/$plat/recording.ts"
      [ ! -f "$pf" ] && continue
      stripped=$(sed 's://.*$::' "$pf")
      if echo "$stripped" | grep -qE '__vexaRecordedChunks\s*=|VEXA_RECORDED_CHUNKS_CAP'; then
        bad+=" platform-regressed:$plat"
      fi
    done
    if [ -z "$bad" ]; then
      step_pass BOT_RECORDING_CHUNK_BUFFER_TRIMMED \
        "splice+cap in BrowserMediaRecorderPipeline (browser.ts); no per-platform regression"
    else
      step_fail BOT_RECORDING_CHUNK_BUFFER_TRIMMED "missing/regressed:$bad"
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
