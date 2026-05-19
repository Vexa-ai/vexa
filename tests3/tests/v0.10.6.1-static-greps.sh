#!/usr/bin/env bash
# v0.10.6.1-static-greps — registry.yaml type:grep checks for v0.10.6.1
# scope items, dispatched as one multi-step run (same pattern as
# v0.10.6-static-greps.sh).
#
# This script is the runtime dispatcher for grep checks that were defined
# in tests3/registry.yaml as type:grep but had no executor wired before
# 2026-05-11 (the dispatch-surface repair, T-038). Each grep check is
# emitted as one step_pass / step_fail <CHECK_ID> entry.
#
# Step IDs (bound to tests3/registry.yaml + scope.yaml proves[] entries):
#   BOT_CONFIG_CAMERA_ENABLED_INDEPENDENT_OF_VOICE_AGENT
#   BOT_TEAMS_JOIN_CAMERA_GATED_BY_CAMERA_ENABLED
#   BOT_SPEAK_HONORS_PROVIDER_PARAM
#   CALLBACKS_FINALIZE_NARROW_EXCEPT
#   CHUNK_WRITE_PRIOR_COUNT_LOG_ACCURATE
#   RUNTIME_API_DELETE_EMITS_EXIT_CALLBACK
#   SINGLE_WRITER_FOR_RECORDING_MASTER_PATH
#   STUCK_MEETING_SWEEP_USES_PROGRESS_TIMESTAMP
#   VEXA_LITE_APPLE_SILICON_CAVEAT_DOCUMENTED
#   SWAGGER_CURL_EXAMPLE_SHOWS_CORRECT_HEADER  (statically check the
#                                              setting in admin-api/app/main.py
#                                              rather than hitting live admin)
#
# No infra required. Pure file-content greps; runs in any mode.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"

echo ""
echo "  v0.10.6.1-static-greps"
echo "  ──────────────────────────────────────────────"

test_begin v0.10.6.1-static-greps

# ─── helper: grep_check <CHECK_ID> <relative_file> <must_match_pattern> ──
# Uses fixed-string match if pattern contains no regex metacharacters,
# otherwise extended grep. Reports FAIL if file missing OR pattern not found.
grep_check() {
  local check_id="$1" relpath="$2" pat="$3"
  local f="$ROOT_DIR/$relpath"
  if [ ! -f "$f" ]; then
    step_fail "$check_id" "file missing: $relpath"
    return
  fi
  if grep -qE "$pat" "$f"; then
    step_pass "$check_id" "$relpath matched"
  else
    step_fail "$check_id" "$relpath did not match pattern: $pat"
  fi
}

# ─── helper: grep_combo_check <CHECK_ID> <relpath> <must_match> <must_not_match> ──
# PASS only when the positive guard is present and the stale/forbidden guard is
# absent. This catches "added the new flag but left the old coupling too".
grep_combo_check() {
  local check_id="$1" relpath="$2" must_match="$3" must_not_match="$4"
  local f="$ROOT_DIR/$relpath"
  if [ ! -f "$f" ]; then
    step_fail "$check_id" "file missing: $relpath"
    return
  fi
  if grep -qE "$must_not_match" "$f"; then
    step_fail "$check_id" "$relpath still matches forbidden pattern: $must_not_match"
    return
  fi
  if grep -qE "$must_match" "$f"; then
    step_pass "$check_id" "$relpath positive/negative guard matched"
  else
    step_fail "$check_id" "$relpath did not match required pattern: $must_match"
  fi
}

# ─── BOT_CONFIG_CAMERA_ENABLED_INDEPENDENT_OF_VOICE_AGENT ───────────────
# Replaces the earlier BOT_CONFIG_CAMERA_ENABLED_WHEN_VOICE_AGENT (PR #239)
# which encoded the inverse, incorrect coupling. camera_enabled is its own
# request field, defaults off, and is honored only when the operator opts in.
grep_combo_check BOT_CONFIG_CAMERA_ENABLED_INDEPENDENT_OF_VOICE_AGENT \
  "services/meeting-api/meeting_api/meetings.py" \
  'if req\.camera_enabled is not None:' \
  'bot_config\["cameraEnabled"\] = bool\(req\.voice_agent_enabled\)'

