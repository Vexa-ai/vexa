#!/usr/bin/env bash
# v0.10.6 runtime smoke checks (Pack U — audio recording unification).
#
# Single-invocation multi-step pattern. Most steps require live test
# cluster + fixture meeting URLs; skip cleanly when absent — operator-
# driven validation runs via scope.yaml human_verify[]. The
# DOWNLOAD_RETURNS_PRESIGNED_URL_TO_MASTER step has a real implementation
# that runs against any completed recording in the test user's data.
#
# Step IDs:
#   FINALIZER_IS_IDEMPOTENT
#   MASTER_AT_STORAGE_PATH
#   BOT_KILL_RECORDING_PLAYABLE_GMEET
#   BOT_KILL_RECORDING_PLAYABLE_TEAMS
#   BOT_KILL_RECORDING_PLAYABLE_ZOOM
#   DEFERRED_TRANSCRIBE_USES_MASTER
#   DOWNLOAD_RETURNS_PRESIGNED_URL_TO_MASTER

source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url 2>/dev/null || echo "")
API_TOKEN=$(state_read api_token 2>/dev/null || echo "")

echo ""
echo "  v0.10.6-runtime-smokes"
echo "  ──────────────────────────────────────────────"

test_begin v0.10.6-runtime-smokes

# ── FINALIZER_IS_IDEMPOTENT ────────────────────────────────────────
# Stub: requires docker-exec into meeting-api container with a known
# recording_id. Operator-driven via scope.yaml human_verify.
step_skip FINALIZER_IS_IDEMPOTENT "harness stub — see scope.yaml human_verify (compose mode)"

# ── MASTER_AT_STORAGE_PATH ─────────────────────────────────────────
# After a normal-completion meeting, media_file.storage_path ends with
# /audio/master.{webm|wav}. Stub — DB query needs fixture data.
step_skip MASTER_AT_STORAGE_PATH "DB query stub — see scope.yaml human_verify (compose+helm)"

# ── BOT_KILL_RECORDING_PLAYABLE_GMEET ──────────────────────────────
if [ -z "${FIXTURE_GMEET_MULTIPARTY_URL:-}" ]; then
  step_skip BOT_KILL_RECORDING_PLAYABLE_GMEET \
    "FIXTURE_GMEET_MULTIPARTY_URL not set — operator-driven; see scope.yaml human_verify"
else
  step_skip BOT_KILL_RECORDING_PLAYABLE_GMEET "live-bot fixture stub — see scope.yaml human_verify"
fi

# ── BOT_KILL_RECORDING_PLAYABLE_TEAMS ──────────────────────────────
if [ -z "${FIXTURE_TEAMS_MULTIPARTY_URL:-}" ]; then
  step_skip BOT_KILL_RECORDING_PLAYABLE_TEAMS \
    "FIXTURE_TEAMS_MULTIPARTY_URL not set — operator-driven; see scope.yaml human_verify"
else
  step_skip BOT_KILL_RECORDING_PLAYABLE_TEAMS "live-bot fixture stub — see scope.yaml human_verify"
fi

# ── BOT_KILL_RECORDING_PLAYABLE_ZOOM ───────────────────────────────
if [ -z "${FIXTURE_ZOOM_URL:-}" ]; then
  step_skip BOT_KILL_RECORDING_PLAYABLE_ZOOM \
    "FIXTURE_ZOOM_URL not set — operator-driven; see scope.yaml human_verify"
else
  step_skip BOT_KILL_RECORDING_PLAYABLE_ZOOM "live-bot fixture stub — see scope.yaml human_verify"
fi

# ── DEFERRED_TRANSCRIBE_USES_MASTER ────────────────────────────────
if [ -z "$GATEWAY_URL" ] || [ -z "$API_TOKEN" ]; then
  step_skip DEFERRED_TRANSCRIBE_USES_MASTER \
    "gateway_url + api_token state not present — see scope.yaml human_verify"
else
  step_skip DEFERRED_TRANSCRIBE_USES_MASTER "deferred-transcribe stub — see scope.yaml human_verify"
fi

# ── DOWNLOAD_RETURNS_PRESIGNED_URL_TO_MASTER ──────────────────────
# Real implementation. Picks the latest completed recording with audio/
# video, calls /download, asserts the .url path component ends at
# /audio/master.{webm|wav} or /video/master.{webm|wav}.
if [ -z "$GATEWAY_URL" ] || [ -z "$API_TOKEN" ]; then
  step_skip DOWNLOAD_RETURNS_PRESIGNED_URL_TO_MASTER \
    "gateway_url + api_token state not present"
else
  rec_list=$(curl -sS -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/recordings?limit=10" 2>/dev/null || echo "")
  if [ -z "$rec_list" ] || [ "$rec_list" = "[]" ] || [ "$rec_list" = "null" ]; then
    step_skip DOWNLOAD_RETURNS_PRESIGNED_URL_TO_MASTER \
      "no recordings available for this token — fixture data needed"
  else
    parsed=$(REC_LIST="$rec_list" python3 -c "
import json, os, sys
data = json.loads(os.environ['REC_LIST'])
recs = data if isinstance(data, list) else data.get('recordings', [])
for r in recs:
    if r.get('status') == 'completed':
        for mf in r.get('media_files', []) or []:
            if mf.get('type') in ('audio', 'video'):
                print(f\"{r['id']} {mf['id']}\")
                sys.exit(0)
" 2>/dev/null || echo "")
    if [ -z "$parsed" ]; then
      step_skip DOWNLOAD_RETURNS_PRESIGNED_URL_TO_MASTER \
        "no completed recording with audio/video — fixture data needed"
    else
      rid=$(echo "$parsed" | awk '{print $1}')
      fid=$(echo "$parsed" | awk '{print $2}')
      resp=$(curl -sS -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/recordings/$rid/media/$fid/download" 2>/dev/null)
      if [ -z "$resp" ]; then
        step_fail DOWNLOAD_RETURNS_PRESIGNED_URL_TO_MASTER "download endpoint returned empty body"
      else
        url=$(RESP="$resp" python3 -c "import json,os; print(json.loads(os.environ['RESP']).get('url',''))" 2>/dev/null || echo "")
        if [ -z "$url" ]; then
          step_fail DOWNLOAD_RETURNS_PRESIGNED_URL_TO_MASTER "/download response missing url field: $resp"
        else
          path=$(URL="$url" python3 -c "import os; from urllib.parse import urlparse; print(urlparse(os.environ['URL']).path)" 2>/dev/null)
          case "$path" in
            */audio/master.webm|*/audio/master.wav|*/video/master.webm|*/video/master.wav)
              step_pass DOWNLOAD_RETURNS_PRESIGNED_URL_TO_MASTER \
                "presigned URL points at master: $path"
              ;;
            *)
              step_fail DOWNLOAD_RETURNS_PRESIGNED_URL_TO_MASTER \
                "URL path does not end at /audio/master.* or /video/master.*: $path"
              ;;
          esac
        fi
      fi
    fi
  fi
fi

echo ""
echo "  ──────────────────────────────────────────────"
test_end
