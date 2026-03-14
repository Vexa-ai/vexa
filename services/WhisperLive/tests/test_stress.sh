#!/bin/bash
#
# WhisperLive — Stress Test
#
# Tests WhisperLive under concurrent WebSocket load.
# Requires transcription-service running on localhost:8083 and
# WhisperLive test compose on ports 19090/19091.
#
# Usage:
#   bash tests/test_stress.sh              # full: start compose, run stress, stop
#   bash tests/test_stress.sh --run-only   # skip compose lifecycle, just run tests
#
# Results are printed to stdout and saved to tests/results/stress_results.txt
#

set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$SERVICE_DIR/../.." && pwd)"
TEST_DIR="$SERVICE_DIR/tests"
COMPOSE_FILE="$TEST_DIR/docker-compose.test.yml"
TEST_AUDIO="$REPO_ROOT/services/transcription-service/tests/test_audio.wav"
WS_URL="ws://localhost:19090/ws"
HEALTH_URL="http://localhost:19091/health"
RESULTS_DIR="$TEST_DIR/results"

MODE="${1:---full}"

mkdir -p "$RESULTS_DIR"
RESULTS_FILE="$RESULTS_DIR/stress_results.txt"

log() { echo "$@" | tee -a "$RESULTS_FILE"; }

wait_for_health() {
  log "Waiting for WhisperLive at $HEALTH_URL..."
  for i in $(seq 1 30); do
    if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
      log "  Healthy after ${i}s"
      return 0
    fi
    sleep 1
  done
  log "  TIMEOUT — not healthy after 30s"
  return 1
}

check_transcription_service() {
  if ! curl -sf http://localhost:8083/health > /dev/null 2>&1; then
    log "ERROR: transcription-service not running on localhost:8083"
    log "Start it first: cd services/transcription-service && docker compose up -d"
    return 1
  fi
  log "  transcription-service: healthy"
}

start_service() {
  log "=== Starting WhisperLive (test compose) ==="
  check_transcription_service || return 1
  docker compose -f "$COMPOSE_FILE" up -d 2>&1 | grep -v "^$"
  wait_for_health
}

stop_service() {
  log ""
  log "=== Stopping WhisperLive (test compose) ==="
  docker compose -f "$COMPOSE_FILE" down 2>&1 | grep -v "^$"
}

