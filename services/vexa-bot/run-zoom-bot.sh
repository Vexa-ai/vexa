#!/usr/bin/env bash
# Run Zoom bot in a container and join the meeting via docker run.
# Requires: vexa stack running (redis, optional whisperlive) and network vexa_dev_vexa_default.
#
# Usage:
#   ./run-zoom-bot.sh
#   # Or from repo root: ./services/vexa-bot/run-zoom-bot.sh
#
# For interactive terminal (see logs, Ctrl+C to stop): add -it after --rm in the docker run line.

set -e

MEETING_URL="${ZOOM_MEETING_URL:-https://us05web.zoom.us/j/81137509581?pwd=swiL9I4MliS99mTVV1FXDCWsgaiRvy.1}"
BOT_NAME="${ZOOM_BOT_NAME:-Vexa Bot}"
REDIS_URL="${REDIS_URL:-redis://redis:6379/0}"
WHISPER_LIVE_URL="${WHISPER_LIVE_URL:-ws://whisperlive:9090/ws}"
ZOOM_CLIENT_ID="${ZOOM_CLIENT_ID:-6gMe9aY8R8OG1pqsmTFBBg}"
ZOOM_CLIENT_SECRET="${ZOOM_CLIENT_SECRET:-mN9cMR039CnNvsE8TGSifduK11k7C6g8}"

# Extract meeting number and passcode from URL (e.g. .../j/81137509581?pwd=xxx)
if [[ "$MEETING_URL" =~ /j/([0-9]+) ]]; then
  MEETING_NUMBER="${BASH_REMATCH[1]}"
else
  echo "Error: ZOOM_MEETING_URL must contain /j/<meeting_number>" >&2
  exit 1
fi

BOT_CONFIG="{\"platform\":\"zoom\",\"meetingUrl\":\"$MEETING_URL\",\"botName\":\"$BOT_NAME\",\"token\":\"docker-run\",\"connectionId\":\"docker-run\",\"nativeMeetingId\":\"$MEETING_NUMBER\",\"meeting_id\":1,\"redisUrl\":\"$REDIS_URL\",\"automaticLeave\":{\"waitingRoomTimeout\":300000,\"noOneJoinedTimeout\":120000,\"everyoneLeftTimeout\":60000}}"

echo "Joining Zoom meeting: $MEETING_URL"
echo "Bot name: $BOT_NAME"
echo "Network: vexa_dev_vexa_default"
echo ""

docker run --rm \
  --platform linux/amd64 \
  --network vexa_dev_vexa_default \
  -e "BOT_CONFIG=$BOT_CONFIG" \
  -e "WHISPER_LIVE_URL=$WHISPER_LIVE_URL" \
  -e "ZOOM_CLIENT_ID=$ZOOM_CLIENT_ID" \
  -e "ZOOM_CLIENT_SECRET=$ZOOM_CLIENT_SECRET" \
  vexa-bot:dev
