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
for plat in googlemeet msteams; do
    f="$ROOT_DIR/services/vexa-bot/core/src/platforms/$plat/recording.ts"
    [ -f "$f" ] || { bad+=" missing:$plat"; continue; }

    # Ordered-pair check: the MediaRecorder.start() call receives a timeslice
    # that is at least 15000 ms — captures 15000, 30000, etc. Permissive so
    # future tuning doesn't break the guard.
    if ! grep -qE 'recorder\.start\s*\(\s*(1[5-9][0-9]{3}|[2-9][0-9]{4,})\s*\)' "$f"; then
        bad+=" $plat:timeslice"
    fi
    if ! grep -q '__vexaSaveRecordingChunk' "$f"; then
        bad+=" $plat:chunk-sink"
    fi
done

if [ -z "$bad" ]; then
    step_pass bot_records_incrementally "bot recording.ts wires ≥15s MediaRecorder timeslice + __vexaSaveRecordingChunk"
else
    step_fail bot_records_incrementally "incremental-upload contract missing:$bad"
fi

echo "  ──────────────────────────────────────────────"
echo ""
