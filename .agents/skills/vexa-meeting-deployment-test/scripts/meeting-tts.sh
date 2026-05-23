#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GROUND_TRUTH_FILE="$SKILL_DIR/references/ground-truth-conversation.md"
RUN_ROOT="${VEXA_MEETING_TEST_RUN_ROOT:-$SKILL_DIR/.runs}"

GATEWAY_URL="${GATEWAY_URL:-${VEXA_GATEWAY_URL:-http://localhost:8056}}"
ADMIN_URL="${ADMIN_URL:-${VEXA_ADMIN_URL:-http://localhost:8057}}"
DASHBOARD_URL="${DASHBOARD_URL:-${VEXA_DASHBOARD_URL:-http://localhost:3001}}"
TTS_SERVICE_URL="${TTS_SERVICE_URL:-${VEXA_TTS_SERVICE_URL:-http://localhost:8002}}"
ADMIN_DOCKER_CONTAINER="${ADMIN_DOCKER_CONTAINER:-${VEXA_ADMIN_DOCKER_CONTAINER:-}}"
TTS_DOCKER_CONTAINER="${TTS_DOCKER_CONTAINER:-${VEXA_TTS_DOCKER_CONTAINER:-}}"
ADMIN_TOKEN="${ADMIN_TOKEN:-${VEXA_ADMIN_API_TOKEN:-${ADMIN_API_TOKEN:-}}}"
LISTENER_EMAIL="${LISTENER_EMAIL:-test@vexa.ai}"
WEBHOOK_URL="${WEBHOOK_URL:-https://httpbin.org/post}"
CASE_ID="case-a"
RUN_ID="meeting-test-$(date -u '+%Y%m%d-%H%M%S')"
MEETING_URL=""
TURN_PAUSE_SECONDS="${TURN_PAUSE_SECONDS:-auto}"
MIN_TURN_PAUSE_SECONDS="${MIN_TURN_PAUSE_SECONDS:-9}"
MAX_TURN_PAUSE_SECONDS="${MAX_TURN_PAUSE_SECONDS:-28}"
ACTIVE_TIMEOUT_SECONDS="${ACTIVE_TIMEOUT_SECONDS:-420}"
POST_SPEECH_WAIT_SECONDS="${POST_SPEECH_WAIT_SECONDS:-35}"
POST_STOP_WAIT_SECONDS="${POST_STOP_WAIT_SECONDS:-20}"
WS_TRANSCRIPT_TIMEOUT_SECONDS="${WS_TRANSCRIPT_TIMEOUT_SECONDS:-420}"
WS_TRANSCRIPT_REQUIRED="${WS_TRANSCRIPT_REQUIRED:-1}"
LEAVE_RUNNING=0

PLATFORM=""
NATIVE_MEETING_ID=""
RUN_DIR=""
LISTENER_TOKEN=""
LISTENER_BOT_ID=""
WS_WATCH_PID=""
WS_TRANSCRIPT_VALIDATED=0
declare -a SPEAKER_LABELS=("speaker-1" "speaker-2")
declare -a SPEAKER_NAMES=("Maya Chen" "Leo Santos")
declare -a SPEAKER_DEFAULT_VOICES=("en_US-amy-medium" "en_US-danny-low")
declare -a SPEAKER_TOKENS=()
declare -a SPEAKER_BOT_IDS=()
declare -a CREATED_TOKENS=()
CLEANUP_DONE=0

