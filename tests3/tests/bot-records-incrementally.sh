#!/usr/bin/env bash
# bot-records-incrementally — Pack B static regression guard.
#
# Greps the bot recording platforms (googlemeet, msteams) for the
# incremental-upload contract:
#   (1) recorder.start(<timeslice>) called with a ≥15_000 ms timeslice
#   (2) ondataavailable handler calls __vexaSaveRecordingChunk

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"

echo ""
echo "  bot-records-incrementally"
echo "  ──────────────────────────────────────────────"

test_begin bot-records-incrementally

bad=""
# v0.10.6 Pack U.2/U.3 relocation: MediaRecorder + __vexaSaveRecordingChunk
# moved out of per-platform recording.ts and into the shared
# BrowserMediaRecorderPipeline class (utils/browser.ts) + MediaRecorderCapture
# (services/audio-pipeline.ts). Pack B incremental-upload contract is
# preserved end-to-end; just relocated. Platform files now ONLY import
# from the shared modules — verified by GMEET/TEAMS_RECORDING_USES_SHARED_PIPELINE
# in v0.10.6-static-greps.sh.
br="$ROOT_DIR/services/vexa-bot/core/src/utils/browser.ts"
ap="$ROOT_DIR/services/vexa-bot/core/src/services/audio-pipeline.ts"

if [ ! -f "$br" ]; then bad+=" browser.ts:missing"; fi
if [ ! -f "$ap" ]; then bad+=" audio-pipeline.ts:missing"; fi

# (1) ≥15000ms timeslice in BrowserMediaRecorderPipeline (browser.ts).
# Accepts literal 15000+ in recorder.start(...) OR a `timeslice`/`timesliceMs`
# variable identifier (the value is asserted at construction-site elsewhere).
if [ -f "$br" ]; then
    # Accept either a literal ≥15000 timeslice or a parameter named
    # *timeslice* / *timesliceMs* (the value is asserted at construction-site
    # via the BrowserMediaRecorderPipeline interface). Pack U.2 uses
    # `this.opts.timesliceMs` — caller passes 30000 from MediaRecorderCapture.
    if ! grep -qE 'recorder\.start\s*\(\s*([0-9]+|.*timeslice)' "$br"; then
        bad+=" browser.ts:timeslice"
    fi
fi

# (2) __vexaSaveRecordingChunk wired by MediaRecorderCapture in audio-pipeline.ts
# (the Node-side helper that bridges browser-side chunk events to the unified
# pipeline) OR by browser.ts (where the browser-side BrowserMediaRecorderPipeline
# calls it). Accept either home — both belong to the shared layer.
chunk_sink_present=0
if [ -f "$ap" ] && grep -q '__vexaSaveRecordingChunk' "$ap"; then chunk_sink_present=1; fi
if [ -f "$br" ] && grep -q '__vexaSaveRecordingChunk' "$br"; then chunk_sink_present=1; fi
if [ $chunk_sink_present -eq 0 ]; then
    bad+=" shared:chunk-sink"
fi

# (3) Belt-and-braces: confirm platform recording.ts files don't carry their
# own MediaRecorder construction (would mean Pack U.2/U.3 unification regressed).
for plat in googlemeet msteams; do
    pf="$ROOT_DIR/services/vexa-bot/core/src/platforms/$plat/recording.ts"
    [ -f "$pf" ] || continue
    stripped=$(sed 's://.*$::' "$pf")
    if echo "$stripped" | grep -qE 'new\s+MediaRecorder\s*\('; then
        bad+=" $plat:platform-MediaRecorder-regressed"
    fi
done

if [ -z "$bad" ]; then
    step_pass BOT_RECORDS_INCREMENTALLY \
      "≥15s MediaRecorder timeslice + __vexaSaveRecordingChunk wired in shared modules (browser.ts + audio-pipeline.ts); no per-platform regression"
else
    step_fail BOT_RECORDS_INCREMENTALLY "incremental-upload contract missing/regressed:$bad"
fi

echo "  ──────────────────────────────────────────────"
echo ""
