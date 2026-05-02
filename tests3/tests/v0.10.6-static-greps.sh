#!/usr/bin/env bash
# v0.10.6 static-grep checks (Pack U — audio recording unification).
#
# Single-invocation multi-step pattern (matches containers.sh / webhooks.sh).
# Runs all step IDs in one matrix invocation; emits one JSON report at
# .state/reports/<mode>/v0.10.6-static-greps.json with all steps.
#
# Step IDs (bound to features/bot-lifecycle/dods.yaml +
# features/post-meeting-transcription/dods.yaml DoDs):
#   PLATFORM_RECORDING_TS_LINE_BUDGET
#   NO_PER_PLATFORM_MASTER_CONSTRUCTION
#   BOT_EXIT_CALLBACK_INVOKES_FINALIZER
#   FINALIZER_BEFORE_STATUS_FLIP
#   SHARED_AUDIO_PIPELINE_MODULE_EXISTS
#   GMEET_RECORDING_USES_SHARED_PIPELINE
#   TEAMS_RECORDING_USES_SHARED_PIPELINE
#   ZOOM_WEB_RECORDING_USES_SHARED_PIPELINE
#   ZOOM_WEB_UPLOADS_CHUNKS_PERIODICALLY
#   SERVER_SIDE_MASTER_FINALIZER_EXISTS
#   DASHBOARD_AUDIO_STREAMS_FROM_BUCKET
#
# These checks read source files only — no infra required. Run in any mode.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"

echo ""
echo "  v0.10.6-static-greps"
echo "  ──────────────────────────────────────────────"

test_begin v0.10.6-static-greps

# ── PLATFORM_RECORDING_TS_LINE_BUDGET ──────────────────────────────
# After Pack U unification, each platform recording.ts is bounded.
# Speaker detection + popup dismissal stays platform-specific.
declare -A budgets=(
  [googlemeet]=800
  [msteams]=1000
  ["zoom/web"]=200
)
budget_bad=""
for plat in googlemeet msteams "zoom/web"; do
  f="$ROOT_DIR/services/vexa-bot/core/src/platforms/$plat/recording.ts"
  if [ ! -f "$f" ]; then budget_bad+=" missing:$plat"; continue; fi
  loc=$(wc -l < "$f")
  budget=${budgets[$plat]}
  if [ "$loc" -gt "$budget" ]; then
    budget_bad+=" $plat=${loc}LOC>${budget}"
  fi
done
if [ -z "$budget_bad" ]; then
  step_pass PLATFORM_RECORDING_TS_LINE_BUDGET "all platform recording.ts within budget"
else
  step_fail PLATFORM_RECORDING_TS_LINE_BUDGET "over budget:$budget_bad"
fi

# ── NO_PER_PLATFORM_MASTER_CONSTRUCTION ────────────────────────────
# Bot-side master assembly is eliminated. We strip single-line `//`
# comments before grepping (deletion-rationale comments OK).
master_bad=""
for plat in googlemeet msteams "zoom/web"; do
  f="$ROOT_DIR/services/vexa-bot/core/src/platforms/$plat/recording.ts"
  if [ ! -f "$f" ]; then master_bad+=" missing:$plat"; continue; fi
  stripped=$(sed 's://.*$::' "$f")
  if echo "$stripped" | grep -q '__vexaSaveRecordingBlob'; then
    master_bad+=" save-blob:$plat"
  fi
  if echo "$stripped" | grep -q '__vexaRecordedChunks'; then
    master_bad+=" recorded-chunks:$plat"
  fi
done
if [ -z "$master_bad" ]; then
  step_pass NO_PER_PLATFORM_MASTER_CONSTRUCTION "no bot-side master construction in platform recording.ts"
else
  step_fail NO_PER_PLATFORM_MASTER_CONSTRUCTION "found in:$master_bad"
fi

# ── BOT_EXIT_CALLBACK_INVOKES_FINALIZER ────────────────────────────
# callbacks.py imports finalize_recording_master AND awaits it inside
# bot_exit_callback (3 sites: graceful, stopping, else).
cf="$ROOT_DIR/services/meeting-api/meeting_api/callbacks.py"
if [ ! -f "$cf" ]; then
  step_fail BOT_EXIT_CALLBACK_INVOKES_FINALIZER "callbacks.py missing"
elif ! grep -q 'from .recording_finalizer import' "$cf"; then
  step_fail BOT_EXIT_CALLBACK_INVOKES_FINALIZER "missing import: from .recording_finalizer import"