usage() {
  cat <<'EOF'
Usage:
  meeting-tts.sh --meeting-url URL [options]
  meeting-tts.sh URL [options]

Runs the Vexa meeting deployment TTS smoke test from the skill directory.
It deploys one listener bot owned by test@vexa.ai plus two speaker bots,
plays references/ground-truth-conversation.md through /speak, fetches the
listener transcript, writes sanitized evidence, and stops the bots.

Options:
  --meeting-url URL       Google Meet or Teams URL to test.
  --case-id ID           Case label to substitute into ground truth (default: case-a).
  --run-id ID            Run label for evidence (default: meeting-test-UTC timestamp).
  --gateway-url URL      API gateway URL (default: http://localhost:8056).
  --admin-url URL        Admin API URL (default: http://localhost:8057).
  --dashboard-url URL    Dashboard URL (default: http://localhost:3001).
  --tts-service-url URL  TTS service URL for voice warmup (default: http://localhost:8002).
  --admin-docker-container CONTAINER
                         Execute Admin API curl calls inside this container.
                         Use for Lite when admin listens on container loopback.
  --tts-docker-container CONTAINER
                         Execute TTS warmup curl calls inside this container.
                         Use for Lite when TTS listens on container loopback.
  --admin-token TOKEN    Admin API token. If omitted, tries ADMIN_API_TOKEN env
                         and then local running deployment containers.
  --listener-email EMAIL Listener user email (default: test@vexa.ai).
  --turn-pause SECONDS   Pause between scripted turns, or auto (default: auto).
                         Auto estimates enough time for each TTS turn to finish.
  --active-timeout SEC   Max wait for all bots to become active (default: 420).
  --post-stop-wait SEC   Wait after stopping bots for final status, webhooks,
                         and recording metadata (default: 20).
  --ws-transcript-timeout SEC
                         Max time for machine WS transcript validation
                         after listener deployment (default: 420).
  --ws-transcript-optional
                         Record WS transcript evidence but do not fail the run
                         when no transcript payload arrives.
  --leave-running        Do not stop bots automatically at the end.
  -h, --help             Show this help.

Environment aliases:
  VEXA_GATEWAY_URL, VEXA_ADMIN_URL, VEXA_DASHBOARD_URL, VEXA_ADMIN_API_TOKEN.
  VEXA_TTS_SERVICE_URL, VEXA_ADMIN_DOCKER_CONTAINER, VEXA_TTS_DOCKER_CONTAINER.

Secrets are never printed. Generated evidence is stored under:
  .agents/skills/vexa-meeting-deployment-test/.runs/<run-id>/
EOF
}

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

json_get() {
  local body="$1" path="$2"
  python3 -c '
import json, sys
path = sys.argv[1].split(".")
try:
    data = json.loads(sys.stdin.read() or "{}")
    cur = data
    for key in path:
        if key == "":
            continue
        if isinstance(cur, list):
            cur = cur[int(key)]
        else:
            cur = cur.get(key)
        if cur is None:
            print("")
            raise SystemExit
    if isinstance(cur, (dict, list)):
        print(json.dumps(cur))
    else:
        print(cur)
except Exception:
    print("")
' "$path" <<<"$body"
}

json_escape_arg() {
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
}

request() {
  local method="$1" url="$2" token="${3:-}" body="${4:-}"
  local args=(-sS -w $'\n%{http_code}' -X "$method" "$url")
  if [ -n "$token" ]; then
    args+=(-H "X-API-Key: $token")
  fi
  if [ -n "$body" ]; then
    args+=(-H "Content-Type: application/json" -d "$body")
  fi
  local resp
  resp="$(curl "${args[@]}" 2>/dev/null || true)"
  HTTP_CODE="$(printf '%s\n' "$resp" | tail -n 1)"
  HTTP_BODY="$(printf '%s\n' "$resp" | sed '$d')"
}

gateway_ws_url() {
  python3 - "$GATEWAY_URL" <<'PY'
from urllib.parse import urlparse, urlunparse
import sys

base = sys.argv[1]
parsed = urlparse(base)
scheme = "wss" if parsed.scheme == "https" else "ws"
print(urlunparse((scheme, parsed.netloc, "/ws", "", "", "")))
PY
}

admin_request() {
  local method="$1" url="$2" body="${3:-}"
  local args=(-sS -w $'\n%{http_code}' -X "$method" "$url" -H "X-Admin-API-Key: $ADMIN_TOKEN")
  local resp
  if [ -n "$ADMIN_DOCKER_CONTAINER" ]; then
    need_cmd docker
    if [ -n "$body" ]; then
      resp="$(
        printf '%s' "$body" \
          | docker exec -i "$ADMIN_DOCKER_CONTAINER" curl "${args[@]}" \
              -H "Content-Type: application/json" --data-binary @- 2>/dev/null || true
      )"
    else
      resp="$(docker exec "$ADMIN_DOCKER_CONTAINER" curl "${args[@]}" 2>/dev/null || true)"
    fi
  else
    if [ -n "$body" ]; then
      args+=(-H "Content-Type: application/json" -d "$body")
    fi
    resp="$(curl "${args[@]}" 2>/dev/null || true)"
  fi
  HTTP_CODE="$(printf '%s\n' "$resp" | tail -n 1)"
  HTTP_BODY="$(printf '%s\n' "$resp" | sed '$d')"
}

discover_admin_token() {
  if [ -n "$ADMIN_TOKEN" ]; then
    return 0
  fi
  command -v docker >/dev/null 2>&1 || return 0
  local c token
  for c in vexa-admin-api-1 vexa vexa-lite; do
    token="$(
      docker inspect "$c" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
        | awk -F= '$1 == "ADMIN_API_TOKEN" {print substr($0, index($0, "=") + 1); exit}' \
        || true
    )"
    if [ -n "$token" ]; then
      ADMIN_TOKEN="$token"
      return 0
    fi
  done
}

parse_meeting_url() {
  local parsed
  parsed="$(python3 - "$MEETING_URL" <<'PY'
import hashlib
import re
import sys
from urllib.parse import parse_qs, urlparse

url = sys.argv[1].strip()
parsed = urlparse(url)
host = parsed.netloc.lower()
path = parsed.path
query = parse_qs(parsed.query)

if "meet.google.com" in host:
    m = re.search(r"/([a-z]{3}-[a-z]{4}-[a-z]{3})(?:$|[/?#])", path + "/")
    if not m:
        raise SystemExit("unsupported Google Meet URL")
    print("PLATFORM=google_meet")
    print(f"NATIVE_MEETING_ID={m.group(1)}")
elif "teams.microsoft.com" in host or host.endswith(".teams.microsoft.us"):
    m = re.match(r"^/meet/(\d{10,15})/?$", path)
    if m:
        print("PLATFORM=teams")
        print(f"NATIVE_MEETING_ID={m.group(1)}")
        p = (query.get("p") or [""])[0]
        if p:
            print(f"TEAMS_PASSCODE={p}")
    elif "/l/meetup-join/" in path:
        print("PLATFORM=teams")
        print(f"NATIVE_MEETING_ID={hashlib.sha256(url.encode()).hexdigest()[:16]}")
        print("TEAMS_MEETING_URL=1")
    else:
        raise SystemExit("unsupported Teams URL")
else:
    raise SystemExit("unsupported meeting URL")
PY
  )" || die "could not parse meeting URL: $MEETING_URL"
  eval "$parsed"
}

ensure_user_token() {
  local email="$1" name="$2" max_bots="$3" scopes="$4" token_name="$5"
  local body user_id token create_body

  admin_request GET "$ADMIN_URL/admin/users/email/$email"
  if [ "$HTTP_CODE" = "200" ]; then
    user_id="$(json_get "$HTTP_BODY" id)"
    body="{\"max_concurrent_bots\":$max_bots}"
    admin_request PATCH "$ADMIN_URL/admin/users/$user_id" "$body"
  else
    create_body="{\"email\":$(json_escape_arg "$email"),\"name\":$(json_escape_arg "$name"),\"max_concurrent_bots\":$max_bots}"
    admin_request POST "$ADMIN_URL/admin/users" "$create_body"
    case "$HTTP_CODE" in
      200|201) ;;
      *)
        admin_request GET "$ADMIN_URL/admin/users/email/$email"
        [ "$HTTP_CODE" = "200" ] || die "failed to create user $email (HTTP $HTTP_CODE)"
        ;;
    esac
    user_id="$(json_get "$HTTP_BODY" id)"
  fi

  [ -n "$user_id" ] || die "could not resolve user id for $email"

  admin_request POST "$ADMIN_URL/admin/users/$user_id/tokens?scopes=$scopes&name=$token_name"
  case "$HTTP_CODE" in
    200|201) ;;
    *) die "failed to create API token for $email (HTTP $HTTP_CODE)" ;;
  esac
  token="$(json_get "$HTTP_BODY" token)"
  [ -n "$token" ] || die "admin API did not return a token for $email"
  CREATED_TOKENS+=("$email")
  printf '%s' "$token"
}

