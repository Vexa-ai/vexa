#!/bin/bash
# Helper script to scale workers up/down for load testing

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

NUM_WORKERS="${1:-3}"

if ! [[ "$NUM_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
    echo -e "${RED}Error: Invalid number of workers: $NUM_WORKERS${NC}"
    echo "Usage: $0 [1-9]"
    exit 1
fi

echo -e "${BLUE}Scaling transcription workers to: $NUM_WORKERS${NC}\n"

# Stop all workers first
echo -e "${YELLOW}Stopping all workers...${NC}"
docker-compose stop transcription-worker-1 transcription-worker-2 transcription-worker-3 2>/dev/null || true

# Start the requested number of workers
echo -e "${YELLOW}Starting $NUM_WORKERS worker(s)...${NC}"
if [ $NUM_WORKERS -ge 1 ]; then
    docker-compose up -d transcription-worker-1
fi
if [ $NUM_WORKERS -ge 2 ]; then
    docker-compose up -d transcription-worker-2
fi
if [ $NUM_WORKERS -ge 3 ]; then
    docker-compose up -d transcription-worker-3
fi

echo -e "${GREEN}Waiting for workers to be ready...${NC}\n"
sleep 5

# Check worker status
echo -e "${BLUE}Worker Status:${NC}"
for i in $(seq 1 $NUM_WORKERS); do
    container_name="transcription-worker-$i"
    if docker ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
        health=$(docker inspect --format='{{.State.Health.Status}}' "$container_name" 2>/dev/null || echo "unknown")
        echo -e "  ${container_name}: ${GREEN}running${NC} (health: $health)"
    else
        echo -e "  ${container_name}: ${RED}not running${NC}"
    fi
done

echo -e "\n${GREEN}Workers scaled to $NUM_WORKERS${NC}"
echo -e "${YELLOW}Note: Nginx will automatically detect available workers${NC}\n"