else
  cnt=$(grep -c 'await finalize_recording_master' "$cf")
  if [ "$cnt" -lt 3 ]; then
    step_fail BOT_EXIT_CALLBACK_INVOKES_FINALIZER "expected 3 await sites (graceful/stopping/else), found $cnt"
  else
    step_pass BOT_EXIT_CALLBACK_INVOKES_FINALIZER "import + 3 await sites present"
  fi
fi

# ── FINALIZER_BEFORE_STATUS_FLIP ───────────────────────────────────
# In bot_exit_callback's body, every `await finalize_recording_master`
# precedes the corresponding `await update_meeting_status`.
if [ -f "$cf" ]; then
  body=$(awk '
    /^async def bot_exit_callback/ { capture=1; print; next }
    capture && /^(async def |def )/ { exit }
    capture { print }
  ' "$cf")
  fin_lines=$(echo "$body" | grep -n 'await finalize_recording_master' | cut -d: -f1)
  upd_lines=$(echo "$body" | grep -n 'await update_meeting_status' | cut -d: -f1)
  if [ -z "$fin_lines" ] || [ -z "$upd_lines" ]; then
    step_fail FINALIZER_BEFORE_STATUS_FLIP "missing finalize or update_meeting_status calls in body"
  else
    order_bad=""
    fin_arr=($fin_lines); upd_arr=($upd_lines)
    for i in "${!fin_arr[@]}"; do
      f_line=${fin_arr[$i]}
      next_upd=""
      for u in "${upd_arr[@]}"; do
        if [ "$u" -gt "$f_line" ]; then next_upd=$u; break; fi
      done
      if [ -z "$next_upd" ]; then
        order_bad+=" finalize@${f_line}-no-following-update"
      fi
    done
    if [ "${#fin_arr[@]}" -ne "${#upd_arr[@]}" ]; then
      order_bad+=" count-mismatch:fin=${#fin_arr[@]}upd=${#upd_arr[@]}"
    fi
    if [ -z "$order_bad" ]; then
      step_pass FINALIZER_BEFORE_STATUS_FLIP "every finalize precedes its branch's status update"
    else
      step_fail FINALIZER_BEFORE_STATUS_FLIP "ordering violations:$order_bad"
    fi
  fi
fi

# ── SHARED_AUDIO_PIPELINE_MODULE_EXISTS ────────────────────────────
ap="$ROOT_DIR/services/vexa-bot/core/src/services/audio-pipeline.ts"
if [ ! -f "$ap" ]; then
  step_fail SHARED_AUDIO_PIPELINE_MODULE_EXISTS "audio-pipeline.ts missing"
elif ! grep -q 'class UnifiedRecordingPipeline' "$ap"; then
  step_fail SHARED_AUDIO_PIPELINE_MODULE_EXISTS "no UnifiedRecordingPipeline class in audio-pipeline.ts"
else
  step_pass SHARED_AUDIO_PIPELINE_MODULE_EXISTS "audio-pipeline.ts exports UnifiedRecordingPipeline + capture sources"
fi

# ── GMEET/TEAMS/ZOOM_WEB_RECORDING_USES_SHARED_PIPELINE ────────────
for plat in googlemeet msteams "zoom/web"; do
  f="$ROOT_DIR/services/vexa-bot/core/src/platforms/$plat/recording.ts"
  case "$plat" in
    googlemeet) check=GMEET_RECORDING_USES_SHARED_PIPELINE ;;
    msteams)    check=TEAMS_RECORDING_USES_SHARED_PIPELINE ;;
    zoom/web)   check=ZOOM_WEB_RECORDING_USES_SHARED_PIPELINE ;;
  esac
  if [ ! -f "$f" ]; then
    step_fail "$check" "$plat/recording.ts missing"
    continue
  fi
  if grep -qE 'from .*services/audio-pipeline' "$f"; then
    step_pass "$check" "imports from services/audio-pipeline"
  else
    step_fail "$check" "no audio-pipeline import in $plat/recording.ts"
  fi
done

# ── ZOOM_WEB_UPLOADS_CHUNKS_PERIODICALLY ───────────────────────────
# PulseAudioCapture in audio-pipeline.ts owns 15s WAV chunking for Zoom.
if [ -f "$ap" ] && grep -q 'class PulseAudioCapture' "$ap"; then
  step_pass ZOOM_WEB_UPLOADS_CHUNKS_PERIODICALLY "PulseAudioCapture class present in audio-pipeline.ts"