configure_listener_webhook() {
  local secret body
  secret="$(openssl rand -hex 16 2>/dev/null || date -u '+%s')"
  body="$(
    python3 - "$WEBHOOK_URL" "$secret" <<'PY'
import json
import sys
url, secret = sys.argv[1:3]
print(json.dumps({
    "webhook_url": url,
    "webhook_secret": secret,
    "webhook_events": {
        "meeting.started": True,
        "meeting.status_change": True,
        "meeting.completed": True,
        "bot.failed": True,
        "recording.completed": True,
        "transcript.finalized": True,
    },
}))
PY
  )"
  request PUT "$GATEWAY_URL/user/webhook" "$LISTENER_TOKEN" "$body"
  case "$HTTP_CODE" in
    200) log "configured listener webhook target: $WEBHOOK_URL" ;;
    *) die "failed to configure listener webhook (HTTP $HTTP_CODE): $HTTP_BODY" ;;
  esac
}

create_bot_payload() {
  local name="$1" transcribe="$2" recording="$3" voice="$4"
  python3 - "$PLATFORM" "$NATIVE_MEETING_ID" "$MEETING_URL" "$name" "$transcribe" "$recording" "$voice" "${TEAMS_PASSCODE:-}" "${TEAMS_MEETING_URL:-}" <<'PY'
import json
import sys
platform, native_id, meeting_url, name, transcribe, recording, voice, passcode, teams_meeting_url = sys.argv[1:10]
payload = {
    "platform": platform,
    "native_meeting_id": native_id,
    "bot_name": name,
    "transcribe_enabled": transcribe == "true",
    "recording_enabled": recording == "true",
    "voice_agent_enabled": voice == "true",
    "camera_enabled": False,
    "automatic_leave": {
        "max_wait_for_admission": 600000,
        "max_time_left_alone": 600000,
        "max_bot_time": 1800000,
        "no_one_joined_timeout": 600000,
    },
}
if teams_meeting_url == "1":
    payload["meeting_url"] = meeting_url
if passcode:
    payload["passcode"] = passcode
print(json.dumps(payload))
PY
}

deploy_bot() {
  local token="$1" name="$2" transcribe="$3" recording="$4" voice="$5" payload bot_id
  payload="$(create_bot_payload "$name" "$transcribe" "$recording" "$voice")"
  request POST "$GATEWAY_URL/bots" "$token" "$payload"
  case "$HTTP_CODE" in
    200|201|202) ;;
    *) die "failed to create bot $name (HTTP $HTTP_CODE): $HTTP_BODY" ;;
  esac
  bot_id="$(json_get "$HTTP_BODY" id)"
  [ -n "$bot_id" ] || die "bot create for $name did not return an id"
  printf '%s' "$bot_id"
}

status_summary_for_token() {
  local token="$1"
  request GET "$GATEWAY_URL/bots/status" "$token"
  [ "$HTTP_CODE" = "200" ] || return 0
  python3 -c '
import json
import sys
platform, native_id = sys.argv[1:3]
try:
    data = json.load(sys.stdin)
except Exception:
    data = {}
for b in data.get("running_bots", []):
    if b.get("platform") == platform and b.get("native_meeting_id") == native_id:
        print(json.dumps({
            "id": b.get("id"),
            "name": b.get("bot_name") or b.get("name"),
            "status": b.get("meeting_status") or b.get("status"),
            "container": b.get("container_name") or b.get("bot_container_id"),
        }))
' "$PLATFORM" "$NATIVE_MEETING_ID" <<<"$HTTP_BODY"
}

