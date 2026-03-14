#!/bin/bash
#
# Print deduplicated transcripts from Redis.
# Only: speaker | transcript text
#
# Usage:
#   bash tests/print_transcripts.sh              # print all
#   bash tests/print_transcripts.sh --follow     # follow live
#   bash tests/print_transcripts.sh --clear      # clear stream then follow
#

MODE="${1:---all}"
CONTAINER="vexa_dev-redis-1"
STREAM="transcription_segments"

# Check container exists
if ! docker ps --format '{{.Names}}' | grep -q "$CONTAINER"; then
  CONTAINER="tests-redis-1"
  if ! docker ps --format '{{.Names}}' | grep -q "$CONTAINER"; then
    echo "No Redis container found (tried vexa_dev-redis-1, tests-redis-1)"
    exit 1
  fi
fi

rcli() {
  docker exec "$CONTAINER" redis-cli "$@" 2>/dev/null
}

if [ "$MODE" = "--clear" ]; then
  rcli DEL "$STREAM" > /dev/null
  echo "Stream cleared. Following..."
  MODE="--follow"
fi

if [ "$MODE" = "--follow" ]; then
  echo "Following transcripts... (Ctrl+C to stop)"
  echo ""
  LAST_ID='$'
  LAST_SPEAKER=""
  LAST_TEXT=""
  while true; do
    RESULT=$(rcli XREAD BLOCK 2000 COUNT 10 STREAMS "$STREAM" "$LAST_ID" 2>/dev/null)
    if [ -z "$RESULT" ]; then continue; fi

    # Parse XREAD output — extract speaker and text fields
    echo "$RESULT" | while IFS= read -r line; do
      if echo "$line" | grep -qP '^\d+-\d+$'; then
        LAST_ID="$line"
      fi
    done

    # Use a simpler approach — get latest entries
    NEW_ENTRIES=$(rcli XRANGE "$STREAM" "$LAST_ID" + 2>/dev/null)
    if [ -z "$NEW_ENTRIES" ]; then continue; fi

    # Parse with awk
    echo "$NEW_ENTRIES" | awk '
      /^[0-9]+-[0-9]+$/ { id=$0; next }
      $0 == "speaker" { getline; speaker=$0; next }
      $0 == "text" { getline; text=$0;
        if (speaker != last_speaker || text != last_text) {
          printf "%s | %s\n", speaker, text
          last_speaker = speaker
          last_text = text
        }
        next
      }
    '

    sleep 1
  done
else
  # Print all — deduplicated
  rcli XRANGE "$STREAM" - + | awk '
    /^[0-9]+-[0-9]+$/ { next }
    $0 == "speaker" { getline; speaker=$0; next }
    $0 == "text" { getline; text=$0;
      if (speaker != last_speaker || text != last_text) {
        printf "%s | %s\n", speaker, text
        last_speaker = speaker
        last_text = text
      }
      next
    }
  '
fi
