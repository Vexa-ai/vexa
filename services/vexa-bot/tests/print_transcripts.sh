#!/bin/bash
#
# Print deduplicated transcripts from Redis.
# Speaker | text
#
# Usage:
#   bash tests/print_transcripts.sh              # all transcripts
#   bash tests/print_transcripts.sh --last N     # last N entries
#   bash tests/print_transcripts.sh --follow     # poll for new
#   bash tests/print_transcripts.sh --clear      # clear stream + follow
#

MODE="${1:---all}"
CONTAINER="vexa_dev-redis-1"
STREAM="transcription_segments"

if ! docker ps --format '{{.Names}}' | grep -q "$CONTAINER" 2>/dev/null; then
  CONTAINER="tests-redis-1"
fi

rcli() {
  docker exec "$CONTAINER" redis-cli "$@" 2>/dev/null
}

dedup() {
  awk '
    /^[0-9]+-[0-9]+$/ { id=$0; next }
    $0 == "speaker" { getline; speaker=$0; next }
    $0 == "text" { getline; text=$0;
      if (text != "" && (speaker != last_speaker || text != last_text)) {
        printf "%s | %s\n", speaker, text
        last_speaker = speaker
        last_text = text
      }
      next
    }
  '
}

case "$MODE" in
  --clear)
    rcli DEL "$STREAM" > /dev/null
    echo "Cleared."
    exec "$0" --follow
    ;;
  --last)
    N="${2:-10}"
    rcli XREVRANGE "$STREAM" + - COUNT "$N" | dedup | tac
    ;;
  --follow)
    echo "Following... (Ctrl+C to stop)"
    LAST_ID=$(rcli XREVRANGE "$STREAM" + - COUNT 1 | head -1)
    LAST_ID="${LAST_ID:-\$}"
    while true; do
      rcli XRANGE "$STREAM" "($LAST_ID" + | dedup
      NEW_ID=$(rcli XREVRANGE "$STREAM" + - COUNT 1 | head -1)
      if [ -n "$NEW_ID" ]; then LAST_ID="$NEW_ID"; fi
      sleep 1
    done
    ;;
  *)
    rcli XRANGE "$STREAM" - + | dedup
    ;;
esac