wait_for_active_bots() {
  local total=$((1 + ${#SPEAKER_LABELS[@]}))
  local deadline=$((SECONDS + ACTIVE_TIMEOUT_SECONDS))
  local active line status seen
  log "waiting for $total bots to become active; admit listener-test-$CASE_ID, ${SPEAKER_NAMES[0]}-$CASE_ID, and ${SPEAKER_NAMES[1]}-$CASE_ID if the meeting shows a lobby"
  while [ "$SECONDS" -lt "$deadline" ]; do
    active=0
    seen=0
    : >"$RUN_DIR/status-latest.jsonl"
    for line in "$(status_summary_for_token "$LISTENER_TOKEN")"; do
      [ -n "$line" ] || continue
      printf '%s\n' "$line" >>"$RUN_DIR/status-latest.jsonl"
    done
    for token in "${SPEAKER_TOKENS[@]}"; do
      while IFS= read -r line; do
        [ -n "$line" ] || continue
        printf '%s\n' "$line" >>"$RUN_DIR/status-latest.jsonl"
      done < <(status_summary_for_token "$token")
    done
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      seen=$((seen + 1))
      status="$(json_get "$line" status)"
      [ "$status" = "active" ] && active=$((active + 1))
    done <"$RUN_DIR/status-latest.jsonl"
    log "bot status: $active/$total active ($seen visible to their owning tokens)"
    [ "$active" -ge "$total" ] && return 0
    sleep 5
  done
  return 1
}

load_turns() {
  python3 - "$GROUND_TRUTH_FILE" "$CASE_ID" "$RUN_ID" <<'PY'
import re
import sys

path, case_id, run_id = sys.argv[1:4]
with open(path, encoding="utf-8") as f:
    for line in f:
        m = re.match(r"\s*\d+\.\s+`([^`]+)`:\s+(.*)", line)
        if not m:
            continue
        meta = [part.strip() for part in m.group(1).split("|")]
        speaker = meta[0]
        name = meta[1] if len(meta) > 1 and meta[1] else speaker
        voice = meta[2] if len(meta) > 2 and meta[2] else ""
        text = m.group(2).replace("CASE_ID", case_id).replace("RUN_ID", run_id)
        print(f"{speaker}\t{name}\t{voice}\t{text}")
PY
}

turn_pause_for_text() {
  local text="$1"
  if [ "$TURN_PAUSE_SECONDS" != "auto" ]; then
    printf '%s' "$TURN_PAUSE_SECONDS"
    return 0
  fi
  python3 - "$text" "$MIN_TURN_PAUSE_SECONDS" "$MAX_TURN_PAUSE_SECONDS" <<'PY'
import math
import re
import sys

text, min_pause, max_pause = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
word_count = max(1, len(re.findall(r"\S+", text)))
char_count = max(1, len(text))
estimate = max(word_count * 0.58, char_count / 13.0) + 4.0
print(int(max(min_pause, min(max_pause, math.ceil(estimate)))))
PY
}

prewarm_tts_voices() {
  local voice voices_json body code
  : >"$RUN_DIR/tts-prewarm.jsonl"
  voices_json="$(
    {
      printf '%s\n' "${SPEAKER_DEFAULT_VOICES[@]}"
      load_turns | awk -F'\t' '{if ($3 != "") print $3}'
    } | sort -u | python3 -c 'import json,sys; print(json.dumps([line.strip() for line in sys.stdin if line.strip()]))'
  )"
  python3 - "$voices_json" <<'PY' | while IFS= read -r voice; do
import json
import sys
for voice in json.loads(sys.argv[1]):
    print(voice)
PY
    body="$(python3 - "$voice" <<'PY'
import json
import sys
print(json.dumps({
    "model": "tts-1",
    "input": "Vexa voice warmup.",
    "voice": sys.argv[1],
    "response_format": "pcm",
}))
PY
)"
    if [ -n "$TTS_DOCKER_CONTAINER" ]; then
      need_cmd docker
      code="$(
        printf '%s' "$body" \
          | docker exec -i "$TTS_DOCKER_CONTAINER" curl -sS -o /dev/null -w '%{http_code}' \
              -X POST "$TTS_SERVICE_URL/v1/audio/speech" \
              -H "Content-Type: application/json" \
              --data-binary @- 2>/dev/null || true
      )"
    else
      code="$(
        curl -sS -o /dev/null -w '%{http_code}' \
          -X POST "$TTS_SERVICE_URL/v1/audio/speech" \
          -H "Content-Type: application/json" \
          -d "$body" 2>/dev/null || true
      )"
    fi
    python3 - "$voice" "$code" <<'PY' >>"$RUN_DIR/tts-prewarm.jsonl"
import json
import sys
print(json.dumps({"voice": sys.argv[1], "http_code": sys.argv[2]}))
PY
    if [ "$code" = "200" ]; then
      log "prewarmed TTS voice: $voice"
    else
      log "TTS voice warmup skipped/failed for $voice (HTTP ${code:-curl-error})"
    fi
  done
}

speak_turns() {
  local turn speaker speaker_name voice text token code sent=0 speaker_index pause
  : >"$RUN_DIR/speak-events.jsonl"
  while IFS=$'\t' read -r speaker speaker_name voice text; do
    case "$speaker" in
      speaker-1) speaker_index=0; token="${SPEAKER_TOKENS[0]}" ;;
      speaker-2) speaker_index=1; token="${SPEAKER_TOKENS[1]}" ;;
      *) die "unsupported speaker in ground truth: $speaker" ;;
    esac
    if [ -z "$speaker_name" ]; then
      speaker_name="${SPEAKER_NAMES[$speaker_index]}"
    fi
    if [ -z "$voice" ]; then
      voice="${SPEAKER_DEFAULT_VOICES[$speaker_index]}"
    fi
    local body
    body="$(python3 - "$text" "$voice" <<'PY'
import json
import sys
print(json.dumps({"text": sys.argv[1], "provider": "piper", "voice": sys.argv[2]}))
PY
)"
    request POST "$GATEWAY_URL/bots/$PLATFORM/$NATIVE_MEETING_ID/speak" "$token" "$body"
    code="$HTTP_CODE"
    python3 - "$speaker" "$speaker_name" "$voice" "$code" "$text" "$HTTP_BODY" <<'PY' >>"$RUN_DIR/speak-events.jsonl"
import json
import sys
speaker, speaker_name, voice, code, text, body = sys.argv[1:7]
shape = {}
try:
    parsed = json.loads(body or "{}")
    shape = {k: parsed.get(k) for k in parsed.keys() if k not in {"token", "secret"}}
except Exception:
    shape = {"raw_length": len(body or "")}
