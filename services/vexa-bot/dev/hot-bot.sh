#!/usr/bin/env bash
# Hot-dev direct-run for vexa-bot — pack-msteams-diarization-cutover (#394).
#
# Spawns ONE vexa-bot container directly via `docker run` with host source
# bind-mounted (BOT_DEV_MODE=1). Bypasses meeting-api / runtime-api so the
# iteration loop is purely: edit src/ → run this script → bot joins meeting.
#
# Usage:
#   ./services/vexa-bot/dev/hot-bot.sh <platform> <meeting_url> [bot_name]
#
# Examples:
#   ./services/vexa-bot/dev/hot-bot.sh teams \
#     "https://teams.microsoft.com/l/meetup-join/19%3ameeting_..." \
#     "Dev Bot"
#
#   ./services/vexa-bot/dev/hot-bot.sh google_meet "https://meet.google.com/abc-defg-hij"
#
# Requirements:
#   - vexaai/vexa-bot:dev image built once (deploy/compose/make build-bot-image).
#   - Either local-prod transcription (default: TRANSCRIPTION_SERVICE_URL=
#     http://host.docker.internal:8085) or override via env.
#   - A docker network the bot can reach Redis on; defaults to host network
#     for the simplest setup.
#
# Wall-time:
#   First run after image build: ~5s container start + meeting-join time.
#   Subsequent runs after edits: same. NO rebuild step.

set -euo pipefail

PLATFORM="${1:?platform required (teams|google_meet|zoom)}"
MEETING_URL="${2:?meeting_url required}"
BOT_NAME="${3:-Vexa Hot-Dev Bot}"

# Resolve repo root so the bind mount uses an absolute path.
REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
SRC_HOST="${REPO_ROOT}/services/vexa-bot/core/src"
SRC_CONT="/app/vexa-bot/core/src"

if [ ! -d "$SRC_HOST" ]; then
  echo "[hot-bot] ERROR: bot source not found at $SRC_HOST" >&2
  exit 1
fi

# Env defaults — override on the command line if needed.
BOT_IMAGE="${BOT_IMAGE:-vexaai/vexa-bot:dev}"
REDIS_URL="${REDIS_URL:-redis://host.docker.internal:6379/0}"
TRANSCRIPTION_SERVICE_URL="${TRANSCRIPTION_SERVICE_URL:-http://host.docker.internal:8085/v1/audio/transcriptions}"
TRANSCRIPTION_SERVICE_TOKEN="${TRANSCRIPTION_SERVICE_TOKEN:-local-dev}"
INTERNAL_API_SECRET="${INTERNAL_API_SECRET:-hot-dev-bypass}"

# Derive native meeting id (best-effort per platform).
NATIVE_ID=""
case "$PLATFORM" in
  teams)
    # Teams URLs include a long encoded thread id; use the URL itself as the
    # native id placeholder. The bot reads native_meeting_id from BOT_CONFIG.
    NATIVE_ID="$(echo "$MEETING_URL" | sed -E 's|.*/19%3a([^/]+)/.*|\1|')"
    [ -z "$NATIVE_ID" ] && NATIVE_ID="$MEETING_URL"
    ;;
  google_meet)
    NATIVE_ID="$(echo "$MEETING_URL" | sed -E 's|.*meet.google.com/([a-z-]+).*|\1|')"
    ;;
  zoom)
    NATIVE_ID="$(echo "$MEETING_URL" | sed -nE 's|.*/j/([0-9]+).*|\1|p')"
    ;;
esac

BOT_CONFIG=$(cat <<JSON
{
  "platform": "${PLATFORM}",
  "meetingUrl": "${MEETING_URL}",
  "botName": "${BOT_NAME}",
  "token": "hot-dev",
  "connectionId": "hot-dev-$(date +%s)",
  "nativeMeetingId": "${NATIVE_ID}",
  "meeting_id": 0,
  "redisUrl": "${REDIS_URL}",
  "automaticLeave": {
    "waitingRoomTimeout": 300000,
    "noOneJoinedTimeout": 120000,
    "everyoneLeftTimeout": 60000
  }
}
JSON
)

cat <<INFO
[hot-bot] Spawning $BOT_IMAGE
  platform        : $PLATFORM
  meeting URL     : $MEETING_URL
  bot name        : $BOT_NAME
  source mount    : $SRC_HOST → $SRC_CONT (ro)
  TRANSCRIPTION   : $TRANSCRIPTION_SERVICE_URL
  REDIS_URL       : $REDIS_URL
  BOT_DEV_MODE    : 1
INFO

DOCKER_TTY_ARGS=""
if [ -t 0 ] && [ -t 1 ]; then DOCKER_TTY_ARGS="-it"; fi

exec docker run --rm $DOCKER_TTY_ARGS \
  --platform linux/amd64 \
  --add-host=host.docker.internal:host-gateway \
  --shm-size=2g \
  -v "${SRC_HOST}:${SRC_CONT}:ro" \
  -e "BOT_DEV_MODE=1" \
  -e "BOT_CONFIG=${BOT_CONFIG}" \
  -e "REDIS_URL=${REDIS_URL}" \
  -e "TRANSCRIPTION_SERVICE_URL=${TRANSCRIPTION_SERVICE_URL}" \
  -e "TRANSCRIPTION_SERVICE_TOKEN=${TRANSCRIPTION_SERVICE_TOKEN}" \
  -e "INTERNAL_API_SECRET=${INTERNAL_API_SECRET}" \
  -e "NODE_ENV=development" \
  -p "6080:6080" \
  "${BOT_IMAGE}"
