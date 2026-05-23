#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  debug-dashboard-meeting.sh --meeting-id ID [options]

No-rebuild release debug packet for dashboard meeting regressions. It only runs
read-only probes and writes redacted summaries.

Options:
  --dashboard-url URL   Dashboard URL, default http://localhost:3001
  --meeting-id ID       Dashboard/API meeting id to inspect
  --platform NAME       Optional platform override, e.g. teams or google_meet
  --native-id ID        Optional native meeting id override
  --auth-token TOKEN    Optional dashboard auth token for cookie-authenticated probes
  --auth-cookie-name N  Optional auth cookie name; defaults to /api/config value
  --out DIR             Evidence directory
  --skip-docker         Skip docker ps container snapshot
  -h, --help            Show this help
USAGE
}

DASHBOARD_URL="${DASHBOARD_URL:-http://localhost:3001}"
MEETING_ID="${MEETING_ID:-}"
PLATFORM="${PLATFORM:-}"
NATIVE_ID="${NATIVE_ID:-}"
AUTH_TOKEN="${DASHBOARD_AUTH_TOKEN:-}"
AUTH_COOKIE_NAME="${AUTH_COOKIE_NAME:-}"
OUT_DIR="${OUT_DIR:-}"
SKIP_DOCKER=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dashboard-url) DASHBOARD_URL="${2:-}"; shift 2 ;;
    --meeting-id) MEETING_ID="${2:-}"; shift 2 ;;
    --platform) PLATFORM="${2:-}"; shift 2 ;;
    --native-id) NATIVE_ID="${2:-}"; shift 2 ;;
    --auth-token) AUTH_TOKEN="${2:-}"; shift 2 ;;
    --auth-cookie-name) AUTH_COOKIE_NAME="${2:-}"; shift 2 ;;
    --out) OUT_DIR="${2:-}"; shift 2 ;;
    --skip-docker) SKIP_DOCKER=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$MEETING_ID" ]; then
  echo "ERROR: --meeting-id is required" >&2
  usage >&2
  exit 2
fi

DASHBOARD_URL="${DASHBOARD_URL%/}"
if [ -z "$OUT_DIR" ]; then
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  OUT_DIR=".agents/releases/unknown/debug/no-rebuild-${ts}-meeting-${MEETING_ID}"
fi
mkdir -p "$OUT_DIR"

raw_config="$OUT_DIR/raw-config.json"
raw_meeting="$OUT_DIR/raw-meeting.json"
raw_transcript="$OUT_DIR/raw-transcript.json"
summary="$OUT_DIR/summary.json"
containers="$OUT_DIR/containers.txt"

http_get() {
  local url="$1"
  local out="$2"
  local code_file="$3"
  local code
  local curl_args=(-sS -L --max-time 15)
  if [ -n "$AUTH_TOKEN" ] && [ -n "$AUTH_COOKIE_NAME" ]; then
    curl_args+=(-H "Cookie: ${AUTH_COOKIE_NAME}=${AUTH_TOKEN}")
  fi
  code="$(curl "${curl_args[@]}" -o "$out" -w '%{http_code}' "$url" || true)"
  printf '%s' "$code" > "$code_file"
}

http_get "$DASHBOARD_URL/api/config" "$raw_config" "$OUT_DIR/config.http"
if [ -n "$AUTH_TOKEN" ] && [ -z "$AUTH_COOKIE_NAME" ]; then
  AUTH_COOKIE_NAME="$(python3 - "$raw_config" <<'PY'
import json
import sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    data = {}
print(data.get("authCookieName") or "vexa-token")
PY
)"
  http_get "$DASHBOARD_URL/api/config" "$raw_config" "$OUT_DIR/config.http"
fi
http_get "$DASHBOARD_URL/api/auth/me" "$OUT_DIR/auth-me.raw" "$OUT_DIR/auth-me.http"
http_get "$DASHBOARD_URL/api/vexa/meetings/$MEETING_ID" "$raw_meeting" "$OUT_DIR/meeting.http"

python3 - "$raw_config" "$raw_meeting" "$OUT_DIR/config.http" "$OUT_DIR/meeting.http" "$OUT_DIR/auth-me.http" "$PLATFORM" "$NATIVE_ID" "$AUTH_COOKIE_NAME" > "$OUT_DIR/probe-env.json" <<'PY'
import json
import sys
from pathlib import Path

raw_config, raw_meeting, config_http, meeting_http, auth_http, platform_arg, native_arg, auth_cookie_name = sys.argv[1:9]

def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None

def read(path):
    try:
        return Path(path).read_text().strip()
    except Exception:
        return ""

config = load_json(raw_config) or {}
meeting = load_json(raw_meeting) or {}
platform = platform_arg or meeting.get("platform") or ""
native_id = native_arg or meeting.get("native_meeting_id") or ""