print(json.dumps({
    "at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    "speaker": speaker,
    "speaker_name": speaker_name,
    "voice": voice,
    "http_code": code,
    "text_prefix": text[:80],
    "response": shape,
}))
PY
    pause="$(turn_pause_for_text "$text")"
    if [ "$code" = "200" ] || [ "$code" = "202" ]; then
      sent=$((sent + 1))
      log "sent TTS turn $sent: $speaker_name ($speaker, voice=$voice); waiting ${pause}s"
    else
      log "TTS turn failed for $speaker (HTTP $code)"
    fi
    sleep "$pause"
  done < <(load_turns)
  log "sent $sent scripted TTS turns"
}

start_ws_transcript_probe() {
  local ws_url
  ws_url="$(gateway_ws_url)"
  log "starting machine WS transcript probe for $PLATFORM/$NATIVE_MEETING_ID meeting_id=$LISTENER_BOT_ID"
  python3 - "$ws_url" "$LISTENER_TOKEN" "$PLATFORM" "$NATIVE_MEETING_ID" "$LISTENER_BOT_ID" "$WS_TRANSCRIPT_TIMEOUT_SECONDS" "$RUN_DIR/ws-transcript-events.jsonl" "$RUN_DIR/ws-transcript-summary.json" <<'PY' &
import asyncio
import json
import sys
import time
from urllib.parse import urlencode

import websockets

ws_url, token, platform, native_id, meeting_id, timeout_s, events_path, summary_path = sys.argv[1:9]
timeout_s = float(timeout_s)
if "?" in ws_url:
    ws_url = f"{ws_url}&{urlencode({'api_key': token})}"
else:
    ws_url = f"{ws_url}?{urlencode({'api_key': token})}"

def emit(path, item):
    item = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **item}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, sort_keys=True) + "\n")

def text_count(message):
    count = 0
    if not isinstance(message, dict):
        return 0
    candidate_lists = []
    for key in ("confirmed", "pending", "segments"):
        value = message.get(key)
        if isinstance(value, list):
            candidate_lists.append(value)
    payload = message.get("payload")
    if isinstance(payload, dict):
        for key in ("confirmed", "pending", "segments"):
            value = payload.get(key)
            if isinstance(value, list):
                candidate_lists.append(value)
    for items in candidate_lists:
        for seg in items:
            if isinstance(seg, dict) and str(seg.get("text") or "").strip():
                count += 1
    if not candidate_lists and str(message.get("text") or "").strip():
        count += 1
    return count

async def main():
    subscribe = {
        "action": "subscribe",
        "meetings": [{
            "platform": platform,
            "native_id": native_id,
            "meeting_id": int(meeting_id),
        }],
    }
    summary = {
        "platform": platform,
        "native_id": native_id,
        "meeting_id": meeting_id,
        "status": "not_validated",
        "transcript_messages": 0,
        "text_segments_seen": 0,
        "subscribed": False,
    }
    try:
        emit(events_path, {"event": "connect", "ws_url": ws_url.split("?", 1)[0], "meeting_id": meeting_id})
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps(subscribe))
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                remaining = max(0.1, deadline - time.monotonic())
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(30.0, remaining))
                except asyncio.TimeoutError:
                    emit(events_path, {"event": "wait", "seconds": 30})
                    continue
                try:
                    message = json.loads(raw)
                except Exception:
                    emit(events_path, {"event": "message", "parse_error": True, "raw_length": len(raw)})
                    continue
                mtype = message.get("type")
                segments = text_count(message)
                emit(events_path, {
                    "event": "message",
                    "type": mtype,
                    "text_segments": segments,
                    "meeting_id": meeting_id,
                    "message": message,
                })
                if mtype == "subscribed":
                    summary["subscribed"] = True
                    summary["subscription"] = message.get("meetings")
                if mtype in {"transcript", "transcript.mutable", "transcript.finalized"}:
                    summary["transcript_messages"] += 1
                    summary["text_segments_seen"] += segments
                    if segments > 0:
                        summary["status"] = "validated"
                        summary["validated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                        with open(summary_path, "w", encoding="utf-8") as f:
                            json.dump(summary, f, indent=2, sort_keys=True)
                        return 0
        summary["status"] = "no_transcript_message"
        summary["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, sort_keys=True)
        return 2
    except Exception as exc:
        summary["status"] = "probe_error"
        summary["error"] = str(exc)
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, sort_keys=True)
        emit(events_path, {"event": "error", "error": str(exc)})
        return 3

raise SystemExit(asyncio.run(main()))
PY
  WS_WATCH_PID="$!"
}

wait_for_ws_transcript_probe() {
  [ -n "$WS_WATCH_PID" ] || return 0
  if wait "$WS_WATCH_PID"; then
    WS_TRANSCRIPT_VALIDATED=1
    log "machine WS transcript probe validated transcript delivery"
  else
    WS_TRANSCRIPT_VALIDATED=0
    log "machine WS transcript probe did not observe transcript delivery"
  fi
  WS_WATCH_PID=""
}