else
  step_fail ZOOM_WEB_UPLOADS_CHUNKS_PERIODICALLY "no PulseAudioCapture class in audio-pipeline.ts"
fi

# ── SERVER_SIDE_MASTER_FINALIZER_EXISTS ────────────────────────────
rf="$ROOT_DIR/services/meeting-api/meeting_api/recording_finalizer.py"
if [ ! -f "$rf" ]; then
  step_fail SERVER_SIDE_MASTER_FINALIZER_EXISTS "recording_finalizer.py missing"
elif ! grep -q 'async def finalize_recording_master' "$rf"; then
  step_fail SERVER_SIDE_MASTER_FINALIZER_EXISTS "no async finalize_recording_master in recording_finalizer.py"
else
  step_pass SERVER_SIDE_MASTER_FINALIZER_EXISTS "recording_finalizer.py exports finalize_recording_master"
fi

# ── DASHBOARD_AUDIO_STREAMS_FROM_BUCKET ────────────────────────────
# Dashboard reads /download (presigned URL) instead of /raw.
da="$ROOT_DIR/services/dashboard/src/lib/api.ts"
if [ ! -f "$da" ]; then
  step_fail DASHBOARD_AUDIO_STREAMS_FROM_BUCKET "dashboard api.ts missing"
elif grep -qE 'getRecordingAudioStreamUrl|recordings/.*/download' "$da"; then
  step_pass DASHBOARD_AUDIO_STREAMS_FROM_BUCKET "dashboard reads /download → presigned URL"
else
  step_fail DASHBOARD_AUDIO_STREAMS_FROM_BUCKET "dashboard api.ts has no /download or getRecordingAudioStreamUrl"
fi

# ── UNIFIED_ALIGNMENT_HOOK_IN_PIPELINE ─────────────────────────────
# Segment-to-audio alignment lives in ONE place — UnifiedRecordingPipeline.
# Verifies:
#   (1) audio-pipeline.ts UnifiedRecordingPipeline subscribes to
#       source.on('started') and calls publisher.resetSessionStart().
#   (2) NO per-platform recording.ts has its own
#       page.exposeFunction('__vexaRecordingStarted', ...) handler
#       with publisher.resetSessionStart inside. Browser-side trigger
#       calls (window.__vexaRecordingStarted?.()) are OK — they fire
#       UP to the shared exposed function in MediaRecorderCapture.
#   (3) NO per-platform recording.ts calls publisher.resetSessionStart()
#       at function entry (premature reset that pre-dates the unified hook).
ap="$ROOT_DIR/services/vexa-bot/core/src/services/audio-pipeline.ts"
align_bad=""
# (1) pipeline must own the hook
if [ ! -f "$ap" ] || ! grep -qE "this\.source\.on\(\"started\"" "$ap"; then
  align_bad+=" pipeline-no-started-hook"
fi
if [ -f "$ap" ] && ! grep -qE "publisher\.resetSessionStart" "$ap"; then
  align_bad+=" pipeline-no-resetSessionStart"
fi
# (2) + (3): platform recording.ts files must NOT own alignment.
# Strip // comments first (deletion-rationale comments are OK).
for plat in googlemeet msteams "zoom/web"; do
  pf="$ROOT_DIR/services/vexa-bot/core/src/platforms/$plat/recording.ts"
  [ ! -f "$pf" ] && continue
  stripped_pf=$(sed 's://.*$::' "$pf")
  # exposeFunction("__vexaRecordingStarted", ...) — forbidden in platform files
  # (MediaRecorderCapture in audio-pipeline.ts owns this).
  if echo "$stripped_pf" | grep -qE 'exposeFunction\([^)]*__vexaRecordingStarted'; then
    align_bad+=" $plat:exposeFunction-redundant"
  fi
  # publisher.resetSessionStart() in platform code — forbidden after unification.
  if echo "$stripped_pf" | grep -qE 'publisher\.resetSessionStart\(\)'; then
    align_bad+=" $plat:platform-side-reset"
  fi
done
if [ -z "$align_bad" ]; then
  step_pass UNIFIED_ALIGNMENT_HOOK_IN_PIPELINE \
    "alignment hook lives only in UnifiedRecordingPipeline; no per-platform handlers"
else
  step_fail UNIFIED_ALIGNMENT_HOOK_IN_PIPELINE "violations:$align_bad"
fi

