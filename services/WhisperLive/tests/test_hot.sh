#!/bin/bash
#
# WhisperLive — Hot Chain Test
#
# Tests WhisperLive in isolation with its test compose (exposes port 9090).
# Requires transcription-service running on localhost:8083 (start separately).
#
# Usage:
#   bash tests/test_hot.sh              # full: start → verify → chain → stop
#   bash tests/test_hot.sh --verify     # verify WebSocket accepts connections
#   bash tests/test_hot.sh --chain      # test audio → transcript chain
#   bash tests/test_hot.sh --start      # start test compose only
#   bash tests/test_hot.sh --stop       # stop test compose only
#

set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$SERVICE_DIR/../.." && pwd)"
TEST_DIR="$SERVICE_DIR/tests"
COMPOSE_FILE="$TEST_DIR/docker-compose.test.yml"
TEST_AUDIO="$REPO_ROOT/services/transcription-service/tests/test_audio.wav"
WS_URL="ws://localhost:19090/ws"
HEALTH_URL="http://localhost:19091/health"

MODE="${1:---full}"

wait_for_health() {
  echo "Waiting for WhisperLive at $HEALTH_URL..."
  for i in $(seq 1 30); do
    if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
      echo "  Healthy after ${i}s"
      return 0
    fi
    sleep 1
  done
  echo "  TIMEOUT — not healthy after 30s"
  return 1
}

check_transcription_service() {
  if ! curl -sf http://localhost:8083/health > /dev/null 2>&1; then
    echo "ERROR: transcription-service not running on localhost:8083"
    echo "Start it first: cd services/transcription-service && docker compose up -d"
    return 1
  fi
  echo "  transcription-service: healthy"
}

start_service() {
  echo "=== Starting WhisperLive (test compose) ==="
  check_transcription_service || return 1
  docker compose -f "$COMPOSE_FILE" up -d 2>&1 | grep -v "^$"
  wait_for_health
  echo ""
  echo "Service info:"
  curl -s "$HEALTH_URL" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "  (health endpoint responded)"
  echo ""
}

verify_service() {
  echo "=== Verify: WhisperLive ==="

  # Check WebSocket is accepting connections
  echo "Testing WebSocket connection..."
  python3 -c "
import asyncio
import websockets
import json

async def test():
    try:
        async with websockets.connect('$WS_URL', close_timeout=3) as ws:
            # Send config with required meeting context
            import uuid
            await ws.send(json.dumps({
                'uid': str(uuid.uuid4()),
                'platform': 'google_meet',
                'meeting_url': 'https://meet.google.com/test-verify',
                'token': 'test-token',
                'meeting_id': 'test-verify-' + str(uuid.uuid4())[:8],
                'language': 'en',
                'task': 'transcribe',
            }))
            # Try to receive server response
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=3)
                data = json.loads(msg)
                print(f'  Server response: {data.get(\"message\", data)}')
            except asyncio.TimeoutError:
                print('  Connected (no immediate response — expected)')
            print('  PASS: WebSocket accepts connections')
    except Exception as e:
        print(f'  FAIL: {e}')

asyncio.run(test())
" 2>&1
}

chain_test() {
  echo "=== Chain Test: WhisperLive → transcription-service ==="
  check_transcription_service || return 1

  if [ ! -f "$TEST_AUDIO" ]; then
    echo "FAIL: test audio not found at $TEST_AUDIO"
    return 1
  fi

  echo "Sending audio through WhisperLive → transcription-service chain..."
  python3 -c "
import asyncio
import websockets
import json
import wave
import time

async def test_chain():
    audio_path = '$TEST_AUDIO'
    with wave.open(audio_path, 'rb') as w:
        frames = w.readframes(w.getnframes())
        rate = w.getframerate()

    duration = len(frames) / rate / 2
    print(f'  Audio: {duration:.1f}s, {rate}Hz, {len(frames)} bytes')

    start = time.time()
    async with websockets.connect('$WS_URL') as ws:
        # Send config (WhisperLive requires meeting context)
        import uuid
        await ws.send(json.dumps({
            'uid': str(uuid.uuid4()),
            'platform': 'google_meet',
            'meeting_url': 'https://meet.google.com/test-hot-test',
            'token': 'test-token',
            'meeting_id': 'test-hot-' + str(uuid.uuid4())[:8],
            'language': 'en',
            'task': 'transcribe',
            'use_vad': True,
        }))

        # Convert Int16 PCM to Float32 (bot sends Float32Array)
        import struct
        import array
        samples = struct.unpack(f'<{len(frames)//2}h', frames)
        float_data = array.array('f', [s / 32768.0 for s in samples])

        # Send audio in chunks (~100ms of Float32 data, matching bot behavior)
        chunk_samples = rate // 10  # 100ms
        offset = 0
        while offset < len(float_data):
            chunk = float_data[offset:offset+chunk_samples]
            await ws.send(bytes(chunk))
            offset += chunk_samples
            await asyncio.sleep(0.05)

        # Collect segments
        segments = []
        try:
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=15)
                data = json.loads(msg)
                if 'segments' in data:
                    for seg in data['segments']:
                        text = seg.get('text', '').strip()
                        if text:
                            segments.append(text)
                elif 'message' in data:
                    if 'DISCONNECT' in str(data.get('message','')):
                        break
        except asyncio.TimeoutError:
            pass

    elapsed = time.time() - start

    if segments:
        full = ' '.join(segments)
        print(f'  Transcript: \"{full}\"')
        print(f'  Segments: {len(segments)}')
        print(f'  Chain latency: {elapsed:.2f}s')
        print(f'  PASS: audio → WhisperLive → transcription → segments')
    else:
        print(f'  FAIL: no segments received after {elapsed:.1f}s')
        print(f'  Check: docker logs for WhisperLive and transcription-service')

asyncio.run(test_chain())
" 2>&1
}

stop_service() {
  echo ""
  echo "=== Stopping WhisperLive (test compose) ==="
  docker compose -f "$COMPOSE_FILE" down 2>&1 | grep -v "^$"
}

case "$MODE" in
  --full)
    start_service
    verify_service
    chain_test
    stop_service
    ;;
  --verify)
    verify_service
    ;;
  --chain)
    chain_test
    ;;
  --start)
    start_service
    ;;
  --stop)
    stop_service
    ;;
  *)
    echo "Usage: bash tests/test_hot.sh [--full|--verify|--chain|--start|--stop]"
    exit 1
    ;;
esac