prompt_human_checks() {
  local listener_dashboard_url="${DASHBOARD_URL%/}/meetings/$LISTENER_BOT_ID"
  log "dashboard: $listener_dashboard_url"
  log "listener bot id: $LISTENER_BOT_ID; meeting: $PLATFORM/$NATIVE_MEETING_ID"
  cat >"$RUN_DIR/human-eyeball-request.md" <<EOF
Human eyeball checkpoint for $PLATFORM / $CASE_ID

- Dashboard URL: $listener_dashboard_url
- Meeting UI: did the listener bot and both speaker bots appear?
- Bot names: are the speakers visibly named Maya Chen and Leo Santos?
- Audio: did you hear the speaker bots?
- Voices: did Maya and Leo sound distinct?
- Multilingual: were Spanish, French, and Portuguese checkpoints audible?
- Speaker labels: does the dashboard visibly distinguish Maya and Leo?
- Errors: any denial, mute, duplicate bot, stuck state, empty UI, or visible error?
- Notes: anything else you saw or heard?
EOF
  printf '\nHuman eyeball checkpoint for %s / %s\n' "$PLATFORM" "$CASE_ID"
  cat "$RUN_DIR/human-eyeball-request.md"
  printf '\n'
  if [ -t 0 ]; then
    local bots_visible names_ok heard distinct_voices multilingual speaker_labels visible_errors notes
    printf 'Did the listener and speaker bots appear in the meeting? [y/N/skip] '
    read -r bots_visible || bots_visible=""
    printf 'Were the speaker bots visibly named Maya Chen and Leo Santos for %s? [y/N/skip] ' "$CASE_ID"
    read -r names_ok || names_ok=""
    printf 'Did you hear the speaker bots talking in the meeting? [y/N/skip] '
    read -r heard || heard=""
    printf 'Did Maya and Leo sound like distinct voices? [y/N/skip] '
    read -r distinct_voices || distinct_voices=""
    printf 'Did you hear multilingual checkpoints (Spanish/French/Portuguese)? [y/N/skip] '
    read -r multilingual || multilingual=""
    printf 'Do dashboard speaker labels visibly distinguish Maya and Leo? [y/N/skip] '
    read -r speaker_labels || speaker_labels=""
    printf 'Any visible meeting/dashboard errors, denials, stuck states, or duplicate bots? [y/N/skip] '
    read -r visible_errors || visible_errors=""
    printf 'Short human observation notes for %s (optional): ' "$CASE_ID"
    read -r notes || notes=""
    {
      printf 'bots_visible=%s\n' "$bots_visible"
      printf 'expected_names_visible=%s\n' "$names_ok"
      printf 'heard_audio=%s\n' "$heard"
      printf 'distinct_voices=%s\n' "$distinct_voices"
      printf 'multilingual_checkpoints_audible=%s\n' "$multilingual"
      printf 'speaker_labels_visible=%s\n' "$speaker_labels"
      printf 'visible_errors=%s\n' "$visible_errors"
      printf 'notes=%s\n' "$notes"
      printf 'confirmed_at=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    } >"$RUN_DIR/human-confirmation.env"
  else
    log "non-interactive shell: human audio/dashboard confirmations left as unknown"
  fi
}

prompt_playback_check() {
  local listener_dashboard_url="${DASHBOARD_URL%/}/meetings/$LISTENER_BOT_ID"
  cat >"$RUN_DIR/playback-eyeball-request.md" <<EOF
Post-stop playback checkpoint for $PLATFORM / $CASE_ID

- Dashboard URL: $listener_dashboard_url
- Artifact: is a recording or transcript artifact visible?
- Playback: can playback or artifact viewing start?
- Match: if playback starts, does it match the meeting audio/content?
- Processing/errors: is it processing, empty, or erroring?
- Notes: anything else visible?
EOF
  printf '\nPost-stop playback checkpoint for %s / %s\n' "$PLATFORM" "$CASE_ID"
  cat "$RUN_DIR/playback-eyeball-request.md"
  printf '\n'
  if [ -t 0 ]; then
    local artifact_visible playback_started playback_matches playback_notes
    printf 'After stop, is a recording or transcript artifact visible for %s? [y/N/processing/skip] ' "$CASE_ID"
    read -r artifact_visible || artifact_visible=""
    printf 'Can playback or artifact viewing start for %s? [y/N/processing/skip] ' "$CASE_ID"
    read -r playback_started || playback_started=""
    printf 'If playback started, does it match the meeting audio/content? [y/N/skip] '
    read -r playback_matches || playback_matches=""
    printf 'Playback/artifact notes for %s (optional): ' "$CASE_ID"
    read -r playback_notes || playback_notes=""
    {
      printf 'artifact_visible=%s\n' "$artifact_visible"
      printf 'playback_started=%s\n' "$playback_started"
      printf 'playback_matches_meeting=%s\n' "$playback_matches"
      printf 'playback_notes=%s\n' "$playback_notes"
      printf 'playback_checked_at=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    } >"$RUN_DIR/playback-confirmation.env"
  else
    log "non-interactive shell: playback/artifact observations left as unknown"
  fi
}

fetch_and_score_transcript() {
  log "waiting ${POST_SPEECH_WAIT_SECONDS}s for transcription pipeline"
  sleep "$POST_SPEECH_WAIT_SECONDS"
  request GET "$GATEWAY_URL/transcripts/$PLATFORM/$NATIVE_MEETING_ID" "$LISTENER_TOKEN"
  printf '%s\n' "$HTTP_BODY" >"$RUN_DIR/transcript.json"
  if [ "$HTTP_CODE" != "200" ]; then
    log "transcript fetch failed (HTTP $HTTP_CODE)"
    return 0
  fi
  python3 - "$RUN_DIR/transcript.json" "$GROUND_TRUTH_FILE" "$CASE_ID" "$RUN_ID" <<'PY' >"$RUN_DIR/score.json"
import json
import re
import sys

transcript_path, truth_path, case_id, run_id = sys.argv[1:5]
with open(transcript_path, encoding="utf-8") as f:
    data = json.load(f)
segments = data.get("segments", data if isinstance(data, list) else [])
texts = " ".join((s.get("text") or "") for s in segments).lower()
speakers = [s.get("speaker") for s in segments if s.get("speaker")]
with open(truth_path, encoding="utf-8") as f:
    truth = f.read().replace("CASE_ID", case_id).replace("RUN_ID", run_id)
anchors_match = re.search(r"Key anchors:\s*(.*)", truth)
anchors = []
if anchors_match:
    anchors = [a.strip(" `.") for a in anchors_match.group(1).split(",")]
matched = [a for a in anchors if a.lower() in texts]
turns = re.findall(r"^\s*\d+\.\s+`([^`]+)`:\s+(.*)$", truth, re.M)
print(json.dumps({
    "segment_count": len(segments),
    "expected_turns": len(turns),
    "key_anchors_total": len(anchors),
    "key_anchors_matched": len(matched),
    "matched_anchors": matched,
    "speaker_labels_seen": sorted(set(speakers)),
    "speaker_label_count": len(set(speakers)),
    "content_accuracy_summary": f"{len(matched)}/{len(anchors)} key anchors matched",
    "speaker_identification_summary": "unavailable" if not speakers else f"{len(set(speakers))} distinct transcript speaker labels seen",
}, indent=2))
PY
  log "wrote transcript and score evidence to $RUN_DIR"
}

fetch_listener_meeting_evidence() {
  if [ -z "$LISTENER_BOT_ID" ]; then
    return 0
  fi
  request GET "$GATEWAY_URL/bots/id/$LISTENER_BOT_ID" "$LISTENER_TOKEN"
  printf '%s\n' "$HTTP_BODY" >"$RUN_DIR/listener-meeting.json"
  if [ "$HTTP_CODE" = "200" ]; then
    python3 - "$RUN_DIR/listener-meeting.json" <<'PY' >"$RUN_DIR/webhook-summary.json"
import json
import sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        meeting = json.load(f)
except Exception:
    meeting = {}
data = meeting.get("data") or {}
deliveries = data.get("webhook_deliveries") or []
completion = data.get("webhook_delivery") or {}
recording = data.get("recording") or data.get("recording_metadata") or {}
recordings = data.get("recordings") or []
if isinstance(recordings, list) and recordings:
    recording = recordings[0]
recording_status = (
    data.get("recording_status")
    or recording.get("status")
    or data.get("recordingState")
    or data.get("recording_state")
)
recording_id = data.get("recording_id") or recording.get("id") or recording.get("recording_id")
playback_url = data.get("playback_url") or data.get("recording_url") or data.get("master_url")
if not playback_url:
    playback_url = recording.get("playback_url") or recording.get("recording_url") or recording.get("master_url")
if isinstance(playback_url, dict):
    playback_url = playback_url.get("audio") or playback_url.get("video")
print(json.dumps({
    "meeting_status": meeting.get("status") or data.get("status"),
    "webhook_target_configured": bool(data.get("webhook_url")),
    "status_delivery_count": len(deliveries),
    "status_events": sorted({d.get("event_type", "") for d in deliveries if d.get("event_type")}),
    "completion_status": completion.get("status"),
    "completion_http_status": completion.get("http_status"),
    "retry_count": completion.get("retry_count"),
    "recording_count": len(recordings) if isinstance(recordings, list) else None,
    "recording_status": recording_status,
    "recording_id_present": bool(recording_id),
    "playback_or_master_url_present": bool(playback_url),
}, indent=2))
PY
  fi
}

score_telemetry() {
  "$SKILL_DIR/scripts/score-telemetry.py" \
    "$RUN_DIR" \
    --ground-truth "$GROUND_TRUTH_FILE" \
    --case-id "$CASE_ID" \
    --run-id "$RUN_ID" >"$RUN_DIR/telemetry-score.stdout.json" || true
  if [ -f "$RUN_DIR/telemetry-score.json" ]; then
    log "wrote telemetry score evidence to $RUN_DIR/telemetry-score.json"
  fi
}

stop_bots() {
  [ "$CLEANUP_DONE" = "0" ] || return 0
  if [ "$LEAVE_RUNNING" = "1" ]; then
    log "leave-running set; bots were not stopped"
    CLEANUP_DONE=1
    return 0
  fi
  log "stopping speaker bots and listener bot"
  local token
  for token in "${SPEAKER_TOKENS[@]}"; do
    request DELETE "$GATEWAY_URL/bots/$PLATFORM/$NATIVE_MEETING_ID" "$token" || true
  done
  if [ -n "$LISTENER_TOKEN" ]; then
    request DELETE "$GATEWAY_URL/bots/$PLATFORM/$NATIVE_MEETING_ID" "$LISTENER_TOKEN" || true
  fi
  CLEANUP_DONE=1
}

write_run_summary() {
  python3 - "$RUN_DIR" "$RUN_ID" "$CASE_ID" "$PLATFORM" "$NATIVE_MEETING_ID" "$GATEWAY_URL" "$DASHBOARD_URL" "$LISTENER_BOT_ID" "${SPEAKER_BOT_IDS[*]:-}" "$LEAVE_RUNNING" <<'PY' >"$RUN_DIR/run-summary.json"
import json
import os
import sys
run_dir, run_id, case_id, platform, native_id, gateway, dashboard, listener_bot_id, speaker_bot_ids, leave_running = sys.argv[1:11]
def load(name):
    path = os.path.join(run_dir, name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None
print(json.dumps({
    "run_id": run_id,
    "case_id": case_id,
    "platform": platform,
    "native_meeting_id": native_id,
    "gateway_url": gateway,
    "dashboard_url": dashboard,
    "listener_bot_id": listener_bot_id,
    "speaker_bot_ids": [x for x in speaker_bot_ids.split() if x],
    "left_running": leave_running == "1",
    "score": load("score.json"),
    "telemetry_score": load("telemetry-score.json"),
    "ws_transcript_summary": load("ws-transcript-summary.json"),
    "webhook_summary": load("webhook-summary.json"),
}, indent=2))
PY
}

cleanup_on_exit() {
  local rc=$?
  if [ "$rc" != "0" ]; then
    stop_bots || true
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --meeting-url) MEETING_URL="${2:-}"; shift 2 ;;
    --case-id) CASE_ID="${2:-}"; shift 2 ;;
    --run-id) RUN_ID="${2:-}"; shift 2 ;;
    --gateway-url) GATEWAY_URL="${2:-}"; shift 2 ;;
    --admin-url) ADMIN_URL="${2:-}"; shift 2 ;;
    --dashboard-url) DASHBOARD_URL="${2:-}"; shift 2 ;;
    --tts-service-url) TTS_SERVICE_URL="${2:-}"; shift 2 ;;
    --admin-docker-container) ADMIN_DOCKER_CONTAINER="${2:-}"; shift 2 ;;
    --tts-docker-container) TTS_DOCKER_CONTAINER="${2:-}"; shift 2 ;;
    --admin-token) ADMIN_TOKEN="${2:-}"; shift 2 ;;
    --listener-email) LISTENER_EMAIL="${2:-}"; shift 2 ;;
    --turn-pause) TURN_PAUSE_SECONDS="${2:-}"; shift 2 ;;
    --active-timeout) ACTIVE_TIMEOUT_SECONDS="${2:-}"; shift 2 ;;
  --post-stop-wait) POST_STOP_WAIT_SECONDS="${2:-}"; shift 2 ;;
    --ws-transcript-timeout) WS_TRANSCRIPT_TIMEOUT_SECONDS="${2:-}"; shift 2 ;;
    --ws-transcript-optional) WS_TRANSCRIPT_REQUIRED=0; shift ;;
    --leave-running) LEAVE_RUNNING=1; shift ;;
    -h|--help) usage; exit 0 ;;
    --*) die "unknown option: $1" ;;
    *)
      if [ -z "$MEETING_URL" ]; then
        MEETING_URL="$1"
        shift
      else
        die "unexpected positional argument: $1"
      fi
      ;;
  esac