print(json.dumps({
    "config_http": read(config_http),
    "auth_http": read(auth_http),
    "meeting_http": read(meeting_http),
    "dashboard": {
        "apiUrl": config.get("apiUrl"),
        "publicApiUrl": config.get("publicApiUrl"),
        "wsUrl": config.get("wsUrl"),
        "authToken_present": bool(config.get("authToken")),
        "authCookieName": config.get("authCookieName") or auth_cookie_name or None,
        "version": config.get("version"),
    },
    "meeting": {
        "id": meeting.get("id"),
        "status": meeting.get("status"),
        "platform": platform,
        "native_meeting_id": native_id,
        "start_time": meeting.get("start_time"),
        "end_time": meeting.get("end_time"),
    },
}, indent=2))
PY

PLATFORM="$(python3 -c 'import json,sys; print((json.load(open(sys.argv[1]))["meeting"].get("platform") or ""))' "$OUT_DIR/probe-env.json")"
NATIVE_ID="$(python3 -c 'import json,sys; print((json.load(open(sys.argv[1]))["meeting"].get("native_meeting_id") or ""))' "$OUT_DIR/probe-env.json")"

if [ -n "$PLATFORM" ] && [ -n "$NATIVE_ID" ]; then
  http_get "$DASHBOARD_URL/api/vexa/transcripts/$PLATFORM/$NATIVE_ID?meeting_id=$MEETING_ID" "$raw_transcript" "$OUT_DIR/transcript.http"
else
  printf '000' > "$OUT_DIR/transcript.http"
  printf '{}' > "$raw_transcript"
fi

http_get "$DASHBOARD_URL/meetings/$MEETING_ID" "$OUT_DIR/meeting-page.html" "$OUT_DIR/meeting-page.http"

if [ "$SKIP_DOCKER" -eq 0 ] && command -v docker >/dev/null 2>&1; then
  docker ps --format '{{.Names}} {{.Image}} {{.Status}} {{.Ports}}' \
    | grep -E 'vexa|dashboard|api-gateway|meeting-api|lite' > "$containers" || true
else
  : > "$containers"
fi

python3 - "$OUT_DIR/probe-env.json" "$raw_transcript" "$OUT_DIR/transcript.http" "$OUT_DIR/meeting-page.http" "$OUT_DIR/meeting-page.html" "$containers" "$summary" <<'PY'
import json
import re
import sys
from pathlib import Path

env_path, transcript_path, transcript_http_path, page_http_path, page_path, containers_path, summary_path = sys.argv[1:8]

def read(path):
    try:
        return Path(path).read_text()
    except Exception:
        return ""

def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}

env = load_json(env_path)
transcript = load_json(transcript_path)
segments = transcript.get("segments") if isinstance(transcript, dict) else None
segments = segments if isinstance(segments, list) else []
html = read(page_path)

config_http = env.get("config_http")
auth_http = env.get("auth_http")
meeting_http = env.get("meeting_http")
transcript_http = read(transcript_http_path).strip()
page_http = read(page_http_path).strip()

if config_http != "200":
    classification = "config-layer"
elif meeting_http != "200":
    classification = "meeting-route-layer"
elif transcript_http != "200":
    classification = "transcript-route-layer"
elif len(segments) == 0:
    classification = "transcript-data-layer"
elif auth_http != "200":
    classification = "shell-auth-caveat-data-ok"
else:
    classification = "running-candidate-data-ok"

result = {
    "classification": classification,
    "no_rebuild_decision": (
        "do_not_rebuild; inspect browser DOM/client state next"
        if classification in {"running-candidate-data-ok", "shell-auth-caveat-data-ok"}
        else "do_not_rebuild; fix or inspect classified layer first"
    ),
    "http": {
        "config": config_http,
        "auth_me": auth_http,
        "meeting": meeting_http,
        "transcript": transcript_http,
        "meeting_page": page_http,
    },
    "dashboard": env.get("dashboard", {}),
    "meeting": env.get("meeting", {}),
    "transcript": {
        "id": transcript.get("id") if isinstance(transcript, dict) else None,
        "status": transcript.get("status") if isinstance(transcript, dict) else None,
        "segment_count": len(segments),
        "first_speaker": segments[0].get("speaker") if segments else None,
        "first_text_prefix": (segments[0].get("text") or "")[:100] if segments else None,
    },
    "server_page_scan": {
        "html_contains_no_transcript_copy": bool(re.search(r"No transcript|No transcript available", html, re.I)),
        "html_contains_known_speaker": bool(re.search(r"Maya|Leo", html)),
        "note": "Server HTML can be inconclusive for client-rendered dashboard pages; use browser DOM probe before rebuilding.",
    },
    "containers": [line for line in read(containers_path).splitlines() if line.strip()],
}

Path(summary_path).write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
PY

rm -f "$raw_config" "$raw_meeting" "$raw_transcript" "$OUT_DIR/auth-me.raw" "$OUT_DIR/meeting-page.html"

if grep -R -E 'vxa_|api_key=|webhook_secret|TRANSCRIPTION_SERVICE_TOKEN|Authorization: Bearer' "$OUT_DIR" >/dev/null 2>&1; then
  echo "ERROR: possible secret material detected in $OUT_DIR" >&2
  exit 1
fi

echo "Evidence: $summary"