# ── BROWSER_UTILS_INJECTED_BEFORE_PIPELINE_START ──────────────────
# Pack U.2/U.3 regression guard: ensureBrowserUtils() MUST run BEFORE
# pipeline.start() in every platform that uses MediaRecorderCapture.
# The pipeline's startBrowserCapture page.evaluate accesses
# window.VexaBrowserUtils which is set up by ensureBrowserUtils. Wrong
# ordering → undefined classes → silent throw → 0 chunks → meeting fails.
# Captured 2026-05-02 after 3 failed real-meeting tests across helm + lite.
order_bad=""
for plat in googlemeet msteams; do
  pf="$ROOT_DIR/services/vexa-bot/core/src/platforms/$plat/recording.ts"
  [ ! -f "$pf" ] && continue
  # Strip single-line `//` comments before grepping for ordering — comment
  # references to "pipeline.start()" must not count as call sites.
  stripped_pf=$(sed 's://.*$::' "$pf")
  ensure_line=$(echo "$stripped_pf" | grep -n 'ensureBrowserUtils(page' | head -1 | cut -d: -f1)
  # Match the OUTER pipeline.start() call (the Node-side
  # `await pipeline.start();` after `pipeline = new UnifiedRecordingPipeline(...)`).
  # The browser-side `await pipeline.start();` inside startBrowserCapture is
  # for the BrowserMediaRecorderPipeline — different object, on the page side.
  # Both should appear AFTER ensureBrowserUtils, so taking the first match
  # is sufficient as a guard.
  start_line=$(echo "$stripped_pf" | grep -n 'await pipeline\.start()' | head -1 | cut -d: -f1)
  if [ -z "$ensure_line" ]; then
    order_bad+=" $plat:no-ensureBrowserUtils"
    continue
  fi
  if [ -z "$start_line" ]; then
    order_bad+=" $plat:no-pipeline.start"
    continue
  fi
  if [ "$ensure_line" -gt "$start_line" ]; then
    order_bad+=" $plat:ensure-after-start(${ensure_line}>${start_line})"
  fi
done
if [ -z "$order_bad" ]; then
  step_pass BROWSER_UTILS_INJECTED_BEFORE_PIPELINE_START \
    "ensureBrowserUtils precedes pipeline.start() in every MediaRecorder platform"
else
  step_fail BROWSER_UTILS_INJECTED_BEFORE_PIPELINE_START "ordering violations:$order_bad"
fi

# ── DASHBOARD_MEETINGS_PAGINATION_TRACKS_UNFILTERED_OFFSET ────────
# GH #304 fix: meetings-store.ts paginates by explicit _offset cursor
# (advances by unfiltered API page size 50), NOT by post-filter
# meetings.length. Plus dedupe by meeting.id when merging pages.
ms="$ROOT_DIR/services/dashboard/src/stores/meetings-store.ts"
if [ ! -f "$ms" ]; then
  step_fail DASHBOARD_MEETINGS_PAGINATION_TRACKS_UNFILTERED_OFFSET "meetings-store.ts missing"
else
  pag_bad=""
  # Must declare _offset in state
  if ! grep -qE '_offset\s*:\s*number|_offset\s*:\s*0' "$ms"; then
    pag_bad+=" no-_offset-declared"
  fi
  # fetchMoreMeetings must read _offset from store (NOT meetings.length)
  if ! grep -qE 'offset:\s*_offset' "$ms"; then
    pag_bad+=" no-_offset-cursor-in-fetchMore"
  fi
  # Must NOT use offset: meetings.length (pre-fix shape) anywhere
  # (strip comments first — the // comment about NOT using it is OK).
  stripped_ms=$(sed 's://.*$::' "$ms")
  if echo "$stripped_ms" | grep -qE 'offset:\s*meetings\.length'; then
    pag_bad+=" old-offset-meetings.length-still-present"
  fi
  # Belt-and-suspenders dedupe: must check seen set / .has(m.id) on merge
  if ! grep -qE 'seen.*has|new Set\(meetings.*id|filter.*seen' "$ms"; then
    pag_bad+=" no-dedupe-on-merge"
  fi
  if [ -z "$pag_bad" ]; then
    step_pass DASHBOARD_MEETINGS_PAGINATION_TRACKS_UNFILTERED_OFFSET \
      "meetings-store.ts uses explicit _offset cursor + dedupe-by-meeting.id (closes #304 duplicate-rows class)"
  else
    step_fail DASHBOARD_MEETINGS_PAGINATION_TRACKS_UNFILTERED_OFFSET "missing/regressed:$pag_bad"
  fi
fi

echo ""
echo "  ──────────────────────────────────────────────"
test_end
