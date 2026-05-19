#!/usr/bin/env bash
# live-bot-transcript-pipeline — prove a real admitted bot produces transcript
# segments, not just recording chunks or a standalone transcription smoke.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$ROOT_DIR/tests3/lib/common.sh"

MODE="$(cat "$STATE/deploy_mode" 2>/dev/null || detect_mode)"
GATEWAY_URL="${GATEWAY_URL:-$(cat "$STATE/gateway_url" 2>/dev/null || true)}"
API_TOKEN="${API_TOKEN:-$(cat "$STATE/api_token" 2>/dev/null || true)}"
LIVE_BOT_MEETING_ID="${LIVE_BOT_MEETING_ID:-${DASHBOARD_RECORDING_MEETING_ID:-${DASHBOARD_MEETING_ID:-}}}"
if [ -z "$GATEWAY_URL" ]; then
  case "$MODE" in
    lite) GATEWAY_URL="http://localhost:8156" ;;
    compose) GATEWAY_URL="http://localhost:8056" ;;
    *) GATEWAY_URL="http://localhost:8056" ;;
  esac
fi

test_begin "live-bot-transcript-pipeline"

if [ -z "$API_TOKEN" ]; then
  step_fail LIVE_BOT_TRANSCRIPT_SEGMENTS_PRESENT "api_token missing from state; cannot inspect live transcript pipeline"
  test_end
  exit 0
fi

OUT_FILE="$(mktemp -t live-transcript-pipeline-XXXXXX.txt)"
trap 'rm -f "$OUT_FILE"' EXIT

python3 - "$GATEWAY_URL" "$API_TOKEN" "$LIVE_BOT_MEETING_ID" >"$OUT_FILE" <<'PY'
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

gateway, token, configured_meeting_id = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3].strip()

def get_json(path):
    req = urllib.request.Request(f"{gateway}{path}", headers={"X-API-Key": token})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)

def chunk_count(meeting):
    count = 0
    for rec in (meeting.get("data") or {}).get("recordings") or []:
        for media in rec.get("media_files") or []:
            count = max(count, int(media.get("chunk_count") or 0), int(media.get("chunk_seq") or -1) + 1)
    return count

try:
    data = get_json("/meetings?limit=50&offset=0")
except Exception as exc:
    print(f"FAIL failed to list meetings: {exc}")
    sys.exit(0)

meetings = data.get("meetings") if isinstance(data, dict) else data
if not isinstance(meetings, list):
    print("FAIL /meetings response did not contain a meetings list")
    sys.exit(0)

candidates = []
for meeting in meetings:
    mdata = meeting.get("data") or {}
    if not mdata.get("transcribe_enabled"):
        continue
    if meeting.get("status") not in {"active", "completed"}:
        continue
    chunks = chunk_count(meeting)
    if chunks <= 0:
        continue
    candidates.append((meeting, chunks))

if not candidates:
    print("FAIL no active/completed transcribe_enabled meeting with recording chunks; cannot hand human a transcript checkpoint")
    sys.exit(0)

if configured_meeting_id:
    candidates = [(meeting, chunks) for meeting, chunks in candidates if str(meeting.get("id")) == configured_meeting_id]
    if not candidates:
        print(f"FAIL configured meeting {configured_meeting_id} is not an active/completed transcribe_enabled meeting with recording chunks")
        sys.exit(0)
else:
    candidates = [max(candidates, key=lambda item: int(item[0].get("id") or 0))]

failures = []
passes = []
for meeting, chunks in candidates:
    meeting_id = meeting.get("id")
    platform = meeting.get("platform")
    native_id = meeting.get("native_meeting_id")
    if not platform or not native_id:
        failures.append(f"meeting {meeting_id} missing platform/native_meeting_id")
        continue

    path = f"/transcripts/{urllib.parse.quote(platform)}/{urllib.parse.quote(native_id)}?meeting_id={urllib.parse.quote(str(meeting_id))}"
    try:
        tx = get_json(path)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:200]
        failures.append(f"transcript fetch HTTP {exc.code} for meeting {meeting_id}: {body}")
        continue
    except Exception as exc:
        failures.append(f"transcript fetch failed for meeting {meeting_id}: {exc}")
        continue

    segments = tx.get("segments") if isinstance(tx, dict) else None
    segment_count = len(segments) if isinstance(segments, list) else 0
    if segment_count <= 0:
        failures.append(f"meeting {meeting_id} has {chunks} recording chunk(s), transcribe_enabled=true, but 0 transcript segments")
    else:
        passes.append(f"meeting {meeting_id}: {chunks} chunk(s), {segment_count} segment(s)")

if failures:
    print("FAIL " + " | ".join(failures[:5]))
else:
    print("PASS " + "; ".join(passes[:5]))
PY

RESULT="$(cat "$OUT_FILE")"
case "$RESULT" in
  PASS*) step_pass LIVE_BOT_TRANSCRIPT_SEGMENTS_PRESENT "${RESULT#PASS }" ;;
  *) step_fail LIVE_BOT_TRANSCRIPT_SEGMENTS_PRESENT "${RESULT#FAIL }" ;;
esac

test_end
