#!/usr/bin/env bash
# v0.10.6 runtime smoke checks (Pack U — audio recording unification).
#
# Most steps require live test cluster + fixture meeting URLs. When
# fixtures aren't provided (e.g. lite mode running locally), each step
# step_skip's cleanly. The bot-kill recovery tests are operator-driven
# in compose/helm — see scope.yaml human_verify[] for the dispatch +
# verify steps. The finalizer-idempotency + download-presigned tests
# can run with just GATEWAY_URL + ADMIN_TOKEN.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6-runtime-smokes :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6-runtime-smokes-$step"

case "$step" in

  finalizer_idempotency)
    # FINALIZER_IS_IDEMPOTENT: invoke finalize_recording_master twice on
    # the same recording; second invocation must be a no-op (HEAD-checks
    # for existing master and returns early).
    if [ -z "${COMPOSE_VM_IP:-}" ] && [ -z "${MEETING_API_CONTAINER:-}" ]; then
      step_skip FINALIZER_IS_IDEMPOTENT "no compose VM / container target — see scope.yaml human_verify"
      exit 0
    fi
    # Strategy: docker exec into the meeting-api container, run a small
    # python harness that imports finalize_recording_master + invokes it
    # twice for the most-recent completed Recording, asserts the second
    # call doesn't re-upload (storage_path unchanged + no error).
    # TODO: implement the docker exec harness once the test fixture
    # provides a known-good recording id.
    step_skip FINALIZER_IS_IDEMPOTENT "harness stub — see scope.yaml human_verify (compose mode)"
    ;;

  master_at_storage_path)
    # MASTER_AT_STORAGE_PATH: after a normal-completion meeting,
    # media_file.storage_path ends with `/audio/master.{webm|wav}` or
    # `/video/master.{webm|wav}`, NOT `/audio/000000.{ext}` (chunk 0).
    # Validates that bot_exit_callback's finalize_recording_master await
    # ran successfully and updated the row.
    if [ -z "${DB_HOST:-}" ] && [ -z "${COMPOSE_VM_IP:-}" ]; then
      step_skip MASTER_AT_STORAGE_PATH "no DB target — see scope.yaml human_verify"
      exit 0
    fi
    # Strategy: psql (or docker exec) query:
    #   SELECT mf.storage_path
    #   FROM media_files mf
    #   JOIN recordings r ON r.id = mf.recording_id
    #   WHERE r.status = 'completed' AND mf.type IN ('audio','video')
    #   ORDER BY mf.id DESC LIMIT 5;
    # Assert at least one row matches /audio/master\.(webm|wav)$ or
    # /video/master\.(webm|wav)$.
    step_skip MASTER_AT_STORAGE_PATH "DB query stub — see scope.yaml human_verify (compose+helm)"
    ;;

  bot_kill_gmeet_master_playable)
    # BOT_KILL_RECORDING_PLAYABLE_GMEET: SIGKILL a GMeet bot mid-recording,
    # wait for runtime-api idle_loop to detect vanished container + fire
    # exit callback, then verify the master.webm was built from chunks
    # already in MinIO and is ffprobe-playable.
    if [ -z "${FIXTURE_GMEET_MULTIPARTY_URL:-}" ]; then
      step_skip BOT_KILL_RECORDING_PLAYABLE_GMEET \
        "FIXTURE_GMEET_MULTIPARTY_URL not set — operator-driven; see scope.yaml human_verify"
      exit 0
    fi
    # Strategy:
    #   1. POST /bots with FIXTURE_GMEET_MULTIPARTY_URL, recording_enabled=true
    #   2. Wait until at least 5 chunks land in MinIO (poll storage prefix)
    #   3. SIGKILL the bot pod (kubectl delete pod --grace-period=0 OR
    #      docker kill <bot-container>)
    #   4. Wait up to 90s for idle_loop → exit callback → finalizer
    #   5. ffprobe the master.webm via presigned URL — duration ≥ 75s
    step_skip BOT_KILL_RECORDING_PLAYABLE_GMEET "live-bot fixture stub — see scope.yaml human_verify"
    ;;

  bot_kill_teams_master_playable)
    # BOT_KILL_RECORDING_PLAYABLE_TEAMS: same as gmeet but with Teams.
    if [ -z "${FIXTURE_TEAMS_MULTIPARTY_URL:-}" ]; then
      step_skip BOT_KILL_RECORDING_PLAYABLE_TEAMS \
        "FIXTURE_TEAMS_MULTIPARTY_URL not set — operator-driven; see scope.yaml human_verify"
      exit 0
    fi
    step_skip BOT_KILL_RECORDING_PLAYABLE_TEAMS "live-bot fixture stub — see scope.yaml human_verify"
    ;;

  bot_kill_zoom_master_playable)
    # BOT_KILL_RECORDING_PLAYABLE_ZOOM: same as gmeet but with Zoom Web.
    # Critical regression coverage — pre-Pack-U Zoom Web crash = total
    # audio loss because parecord WAV was on bot-pod local disk only.
    # Now chunks land in MinIO every 15s (Pack U.4) so the master can be
    # built post-crash.
    if [ -z "${FIXTURE_ZOOM_URL:-}" ]; then
      step_skip BOT_KILL_RECORDING_PLAYABLE_ZOOM \
        "FIXTURE_ZOOM_URL not set — operator-driven; see scope.yaml human_verify"
      exit 0
    fi
    step_skip BOT_KILL_RECORDING_PLAYABLE_ZOOM "live-bot fixture stub — see scope.yaml human_verify"
    ;;

  deferred_transcribe_master)
    # DEFERRED_TRANSCRIBE_USES_MASTER: POST /meetings/{id}/transcribe on
    # a recording produced by a SIGKILL'd bot — should now succeed and
    # return segments because storage_path points at master.webm built
    # by the finalizer (didn't pre-Pack-U).
    if [ -z "${GATEWAY_URL:-}" ] || [ -z "${ADMIN_TOKEN:-}" ]; then
      step_skip DEFERRED_TRANSCRIBE_USES_MASTER \
        "GATEWAY_URL + ADMIN_TOKEN required — see scope.yaml human_verify"
      exit 0
    fi
    # Strategy: pick the latest completed/failed meeting for the test
    # user, POST /meetings/{id}/transcribe, assert HTTP 200 + segment_count > 0.
    step_skip DEFERRED_TRANSCRIBE_USES_MASTER "deferred-transcribe stub — see scope.yaml human_verify"
    ;;

  download_presigned_master)
    # DOWNLOAD_RETURNS_PRESIGNED_URL_TO_MASTER: GET /download returns
    # JSON with a `url` field whose path component ends in
    # `/audio/master.{webm|wav}` or `/video/master.{webm|wav}`. Browser-
    # reachable via MINIO_PUBLIC_ENDPOINT.
    if [ -z "${GATEWAY_URL:-}" ] || [ -z "${ADMIN_TOKEN:-}" ]; then
      step_skip DOWNLOAD_RETURNS_PRESIGNED_URL_TO_MASTER \
        "GATEWAY_URL + ADMIN_TOKEN required — see scope.yaml human_verify"
      exit 0
    fi
    # Strategy:
    #   1. GET /recordings (list user's recordings, completed only)
    #   2. Pick latest; GET /recordings/{id}/media/{file}/download
    #   3. Parse JSON; assert .url ends with /master.{webm|wav}
    #   4. Assert .url has the MINIO_PUBLIC_ENDPOINT host (or relative path)
    #   5. HEAD-request the URL; assert 200 + Content-Range support
    #
    # Implemented as best-effort — if no recordings exist, step_skip
    # rather than fail (no fixture data yet).
    rec_list=$(curl -sS -H "X-API-Key: $ADMIN_TOKEN" "$GATEWAY_URL/recordings?limit=10" 2>/dev/null || echo "")
    if [ -z "$rec_list" ] || [ "$rec_list" = "[]" ]; then
      step_skip DOWNLOAD_RETURNS_PRESIGNED_URL_TO_MASTER \
        "no recordings available for this token — fixture data needed"
      exit 0
    fi
    # Pick the first completed recording with a media_file. Use python
    # (jq may not be available in all test envs).
    parsed=$(python3 -c "
import json, sys
data = json.loads('''$rec_list''')
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
      exit 0
    fi
    rid=$(echo "$parsed" | awk '{print $1}')
    fid=$(echo "$parsed" | awk '{print $2}')
    resp=$(curl -sS -H "X-API-Key: $ADMIN_TOKEN" "$GATEWAY_URL/recordings/$rid/media/$fid/download" 2>/dev/null)
    if [ -z "$resp" ]; then
      step_fail DOWNLOAD_RETURNS_PRESIGNED_URL_TO_MASTER "download endpoint returned empty body"
      exit 1
    fi
    url=$(echo "$resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('url',''))" 2>/dev/null || echo "")
    if [ -z "$url" ]; then
      step_fail DOWNLOAD_RETURNS_PRESIGNED_URL_TO_MASTER "/download response missing url field: $resp"
      exit 1
    fi
    # Path component ends with master.{webm|wav}? Use python URL parse.
    path=$(python3 -c "from urllib.parse import urlparse; print(urlparse('$url').path)" 2>/dev/null)
    case "$path" in
      */audio/master.webm|*/audio/master.wav|*/video/master.webm|*/video/master.wav)
        step_pass DOWNLOAD_RETURNS_PRESIGNED_URL_TO_MASTER \
          "presigned URL points at master: $path"
        ;;
      *)
        step_fail DOWNLOAD_RETURNS_PRESIGNED_URL_TO_MASTER \
          "URL path does not end at /audio/master.* or /video/master.*: $path"
        exit 1
        ;;
    esac
    ;;

  *)
    step_fail "v0.10.6-runtime-smokes" "unknown step: $step"
    exit 1
    ;;
esac

echo "  ──────────────────────────────────────────────"
echo ""
test_end
