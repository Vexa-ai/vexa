#!/usr/bin/env bash
# v0.10.6 static-grep checks (Pack U — audio recording unification).
#
# These checks run in any mode (lite/compose/helm); they read source files
# only, no service deployed required. Each step corresponds to a registry
# check ID. See tests3/registry.yaml for the bound entries.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6-static-greps :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6-static-greps-$step"

case "$step" in

  platform_recording_line_budget)
    # PLATFORM_RECORDING_TS_LINE_BUDGET: after Pack U unification, each
    # platform's recording.ts is bounded. Speaker-detection + popup-
    # dismissal + DOM glue is platform-specific and stays; everything
    # else moved to services/audio-pipeline.ts. Budget set generously to
    # accommodate Teams' larger speaker-detection block.
    # Budgets reflect platform-specific code that must remain (DOM speaker
    # detection, popup dismissal, selector-driven observability). Capture
    # logic moved to services/audio-pipeline.ts (~600 LOC there). Budgets
    # tuned to current shape with ~10% headroom for legitimate growth.
    declare -A budgets=(
      [googlemeet]=800
      [msteams]=1000
      ["zoom/web"]=200
    )
    bad=""
    for plat in googlemeet msteams "zoom/web"; do
      f="$ROOT_DIR/services/vexa-bot/core/src/platforms/$plat/recording.ts"
      if [ ! -f "$f" ]; then bad+=" missing:$plat"; continue; fi
      loc=$(wc -l < "$f")
      budget=${budgets[$plat]}
      if [ "$loc" -gt "$budget" ]; then
        bad+=" $plat=${loc}LOC>${budget}"
      fi
    done
    if [ -z "$bad" ]; then
      step_pass PLATFORM_RECORDING_TS_LINE_BUDGET "all platform recording.ts within budget"
    else
      step_fail PLATFORM_RECORDING_TS_LINE_BUDGET "over budget:$bad"
      exit 1
    fi
    ;;

  no_bot_master_construction)
    # NO_PER_PLATFORM_MASTER_CONSTRUCTION: bot-side master assembly is
    # eliminated. Master is built exclusively by recording_finalizer.py
    # at bot_exit_callback. The browser-side `__vexaSaveRecordingBlob`
    # exposed function and `__vexaRecordedChunks` master-blob construction
    # MUST NOT appear in any platform recording.ts CODE.
    #
    # We ignore single-line `//` comments (which legitimately reference the
    # removed primitive in deletion-rationale comments) but flag any code
    # use — function calls, variable assignments, type references.
    bad=""
    for plat in googlemeet msteams "zoom/web"; do
      f="$ROOT_DIR/services/vexa-bot/core/src/platforms/$plat/recording.ts"
      if [ ! -f "$f" ]; then bad+=" missing:$plat"; continue; fi
      # Strip single-line // comments before grepping. Multi-line /* */
      # comments are rare here; if they crop up we'll add stripping.
      stripped=$(sed 's://.*$::' "$f")
      if echo "$stripped" | grep -q '__vexaSaveRecordingBlob'; then
        bad+=" save-blob:$plat"
      fi
      if echo "$stripped" | grep -q '__vexaRecordedChunks'; then
        bad+=" recorded-chunks:$plat"
      fi
    done
    if [ -z "$bad" ]; then
      step_pass NO_PER_PLATFORM_MASTER_CONSTRUCTION "no bot-side master construction in platform recording.ts (comments OK)"
    else
      step_fail NO_PER_PLATFORM_MASTER_CONSTRUCTION "found in:$bad"
      exit 1
    fi
    ;;

  finalizer_invoked_in_exit_callback)
    # BOT_EXIT_CALLBACK_INVOKES_FINALIZER: callbacks.py imports
    # finalize_recording_master AND awaits it inside bot_exit_callback.
    f="$ROOT_DIR/services/meeting-api/meeting_api/callbacks.py"
    if [ ! -f "$f" ]; then
      step_fail BOT_EXIT_CALLBACK_INVOKES_FINALIZER "callbacks.py missing"
      exit 1
    fi
    # Import line
    if ! grep -q 'from .recording_finalizer import' "$f"; then
      step_fail BOT_EXIT_CALLBACK_INVOKES_FINALIZER "missing import: from .recording_finalizer import"
      exit 1
    fi
    # Call site count — should be 3 (graceful, stopping, else branches in
    # bot_exit_callback). If it's 0 or 1, hook is missing or partial.
    cnt=$(grep -c 'await finalize_recording_master' "$f")
    if [ "$cnt" -lt 3 ]; then
      step_fail BOT_EXIT_CALLBACK_INVOKES_FINALIZER "expected 3 await sites (graceful/stopping/else), found $cnt"
      exit 1
    fi
    step_pass BOT_EXIT_CALLBACK_INVOKES_FINALIZER "import + 3 await sites present"
    ;;

  finalizer_before_status)
    # FINALIZER_BEFORE_STATUS_FLIP: in bot_exit_callback, every textual
    # `await finalize_recording_master` line must appear BEFORE the next
    # `await update_meeting_status` line in the same branch. Race-window
    # check — if status flips first, /transcribe + dashboard playback can
    # read a stale storage_path.
    f="$ROOT_DIR/services/meeting-api/meeting_api/callbacks.py"
    if [ ! -f "$f" ]; then
      step_fail FINALIZER_BEFORE_STATUS_FLIP "callbacks.py missing"
      exit 1
    fi
    # Extract the bot_exit_callback function body (heuristic: from the
    # def line to the next top-level `async def` or `def` at column 0).
    body=$(awk '
      /^async def bot_exit_callback/ { capture=1; print; next }
      capture && /^(async def |def )/ { exit }
      capture { print }
    ' "$f")
    # For each `await finalize_recording_master` line, find its line
    # number; for each `await update_meeting_status` line, find its line
    # number. Pair them by branch (proximity): for each finalize line N,
    # the next update_meeting_status line in body must be > N.
    fin_lines=$(echo "$body" | grep -n 'await finalize_recording_master' | cut -d: -f1)
    upd_lines=$(echo "$body" | grep -n 'await update_meeting_status' | cut -d: -f1)
    if [ -z "$fin_lines" ] || [ -z "$upd_lines" ]; then
      step_fail FINALIZER_BEFORE_STATUS_FLIP "missing finalize or update_meeting_status calls in body"
      exit 1
    fi
    # Pair: each finalize must precede an update_meeting_status that's
    # closer (next one) than the next finalize.
    bad=""
    fin_arr=($fin_lines)
    upd_arr=($upd_lines)
    for i in "${!fin_arr[@]}"; do
      f_line=${fin_arr[$i]}
      # Find the smallest update_meeting_status line that's > f_line.
      next_upd=""
      for u in "${upd_arr[@]}"; do
        if [ "$u" -gt "$f_line" ]; then next_upd=$u; break; fi
      done
      if [ -z "$next_upd" ]; then
        bad+=" finalize@${f_line}-no-following-update"
      fi
    done
    # Also: count must match (3 each).
    if [ "${#fin_arr[@]}" -ne "${#upd_arr[@]}" ]; then
      bad+=" count-mismatch:fin=${#fin_arr[@]}upd=${#upd_arr[@]}"
    fi
    if [ -z "$bad" ]; then
      step_pass FINALIZER_BEFORE_STATUS_FLIP "every finalize precedes its branch's status update"
    else
      step_fail FINALIZER_BEFORE_STATUS_FLIP "ordering violations:$bad"
      exit 1
    fi
    ;;

  *)
    echo "Unknown step: $step"
    echo "Valid steps: platform_recording_line_budget no_bot_master_construction finalizer_invoked_in_exit_callback finalizer_before_status"
    exit 2
    ;;
esac

test_end
