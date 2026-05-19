#!/usr/bin/env bash
# deferred-transcribe-master-gate — deferred transcription must wait for
# recording_finalizer.master; chunk paths are not valid transcription input.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$ROOT_DIR/tests3/lib/common.sh"

test_begin "deferred-transcribe-master-gate"

meetings_py="$ROOT_DIR/services/meeting-api/meeting_api/meetings.py"
auto_py="$ROOT_DIR/tests3/tests/autonomous_real_meeting.py"
runtime_api_py="$ROOT_DIR/services/runtime-api/runtime_api/api.py"
runtime_lifecycle_py="$ROOT_DIR/services/runtime-api/runtime_api/lifecycle.py"
compose_yml="$ROOT_DIR/deploy/compose/docker-compose.yml"
runtime_profiles="$ROOT_DIR/services/runtime-api/profiles.yaml"
bot_callback_ts="$ROOT_DIR/services/vexa-bot/core/src/services/unified-callback.ts"

if grep -q "def _find_finalized_audio_master" "$meetings_py" &&
   grep -q "recording_finalizer.master" "$meetings_py" &&
   grep -q "Recording finalization is still in progress" "$meetings_py" &&
   ! grep -q 'mf.get("type") in ("audio", "video") and mf.get("storage_path")' "$meetings_py"; then
  step_pass DEFERRED_TRANSCRIBE_WAITS_FOR_RECORDING_MASTER \
    "POST /meetings/{id}/transcribe only accepts finalized audio masters"
else
  step_fail DEFERRED_TRANSCRIBE_WAITS_FOR_RECORDING_MASTER \
    "deferred transcription can still consume a raw storage_path before recording_finalizer.master"
fi

if grep -q "callback_headers" "$runtime_api_py" &&
   grep -q "callback_headers" "$runtime_lifecycle_py" &&
   grep -q "headers=headers" "$runtime_lifecycle_py" &&
   grep -q 'callback_headers={"X-Internal-Secret": os.getenv("INTERNAL_API_SECRET", "")}' "$meetings_py"; then
  step_pass RUNTIME_EXIT_CALLBACK_SENDS_INTERNAL_SECRET \
    "runtime-api persists callback headers and meeting-api sends X-Internal-Secret for exit callbacks"
else
  step_fail RUNTIME_EXIT_CALLBACK_SENDS_INTERNAL_SECRET \
    "runtime exit callback can reach meeting-api without X-Internal-Secret"
fi

if grep -q "process.env.INTERNAL_API_SECRET" "$bot_callback_ts" &&
   grep -q '"INTERNAL_API_SECRET": os.getenv("INTERNAL_API_SECRET", "")' "$meetings_py" &&
   grep -q 'INTERNAL_API_SECRET=${INTERNAL_API_SECRET:-vexa-internal-secret}' "$compose_yml" &&
   grep -q 'INTERNAL_API_SECRET: "${INTERNAL_API_SECRET}"' "$runtime_profiles"; then
  step_pass BOT_STATUS_CALLBACK_HAS_INTERNAL_SECRET_FALLBACK \
    "bot status callbacks have one internal secret source wired through meeting-api, compose runtime-api, and runtime profiles"
else
  step_fail BOT_STATUS_CALLBACK_HAS_INTERNAL_SECRET_FALLBACK \
    "bot status callbacks can lose X-Internal-Secret and stall before active"
fi

if grep -q "deferred-transcribe-after-crash" "$auto_py" &&
   grep -q "DEFERRED_TRANSCRIBE_USES_MASTER" "$auto_py" &&
   grep -q "DEFERRED_TRANSCRIPT_PERSISTED_AFTER_CRASH" "$auto_py" &&
   grep -q "transcribe_enabled" "$auto_py"; then
  step_pass DISRUPTIVE_DEFERRED_TRANSCRIPTION_HARNESS_EXISTS \
    "autonomous real-meeting crash harness can run recording-only bot, SIGKILL it, then prove deferred transcript persistence"
else
  step_fail DISRUPTIVE_DEFERRED_TRANSCRIPTION_HARNESS_EXISTS \
    "autonomous real-meeting harness lacks crash -> finalized master -> deferred transcript persistence flow"
fi

if grep -q "bot_container_id" "$auto_py" &&
   grep -q "docker kill -s KILL {container_id}" "$auto_py" &&
   grep -q "latest.get(\"bot_container_id\")" "$auto_py"; then
  step_pass DISRUPTIVE_CRASH_KILLS_CANONICAL_BOT_CONTAINER \
    "compose crash harness SIGKILLs the meeting-api bot_container_id before falling back to env/name discovery"
else
  step_fail DISRUPTIVE_CRASH_KILLS_CANONICAL_BOT_CONTAINER \
    "compose crash harness can miss the running bot by using non-canonical container discovery"
fi

test_end
