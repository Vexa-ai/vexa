#!/bin/bash
#
# Transcription Service — Hot Isolation Test
#
# Starts the service, verifies it works, load tests it, stops it.
# Self-contained — knows its own port, API contract, test data, baselines.
#
# Usage:
#   cd services/transcription-service
#   bash tests/test_hot.sh              # full: start → verify → load → stop
#   bash tests/test_hot.sh --verify     # just verify (service already running)
#   bash tests/test_hot.sh --load       # just load test (service already running)
#

set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$SERVICE_DIR/../.." && pwd)"

# ── Service config ────────────────────────────────────────────────────────
# Read from docker-compose to get the actual port
HOST_PORT=$(grep -A5 'ports:' "$SERVICE_DIR/docker-compose.yml" 2>/dev/null | grep -oP '\d+(?=:80)' | head -1)
HOST_PORT="${HOST_PORT:-8083}"
BASE_URL="http://localhost:${HOST_PORT}"
ENDPOINT="${BASE_URL}/v1/audio/transcriptions"
HEALTH_URL="${BASE_URL}/health"
TEST_AUDIO="$SERVICE_DIR/tests/test_audio.wav"
MODEL="large-v3-turbo"
CONTAINER_PREFIX="transcription-service"

# Read API_TOKEN from .env if set
API_TOKEN=""
if [ -f "$SERVICE_DIR/.env" ]; then
  API_TOKEN=$(grep -E '^API_TOKEN=' "$SERVICE_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'" || true)
fi
# Build auth args (empty if no token or placeholder)
AUTH_ARGS=()
if [ -n "$API_TOKEN" ] && [ "$API_TOKEN" != "your_secure_token_here" ]; then
  AUTH_ARGS=(-H "Authorization: Bearer $API_TOKEN")
fi

# ── Baselines ─────────────────────────────────────────────────────────────
# Update these after each verified load test run
BASELINE_SINGLE_LATENCY_S=0.5        # GPU mode, 5s audio
BASELINE_CONCURRENT_5_DEGRADATION=20  # % acceptable degradation at 5 concurrent

MODE="${1:---full}"

# ── Helpers ───────────────────────────────────────────────────────────────

docker_stats_service() {
  docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' 2>/dev/null | grep "$CONTAINER_PREFIX" || true
}

wait_for_health() {
  echo "Waiting for service at $HEALTH_URL..."
  for i in $(seq 1 30); do
    if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
      echo "  Healthy after ${i}s"
      return 0
    fi
    # Also try base URL (some configs don't have /health)
    if curl -sf "$BASE_URL/" > /dev/null 2>&1; then
      echo "  Responding after ${i}s (no /health endpoint)"
      return 0
    fi
    sleep 1
  done
  echo "  TIMEOUT — service not healthy after 30s"
  return 1
}

# ── Start ─────────────────────────────────────────────────────────────────

start_service() {
  echo "=== Starting transcription-service ==="
  cd "$SERVICE_DIR"
  docker compose up -d 2>&1 | grep -v "^$"
  wait_for_health
  echo ""
  echo "Service info:"
  curl -s "$BASE_URL/" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "  (no info endpoint)"
  echo ""
  echo "Resource baseline:"
  docker_stats_service
  echo ""
}

# ── Verify ────────────────────────────────────────────────────────────────

verify_service() {
  echo "=== Verify: transcription-service ==="
  echo ""

  # Check test audio exists
  if [ ! -f "$TEST_AUDIO" ]; then
    echo "FAIL: test audio not found at $TEST_AUDIO"
    return 1
  fi

  # Send test audio
  echo "Sending test audio ($(du -h "$TEST_AUDIO" | cut -f1))..."
  TMPBODY=$(mktemp)
  TMPMETA=$(mktemp)
  curl -s -w "%{http_code} %{time_total}" -o "$TMPBODY" -X POST "$ENDPOINT" "${AUTH_ARGS[@]}" \
    -F "file=@${TEST_AUDIO}" \
    -F "model=${MODEL}" > "$TMPMETA" 2>&1
  BODY=$(cat "$TMPBODY")
  HTTP_CODE=$(awk '{print $1}' "$TMPMETA")
  LATENCY=$(awk '{print $2}' "$TMPMETA")
  rm -f "$TMPBODY" "$TMPMETA"

  echo "  HTTP: $HTTP_CODE"
  echo "  Latency: ${LATENCY}s"

  if [ "$HTTP_CODE" != "200" ]; then
    echo "  FAIL: expected 200, got $HTTP_CODE"
    echo "  Response: $BODY"
    return 1
  fi

  # Parse transcript
  TEXT=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('text',''))" 2>/dev/null)
  DURATION=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('duration',0))" 2>/dev/null)
  LANG=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('language',''))" 2>/dev/null)

  echo "  Language: $LANG"
  echo "  Audio duration: ${DURATION}s"
  echo "  Transcript: \"$TEXT\""

  # Quality checks
  if [ -z "$TEXT" ] || [ "$TEXT" = "None" ]; then
    echo "  FAIL: empty transcript"
    return 1
  fi

  WORD_COUNT=$(echo "$TEXT" | wc -w)
  if [ "$WORD_COUNT" -lt 3 ]; then
    echo "  FAIL: transcript too short ($WORD_COUNT words)"
    return 1
  fi

  echo ""
  echo "  PASS: coherent transcript, ${WORD_COUNT} words, ${LATENCY}s latency"
  echo ""

  # Docs validation: check README port matches actual port
  if [ -f "$SERVICE_DIR/README.md" ]; then
    README_PORT=$(grep -oP 'localhost:\K\d+' "$SERVICE_DIR/README.md" | head -1)
    if [ -n "$README_PORT" ] && [ "$README_PORT" != "$HOST_PORT" ]; then
      echo "  WARNING: README says port $README_PORT but service runs on $HOST_PORT"
    fi
  fi
}

