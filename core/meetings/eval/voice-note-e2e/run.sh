#!/usr/bin/env bash
# The voice-note E2E (#1783), on a host with Docker (bbb), never the laptop: a throwaway Valkey, the
# branch's meeting-api and gateway as local processes, the bot as a local Node process per note, and
# a real transcription service. A synthetic speech WAV goes through the same WebSocket Minutes uses.
#
#   STT_URL=http://127.0.0.1:13950/v1/audio/transcriptions STT_TOKEN_FROM=<container> \
#     core/meetings/eval/voice-note-e2e/run.sh
#
# STT_TOKEN_FROM names a container whose API_TOKEN is the STT key; it is read into a variable and
# never printed. Everything is torn down on exit; the work directory keeps the logs.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../../../.." && pwd)
HERE="$ROOT/core/meetings/eval/voice-note-e2e"
WORK=${WORK:-$(mktemp -d /tmp/vn-e2e.XXXXXX)}
mkdir -p "$WORK/bot-logs"
UV=${UV:-uv}
REDIS_PORT=${REDIS_PORT:-16390}; MA_PORT=${MA_PORT:-18890}; GW_PORT=${GW_PORT:-18891}
NAME=vn-e2e-redis-$$
REDIS_PASS=$(openssl rand -hex 16)
export ADMIN_TOKEN=$(openssl rand -hex 24) E2E_API_KEY=vxa_e2e_$(openssl rand -hex 12)
export INTERNAL_API_SECRET=$(openssl rand -hex 24)
export TRANSCRIPTION_SERVICE_URL="$STT_URL"
export TRANSCRIPTION_SERVICE_TOKEN=$(docker exec "$STT_TOKEN_FROM" printenv API_TOKEN)
export VOICE_NOTE_MAX_DURATION_SEC=${VOICE_NOTE_MAX_DURATION_SEC:-600}
# The Redis URL carries a password: it travels in the environment, never on a command line.
export E2E_REDIS_URL="redis://:$REDIS_PASS@127.0.0.1:$REDIS_PORT/0"
pids=()
# Each service runs in a process group of its own (setsid), so the whole group — uv, python and the
# bots it started — goes on exit.
cleanup() { for p in "${pids[@]}"; do kill -TERM -- "-$p" 2>/dev/null || true; done; sleep 2; docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -d --name "$NAME" -p 127.0.0.1:$REDIS_PORT:6379 valkey/valkey:8-alpine \
  valkey-server --requirepass "$REDIS_PASS" >/dev/null
"$UV" run --project "$ROOT/core/gateway/services/gateway" python \
  "$ROOT/core/gateway/services/gateway/src/gateway/identity_token.py" keygen "$WORK/sign.pem" "$WORK/pub.pem"

setsid bash -c 'cd "$1" && PYTHONPATH=src exec "$2" run --group production python "${@:3}"' _ \
  "$ROOT/core/meetings/services/meeting-api" "$UV" "$HERE/meeting_api_stack.py" --port $MA_PORT --verify-key "$WORK/pub.pem" \
  --bot-dir "$ROOT/core/meetings/services/bot" --log-dir "$WORK/bot-logs" >"$WORK/meeting-api.log" 2>&1 & pids+=($!)
setsid bash -c 'cd "$1" && PYTHONPATH=src exec "$2" run --group production python "${@:3}"' _ \
  "$ROOT/core/gateway/services/gateway" "$UV" "$HERE/gateway_stack.py" --port $GW_PORT --meeting-api "http://127.0.0.1:$MA_PORT" --signing-key "$WORK/sign.pem" \
  >"$WORK/gateway.log" 2>&1 & pids+=($!)
up=""
for _ in $(seq 90); do
  curl -fsS "http://127.0.0.1:$GW_PORT/health" >/dev/null 2>&1 && curl -fsS "http://127.0.0.1:$MA_PORT/health" >/dev/null 2>&1 && { up=1; break; }
  sleep 1
done
[ -n "$up" ] || { echo "the stack did not come up: see $WORK/*.log" >&2; exit 1; }

WAV=${WAV:-$WORK/speech.wav}
if [ ! -f "$WAV" ]; then
  espeak-ng -s 150 -w "$WORK/speech-raw.wav" -f "$HERE/speech.txt"
  ffmpeg -loglevel error -y -i "$WORK/speech-raw.wav" -ar 16000 -ac 1 -sample_fmt s16 "$WAV"
fi
(cd "$ROOT/core/gateway/services/gateway" && "$UV" run --group production python "$HERE/client.py" \
  --gateway "http://127.0.0.1:$GW_PORT" --wav "$WAV" --bot-log-dir "$WORK/bot-logs") \
  | tee "$WORK/report.json"
echo "logs: $WORK" >&2
