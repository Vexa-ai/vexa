#!/usr/bin/env bash
# v0.10.6.1-multichunk-playback-url — pins the m314 multi-chunk playback
# contract: after finalize, the recording's JSONB carries a stable
# playback_url.{audio,video} pointing at /recordings/<id>/master?type=…
# which the dashboard reads directly (no client-side master-picking).
#
# Steps:
#   finalizer_writes_playback_url  Static (recording_finalizer.py):
#                                  finalize_recording_master constructs
#                                  rec_payload["playback_url"] = {audio, video}
#                                  with stable route URLs after master
#                                  assembly succeeds for either media type.
#   master_endpoint_present        Static (api-gateway main.py +
#                                  meeting_api/recordings.py): the
#                                  /recordings/{id}/master endpoint exists
#                                  and accepts ?type=audio|video so the
#                                  playback_url field resolves on fetch.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
FIN="$ROOT_DIR/services/meeting-api/meeting_api/recording_finalizer.py"
RECS="$ROOT_DIR/services/meeting-api/meeting_api/recordings.py"
GATEWAY="$ROOT_DIR/services/api-gateway/main.py"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-multichunk-playback-url :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-multichunk-playback-url-$step"

case "$step" in
  finalizer_writes_playback_url)
    failed=0

    if [ ! -f "$FIN" ]; then
      echo "    FAIL: recording_finalizer.py missing"
      step_fail "FINALIZER_PRESENT" "file not found"
      failed=1
    else
      step_pass "FINALIZER_PRESENT" "recording_finalizer.py present"

      # The function exists.
      if ! grep -qE 'def\s+finalize_recording_master' "$FIN"; then
        echo "    FAIL: finalize_recording_master not declared"
        step_fail "FINALIZE_FN" "function declaration missing"
        failed=1
      else
        step_pass "FINALIZE_FN" "finalize_recording_master declared"
      fi

      # rec_payload["playback_url"] = { ... } is written.
      if ! grep -qE 'rec_payload\[\s*"playback_url"\s*\]\s*=\s*\{' "$FIN"; then
        echo "    FAIL: finalizer does not assign rec_payload[\"playback_url\"]"
        step_fail "PLAYBACK_URL_WRITTEN" "playback_url assignment missing"
        failed=1
      else
        step_pass "PLAYBACK_URL_WRITTEN" "finalizer writes rec_payload[\"playback_url\"]"
      fi

      # Both audio and video keys are emitted (None when absent).
      for kind in audio video; do
        if ! grep -qE "\"$kind\"\s*:\s*f\"/recordings/\{recording_id\}/master\?type=$kind\"" "$FIN"; then
          echo "    FAIL: finalizer missing $kind playback URL pattern"
          step_fail "PLAYBACK_${kind^^}_PATTERN" "URL pattern not matched"
          failed=1
        else
          step_pass "PLAYBACK_${kind^^}_PATTERN" "stable $kind URL pattern present"
        fi
      done
    fi

    if [ "$failed" -eq 1 ]; then
      test_end; exit 1
    fi
    ;;

  master_endpoint_present)
    failed=0

    if [ ! -f "$GATEWAY" ]; then
      echo "    FAIL: api-gateway main.py missing"
      step_fail "GATEWAY_PRESENT" "file not found"
      failed=1
    else
      # /recordings/{recording_id}/master proxied by api-gateway
      if ! grep -qE '@app\.get\("/recordings/\{recording_id\}/master"' "$GATEWAY"; then
        echo "    FAIL: api-gateway does not expose /recordings/{id}/master"
        step_fail "GATEWAY_MASTER_ROUTE" "route declaration missing"
        failed=1
      else
        step_pass "GATEWAY_MASTER_ROUTE" "/recordings/{id}/master exposed via gateway"
      fi
    fi

    if [ ! -f "$RECS" ]; then
      echo "    FAIL: recordings.py missing"
      step_fail "RECORDINGS_PRESENT" "file not found"
      failed=1
    else
      # The implementation accepts a `type` query param (audio|video).
      if ! grep -qE 'type:\s*str\s*=\s*Query\(' "$RECS"; then
        echo "    FAIL: master endpoint does not declare type query param"
        step_fail "MASTER_TYPE_PARAM" "type Query() declaration missing"
        failed=1
      else
        step_pass "MASTER_TYPE_PARAM" "master endpoint accepts type=audio|video"
      fi
    fi

    if [ "$failed" -eq 1 ]; then
      test_end; exit 1
    fi
    ;;

  *)
    echo "    FAIL: unknown step '$step'"
    test_end
    exit 1
    ;;
esac

test_end
echo ""
echo "  PASS"