# ── Load Test ─────────────────────────────────────────────────────────────

load_test() {
  echo "=== Load Test: transcription-service ==="
  echo ""
  RESULTS_DIR="$REPO_ROOT/tests/load/results"
  mkdir -p "$RESULTS_DIR"
  TIMESTAMP=$(date +%Y%m%d_%H%M%S)

  # Single request baseline
  echo "--- Single request baseline ---"
  LATENCIES=()
  for i in $(seq 1 3); do
    LATENCY=$(curl -s -w "%{time_total}" -o /dev/null -X POST "$ENDPOINT" \
      -F "file=@${TEST_AUDIO}" \
      -F "model=${MODEL}")
    LATENCIES+=("$LATENCY")
    echo "  Request $i: ${LATENCY}s"
  done

  LATS_CSV=$(IFS=,; echo "${LATENCIES[*]}")
  AVG_LATENCY=$(python3 -c "lats=[$LATS_CSV]; print(f'{sum(lats)/len(lats):.3f}')")
  echo "  Average: ${AVG_LATENCY}s (baseline: ${BASELINE_SINGLE_LATENCY_S}s)"
  echo ""

  # Resource usage
  echo "--- Resource usage ---"
  docker_stats_service
  echo ""

  # Concurrent (5)
  echo "--- Concurrent: 5 requests ---"
  CONCURRENT_START=$(date +%s%N)
  for i in $(seq 1 5); do
    curl -s -o /dev/null -X POST "$ENDPOINT" \
      -F "file=@${TEST_AUDIO}" \
      -F "model=${MODEL}" &
  done
  wait
  CONCURRENT_END=$(date +%s%N)
  CONCURRENT_DURATION=$(python3 -c "print(f'{(${CONCURRENT_END}-${CONCURRENT_START})/1e9:.2f}')")
  echo "  5 concurrent completed in ${CONCURRENT_DURATION}s"
  echo "  Resource after concurrent:"
  docker_stats_service
  echo ""

  # Save results
  cat > "$RESULTS_DIR/transcription_service_${TIMESTAMP}.json" << JSONEOF
{
  "service": "transcription-service",
  "timestamp": "$(date -Iseconds)",
  "port": $HOST_PORT,
  "model": "$MODEL",
  "test_audio": "$(basename $TEST_AUDIO)",
  "single_avg_latency_s": $AVG_LATENCY,
  "single_baseline_s": $BASELINE_SINGLE_LATENCY_S,
  "concurrent_5_total_s": $CONCURRENT_DURATION,
  "results_saved": true
}
JSONEOF

  echo "Results saved to $RESULTS_DIR/transcription_service_${TIMESTAMP}.json"
}

# ── Stop ──────────────────────────────────────────────────────────────────

stop_service() {
  echo ""
  echo "=== Stopping transcription-service ==="
  cd "$SERVICE_DIR"
  docker compose down 2>&1 | grep -v "^$"
}

# ── Main ──────────────────────────────────────────────────────────────────

case "$MODE" in
  --full)
    start_service
    verify_service
    load_test
    stop_service
    ;;
  --verify)
    verify_service
    ;;
  --load)
    load_test
    ;;
  --start)
    start_service
    ;;
  --stop)
    stop_service
    ;;
  *)
    echo "Usage: bash tests/test_hot.sh [--full|--verify|--load|--start|--stop]"
    exit 1
    ;;
esac