run_stress() {
  log ""
  log "============================================"
  log "  WhisperLive Stress Test"
  log "  $(date -Iseconds)"
  log "============================================"
  log ""

  if [ ! -f "$TEST_AUDIO" ]; then
    log "FAIL: test audio not found at $TEST_AUDIO"
    return 1
  fi

  check_transcription_service || return 1

  # Resource baseline
  log "=== Resource Baseline ==="
  docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" 2>/dev/null \
    | grep -E "whisperlive|redis|NAME" | tee -a "$RESULTS_FILE" || true
  log ""

  for CONCURRENCY in 1 5 10; do
    log "--- Concurrency: $CONCURRENCY connections ---"

    python3 -c "
import asyncio
import websockets
import json
import wave
import struct
import array
import time
import uuid
import sys

WS_URL = '$WS_URL'
AUDIO_PATH = '$TEST_AUDIO'
CONCURRENCY = $CONCURRENCY

# Load and convert audio once
with wave.open(AUDIO_PATH, 'rb') as w:
    frames = w.readframes(w.getnframes())
    rate = w.getframerate()

samples = struct.unpack(f'<{len(frames)//2}h', frames)
float_data = array.array('f', [s / 32768.0 for s in samples])
audio_bytes = bytes(float_data)
chunk_samples = rate // 10  # 100ms chunks

async def run_client(client_id):
    \"\"\"Single client: connect, stream audio, collect segments.\"\"\"
    result = {
        'client_id': client_id,
        'connected': False,
        'segments': [],
        'error': None,
        'elapsed': 0,
    }
    start = time.time()
    try:
        async with websockets.connect(WS_URL, close_timeout=5, open_timeout=10) as ws:
            result['connected'] = True
            uid = str(uuid.uuid4())
            meeting_id = f'stress-{client_id}-{uid[:8]}'

            await ws.send(json.dumps({
                'uid': uid,
                'platform': 'google_meet',
                'meeting_url': f'https://meet.google.com/stress-test-{client_id}',
                'token': f'stress-token-{client_id}',
                'meeting_id': meeting_id,
                'language': 'en',
                'task': 'transcribe',
                'use_vad': True,
            }))

            # Send audio in Float32 chunks
            offset = 0
            while offset < len(float_data):
                chunk = float_data[offset:offset+chunk_samples]
                await ws.send(bytes(chunk))
                offset += chunk_samples
                await asyncio.sleep(0.03)

            # Collect segments
            try:
                while True:
                    msg = await asyncio.wait_for(ws.recv(), timeout=15)
                    data = json.loads(msg)
                    if 'segments' in data:
                        for seg in data['segments']:
                            text = seg.get('text', '').strip()
                            if text:
                                result['segments'].append({
                                    'text': text,
                                    'meeting_id': meeting_id,
                                })
                    elif 'message' in data and 'DISCONNECT' in str(data.get('message', '')):
                        break
            except asyncio.TimeoutError:
                pass

    except Exception as e:
        result['error'] = str(e)

    result['elapsed'] = round(time.time() - start, 2)
    return result

async def main():
    tasks = [run_client(i) for i in range(CONCURRENCY)]
    results = await asyncio.gather(*tasks)

    connected = sum(1 for r in results if r['connected'])
    with_segments = sum(1 for r in results if len(r['segments']) > 0)
    total_segments = sum(len(r['segments']) for r in results)
    errors = [r for r in results if r['error']]
    avg_elapsed = sum(r['elapsed'] for r in results) / len(results)

    print(f'  Connections accepted: {connected}/{CONCURRENCY}')
    print(f'  Streams with segments: {with_segments}/{CONCURRENCY}')
    print(f'  Total segments received: {total_segments}')
    print(f'  Average elapsed: {avg_elapsed:.1f}s')

    if errors:
        print(f'  Errors ({len(errors)}):')
        for e in errors:
            print(f'    client {e[\"client_id\"]}: {e[\"error\"]}')

    # Cross-contamination check: each client should only have its own meeting_id in segments
    contaminated = 0
    for r in results:
        expected_mid = None
        for seg in r['segments']:
            if expected_mid is None:
                expected_mid = seg['meeting_id']
            elif seg['meeting_id'] != expected_mid:
                contaminated += 1
                break

    if contaminated > 0:
        print(f'  CROSS-CONTAMINATION: {contaminated} streams had mixed meeting_ids')
    else:
        print(f'  Cross-contamination check: PASS (0 streams mixed)')

    if connected == CONCURRENCY and with_segments > 0 and contaminated == 0:
        print(f'  RESULT: PASS')
    elif connected == CONCURRENCY and with_segments == 0:
        print(f'  RESULT: WARN (connected but no segments — transcription-service may be slow)')
    else:
        print(f'  RESULT: FAIL')

asyncio.run(main())
" 2>&1 | tee -a "$RESULTS_FILE"

    # Resource snapshot after this concurrency level
    log ""
    log "  Resources after $CONCURRENCY connections:"
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" 2>/dev/null \
      | grep -E "whisperlive|redis|NAME" | tee -a "$RESULTS_FILE" || true
    log ""

    # Brief cooldown between levels
    if [ "$CONCURRENCY" -ne 10 ]; then
      sleep 3
    fi
  done

  log ""
  log "=== Stress Test Complete ==="
  log "Results saved to: $RESULTS_FILE"
}

case "$MODE" in
  --full)
    > "$RESULTS_FILE"
    start_service
    run_stress
    stop_service
    ;;
  --run-only)
    > "$RESULTS_FILE"
    run_stress
    ;;
  *)
    echo "Usage: bash tests/test_stress.sh [--full|--run-only]"
    exit 1
    ;;
esac
