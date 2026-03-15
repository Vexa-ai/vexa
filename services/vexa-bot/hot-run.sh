#!/bin/bash
# Run vexa-bot hot (no Docker) for fast iteration.
#
# Usage:
#   1. Start a meeting from dashboard as normal — bot-manager launches container
#   2. Grab config: ./hot-run.sh grab <container-name>
#   3. Stop the container: docker stop <container-name>
#   4. Run hot: ./hot-run.sh run
#   5. Edit TypeScript, Ctrl+C, run again — changes apply instantly
#
# Or manually:
#   ./hot-run.sh run '{"platform":"google_meet","meetingUrl":"https://meet.google.com/abc-defg-hij",...}'

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/.hot-bot-config.json"

case "${1:-}" in
  grab)
    # Grab BOT_CONFIG from a running container
    CONTAINER="${2:-$(docker ps --format '{{.Names}}' | grep vexa-bot | head -1)}"
    if [ -z "$CONTAINER" ]; then
      echo "No vexa-bot container found. Start one from dashboard first."
      exit 1
    fi
    echo "Grabbing BOT_CONFIG from $CONTAINER..."
    BOT_CONFIG=$(docker inspect "$CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^BOT_CONFIG=' | sed 's/^BOT_CONFIG=//')
    if [ -z "$BOT_CONFIG" ]; then
      echo "No BOT_CONFIG found in container env"
      exit 1
    fi
    echo "$BOT_CONFIG" | python3 -m json.tool > "$CONFIG_FILE"
    echo "Saved to $CONFIG_FILE"
    echo ""
    echo "Now stop the container and run hot:"
    echo "  docker stop $CONTAINER"
    echo "  ./hot-run.sh run"
    ;;

  run)
    if [ -n "${2:-}" ]; then
      # Config passed as argument
      export BOT_CONFIG="$2"
    elif [ -f "$CONFIG_FILE" ]; then
      # Read from saved config, compact to one line
      export BOT_CONFIG=$(python3 -c "import json; print(json.dumps(json.load(open('$CONFIG_FILE'))))")
    else
      echo "No config found. Run './hot-run.sh grab' first, or pass config as argument."
      exit 1
    fi

    # Override callback URLs for local dev (host network, not Docker)
    export BOT_CONFIG=$(python3 -c "
import json, sys
c = json.loads('$BOT_CONFIG')
# Fix URLs: bot-manager is on localhost, not Docker hostname
if 'botManagerCallbackUrl' in c:
    c['botManagerCallbackUrl'] = c['botManagerCallbackUrl'].replace('bot-manager:8080', 'localhost:8090')
if 'recordingUploadUrl' in c:
    c['recordingUploadUrl'] = c['recordingUploadUrl'].replace('bot-manager:8080', 'localhost:8090')
if 'redisUrl' in c:
    c['redisUrl'] = c['redisUrl'].replace('redis:6379', 'localhost:6399')
if not c.get('transcriptionServiceUrl'):
    c['transcriptionServiceUrl'] = 'http://localhost:8083/v1/audio/transcriptions'
print(json.dumps(c))
")

    echo "Running bot hot..."
    echo "Meeting: $(echo $BOT_CONFIG | python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("meetingUrl","?"))')"
    echo "Platform: $(echo $BOT_CONFIG | python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("platform","?"))')"
    echo ""
    cd "$SCRIPT_DIR/core"
    exec npx tsx src/docker.ts
    ;;

  config)
    # Show current config
    if [ -f "$CONFIG_FILE" ]; then
      cat "$CONFIG_FILE"
    else
      echo "No config saved. Run './hot-run.sh grab' first."
    fi
    ;;

  *)
    echo "Usage:"
    echo "  ./hot-run.sh grab [container-name]  — grab BOT_CONFIG from running container"
    echo "  ./hot-run.sh run [config-json]       — run bot hot with saved or provided config"
    echo "  ./hot-run.sh config                  — show saved config"
    ;;
esac