done

need_cmd curl
need_cmd python3
[ -f "$GROUND_TRUTH_FILE" ] || die "missing ground truth file: $GROUND_TRUTH_FILE"
[ -n "$MEETING_URL" ] || { usage; exit 2; }

discover_admin_token
[ -n "$ADMIN_TOKEN" ] || die "admin token is required; pass --admin-token or set VEXA_ADMIN_API_TOKEN"
parse_meeting_url

RUN_DIR="$RUN_ROOT/$RUN_ID"
mkdir -p "$RUN_DIR"
chmod 700 "$RUN_ROOT" "$RUN_DIR" 2>/dev/null || true
trap cleanup_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

log "run: $RUN_ID ($CASE_ID)"
log "deployment: gateway=$GATEWAY_URL admin=$ADMIN_URL dashboard=$DASHBOARD_URL tts=$TTS_SERVICE_URL"
log "meeting: $PLATFORM/$NATIVE_MEETING_ID"

LISTENER_TOKEN="$(ensure_user_token "$LISTENER_EMAIL" "listener-test" 5 "bot,tx" "listener-$RUN_ID")"
configure_listener_webhook
prewarm_tts_voices

for i in "${!SPEAKER_LABELS[@]}"; do
  label="${SPEAKER_LABELS[$i]}"
  email="${label}@vexa.ai"
  token="$(ensure_user_token "$email" "${SPEAKER_NAMES[$i]}" 3 "bot" "$label-$RUN_ID")"
  SPEAKER_TOKENS+=("$token")
