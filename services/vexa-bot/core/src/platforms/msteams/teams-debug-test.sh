#!/bin/bash

# Teams Debug Test Script
# This script builds the Docker image, runs the Teams bot, and monitors its logs

set -e

# Configuration
CONTAINER_NAME="vexa-bot-teams-debug"
IMAGE_NAME="vexa-bot:test"
SCREENSHOTS_DIR="../teams_docs/screenshots"
MEETING_URL="https://teams.live.com/meet/9398850880426?p=RBZCWdxyp85TpcKna8"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 Starting Teams Debug Test${NC}"

# Clean up any existing container
echo -e "${YELLOW}🧹 Cleaning up existing containers...${NC}"
docker rm -f $CONTAINER_NAME 2>/dev/null || true

# Build the Docker image
echo -e "${YELLOW}🔨 Building Docker image...${NC}"
cd ../../../..
docker build -t $IMAGE_NAME -f core/Dockerfile core/

# Run the bot
echo -e "${YELLOW}🤖 Starting Teams bot...${NC}"
cd tests
docker run --rm --name $CONTAINER_NAME \
  --network vexa_dev_vexa_default \
  -v "$(pwd)/$SCREENSHOTS_DIR:/app/screenshots" \
  -e BOT_CONFIG='{
    "platform":"teams",
    "meetingUrl":"'$MEETING_URL'",
    "botName":"TeamsDebugBot",
    "connectionId":"teams-debug-test",
    "nativeMeetingId":"9398850880426",
    "token":"debug-token",
    "redisUrl":"redis://redis:6379/0",
    "container_name":"'$CONTAINER_NAME'",
    "automaticLeave":{
      "waitingRoomTimeout":300000,
      "noOneJoinedTimeout":600000,
      "everyoneLeftTimeout":120000
    }
  }' \
  -e WHISPER_LIVE_URL="ws://whisperlive:9090" \
  -e WL_MAX_CLIENTS="10" \
  --cap-add=SYS_ADMIN \
  --shm-size=2g \
  $IMAGE_NAME

echo -e "${GREEN}✅ Teams debug test completed${NC}"



