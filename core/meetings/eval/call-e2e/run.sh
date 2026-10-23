#!/usr/bin/env bash
# The call E2E (#1783), on a host with Docker (bbb), never the laptop: a throwaway Valkey and LiveKit
# server, the branch's meeting-api and gateway as local processes, the listener bot as a local Node
# process, two headless participants publishing speech WAVs, and a real transcription service.
#
#   STT_URL=http://127.0.0.1:13950/v1/audio/transcriptions STT_TOKEN_FROM=<container> \
#     core/meetings/eval/call-e2e/run.sh
#
# Secrets are minted here or read into variables, travel in the environment, and are never printed.
# Everything is torn down on exit; the work directory keeps the logs and the report.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../../../.." && pwd)
HERE="$ROOT/core/meetings/eval/call-e2e"
WORK=${WORK:-$(mktemp -d /tmp/call-e2e.XXXXXX)}
mkdir -p "$WORK/bot-logs"
UV=${UV:-uv}
REDIS_PORT=${REDIS_PORT:-16391}; MA_PORT=${MA_PORT:-18892}; GW_PORT=${GW_PORT:-18893}
LK_PORT=${LK_PORT:-17880}; LK_TCP=${LK_TCP:-17881}; LK_UDP_START=${LK_UDP_START:-51000}; LK_UDP_END=${LK_UDP_END:-51060}
LK_IMAGE=${LK_IMAGE:-livekit/livekit-server:v1.13.7@sha256:6fd3b7088874c4d119160dd688798dfec852bc014786d392caad15f6f63912a3}
RNAME=call-e2e-redis-$$; LNAME=call-e2e-livekit-$$
REDIS_PASS=$(openssl rand -hex 16)
export ADMIN_TOKEN=$(openssl rand -hex 24) E2E_API_KEY=vxa_e2e_$(openssl rand -hex 12)
export INTERNAL_API_SECRET=$(openssl rand -hex 24)
export TRANSCRIPTION_SERVICE_URL="$STT_URL"
export TRANSCRIPTION_SERVICE_TOKEN=$(docker exec "$STT_TOKEN_FROM" printenv API_TOKEN)
export E2E_REDIS_URL="redis://:$REDIS_PASS@127.0.0.1:$REDIS_PORT/0"
# WebRTC ignores loopback candidates, and over the docker bridge address the DTLS handshake never
# completed (ICE connected, then "connect timeout"), so media rides the host's own private address
# (the default route's source) — bound to it alone, so nothing listens on any other interface.
LK_IP=${LK_IP:-$(ip -4 route get 1.1.1.1 | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')}
export LIVEKIT_URL="ws://$LK_IP:$LK_PORT" LIVEKIT_API_KEY=e2ekey LIVEKIT_API_SECRET=$(openssl rand -hex 32)
export CALL_EMPTY_GRACE_SEC=${CALL_EMPTY_GRACE_SEC:-5} CALL_FIRST_JOIN_TIMEOUT_SEC=120 CALL_MAX_DURATION_SEC=900
pids=()
cleanup() {
  for p in "${pids[@]}"; do kill -TERM -- "-$p" 2>/dev/null || true; done
  sleep 2
  docker rm -f "$RNAME" "$LNAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d --name "$RNAME" -p 127.0.0.1:$REDIS_PORT:6379 valkey/valkey:8-alpine \
  valkey-server --requirepass "$REDIS_PASS" >/dev/null
# LiveKit on the host network, bound to the bridge address only; keys and config travel in the environment.
LIVEKIT_KEYS="$LIVEKIT_API_KEY: $LIVEKIT_API_SECRET" LIVEKIT_CONFIG="port: $LK_PORT
bind_addresses: ['$LK_IP']
rtc:
  tcp_port: $LK_TCP
  port_range_start: $LK_UDP_START
  port_range_end: $LK_UDP_END
  use_external_ip: false
  node_ip: $LK_IP
room:
  auto_create: false" docker run -d --name "$LNAME" --network host -e LIVEKIT_KEYS -e LIVEKIT_CONFIG "$LK_IMAGE" >/dev/null

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
  curl -fsS "http://127.0.0.1:$GW_PORT/health" >/dev/null 2>&1 && curl -fsS "http://127.0.0.1:$MA_PORT/health" >/dev/null 2>&1 \
    && curl -fsS "http://$LK_IP:$LK_PORT/" >/dev/null 2>&1 && { up=1; break; }
  sleep 1
done
[ -n "$up" ] || { docker logs "$LNAME" >"$WORK/livekit.log" 2>&1 || true; echo "the stack did not come up: see $WORK/*.log" >&2; exit 1; }

for who in a b; do
  if [ ! -f "$WORK/$who.wav" ]; then
    espeak-ng -v "en-us+$([ $who = a ] && echo f3 || echo m3)" -s 150 -w "$WORK/$who-raw.wav" -f "$HERE/speech-$who.txt"
    ffmpeg -loglevel error -y -i "$WORK/$who-raw.wav" -ar 16000 -ac 1 -sample_fmt s16 "$WORK/$who.wav"
  fi
done
(cd "$ROOT/core/meetings/services/meeting-api" && PYTHONPATH=src "$UV" run --group production python "$HERE/client.py" \
  --gateway "http://127.0.0.1:$GW_PORT" --wav-a "$WORK/a.wav" --wav-b "$WORK/b.wav" \
  --bot-dir "$ROOT/core/meetings/services/bot" --bot-log-dir "$WORK/bot-logs") | tee "$WORK/report.json"
docker logs "$LNAME" >"$WORK/livekit.log" 2>&1 || true
echo "logs: $WORK" >&2