done

LISTENER_BOT_ID="$(deploy_bot "$LISTENER_TOKEN" "listener-test-$CASE_ID" true true false)"
log "listener deployed: bot_id=$LISTENER_BOT_ID"
for i in "${!SPEAKER_LABELS[@]}"; do
  bot_id="$(deploy_bot "${SPEAKER_TOKENS[$i]}" "${SPEAKER_NAMES[$i]}-$CASE_ID" false false true)"
  SPEAKER_BOT_IDS+=("$bot_id")
  log "speaker deployed: ${SPEAKER_NAMES[$i]} (${SPEAKER_LABELS[$i]}) bot_id=$bot_id"
done

cat >"$RUN_DIR/evidence.env" <<EOF
run_id=$RUN_ID
case_id=$CASE_ID
platform=$PLATFORM
native_meeting_id=$NATIVE_MEETING_ID
meeting_url=$MEETING_URL
gateway_url=$GATEWAY_URL
admin_url=$ADMIN_URL
dashboard_url=$DASHBOARD_URL
tts_service_url=$TTS_SERVICE_URL
admin_docker_container=${ADMIN_DOCKER_CONTAINER:-}
tts_docker_container=${TTS_DOCKER_CONTAINER:-}
listener_email=$LISTENER_EMAIL
listener_bot_id=$LISTENER_BOT_ID
speaker_names=${SPEAKER_NAMES[*]}
speaker_bot_ids=${SPEAKER_BOT_IDS[*]}
webhook_url=$WEBHOOK_URL
EOF

if ! wait_for_active_bots; then
  log "not all bots became active before timeout; check $RUN_DIR/status-latest.jsonl"
  stop_bots
  write_run_summary
  exit 1
fi

start_ws_transcript_probe
speak_turns
prompt_human_checks
wait_for_ws_transcript_probe
fetch_and_score_transcript
fetch_listener_meeting_evidence
stop_bots
if [ "$LEAVE_RUNNING" != "1" ]; then
  log "waiting ${POST_STOP_WAIT_SECONDS}s for final status, webhook delivery, and recording metadata"
  sleep "$POST_STOP_WAIT_SECONDS"
  fetch_listener_meeting_evidence
fi
prompt_playback_check
score_telemetry
write_run_summary

log "complete; evidence: $RUN_DIR"
log "summary: $RUN_DIR/run-summary.json"
if [ "$WS_TRANSCRIPT_REQUIRED" = "1" ] && [ "$WS_TRANSCRIPT_VALIDATED" != "1" ]; then
  log "WS transcript validation is required and did not pass"
  exit 1
fi