# ─── BOT_TEAMS_JOIN_CAMERA_GATED_BY_CAMERA_ENABLED ─────────────────────
# Teams pre-join must not turn camera on just because voice-agent / speak is
# enabled. The only valid gate for outgoing avatar/video is cameraEnabled.
grep_combo_check BOT_TEAMS_JOIN_CAMERA_GATED_BY_CAMERA_ENABLED \
  "services/vexa-bot/core/src/platforms/msteams/join.ts" \
  'if \(botConfig\.cameraEnabled\) \{' \
  'if \(botConfig\.voiceAgentEnabled\) \{'

# ─── BOT_SPEAK_HONORS_PROVIDER_PARAM ────────────────────────────────────
# Provider-param plumbing lives in tts-playback.ts (synthesizeViaTtsService).
# speak.ts never existed — the original prove-path was wrong; the bot's TTS
# request body assembly is in tts-playback.ts:319 (provider default) and :350
# (provider passed to /v1/audio/speech body). T-038 triage 2026-05-11.
grep_check BOT_SPEAK_HONORS_PROVIDER_PARAM \
  "services/vexa-bot/core/src/services/tts-playback.ts" \
  'provider'

# ─── CALLBACKS_FINALIZE_NARROW_EXCEPT ───────────────────────────────────
# Re-raise CancelledError + MemoryError, not bare except Exception.
grep_check CALLBACKS_FINALIZE_NARROW_EXCEPT \
  "services/meeting-api/meeting_api/callbacks.py" \
  'asyncio\.CancelledError, MemoryError'

# ─── CHUNK_WRITE_PRIOR_COUNT_LOG_ACCURATE ───────────────────────────────
grep_check CHUNK_WRITE_PRIOR_COUNT_LOG_ACCURATE \
  "services/meeting-api/meeting_api/recordings.py" \
  'prior_chunks=%s'

# ─── RUNTIME_API_DELETE_EMITS_EXIT_CALLBACK ─────────────────────────────
# meeting-api plumbs connection_id='bs:<meeting_id>' for browser_session
# dispatches; runtime-api recognizes the bs: prefix.
grep_check RUNTIME_API_DELETE_EMITS_EXIT_CALLBACK \
  "services/meeting-api/meeting_api/meetings.py" \
  'connection_id.*bs:'

# ─── SINGLE_WRITER_FOR_RECORDING_MASTER_PATH ────────────────────────────
# post_meeting observes master entries; only recording_finalizer writes them.
grep_check SINGLE_WRITER_FOR_RECORDING_MASTER_PATH \
  "services/meeting-api/meeting_api/post_meeting.py" \
  'sp\.endswith\("/audio/master\.webm"\)'

# ─── STUCK_MEETING_SWEEP_USES_PROGRESS_TIMESTAMP ────────────────────────
grep_check STUCK_MEETING_SWEEP_USES_PROGRESS_TIMESTAMP \
  "services/meeting-api/meeting_api/sweeps.py" \
  'last_progress_at'

# ─── VEXA_LITE_APPLE_SILICON_CAVEAT_DOCUMENTED ──────────────────────────
grep_check VEXA_LITE_APPLE_SILICON_CAVEAT_DOCUMENTED \
  "docs/vexa-lite-deployment.mdx" \
  'Apple Silicon'

# ─── SWAGGER_CURL_EXAMPLE_SHOWS_CORRECT_HEADER ─────────────────────────
# Static guard: AdminApiKey scheme with X-Admin-API-Key header name set
# on the APIKeyHeader. (The runtime HTTP check in registry.yaml is also
# valid but requires a live admin URL; the static guard is cheaper +
# always available.)
grep_check SWAGGER_CURL_EXAMPLE_SHOWS_CORRECT_HEADER \
  "services/admin-api/app/main.py" \
  'name="X-Admin-API-Key".*scheme_name="AdminApiKey"'
