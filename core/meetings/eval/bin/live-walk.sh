#!/usr/bin/env bash
# live-walk.sh — instruments for the Vexa 0.12 dashboard happy-path walk.
#
# A read-only, non-destructive harness the operator runs LIVE against a real
# stack (bbb or otherwise) while watching the dashboard meeting-detail page. It
# proves the four signal paths the dashboard depends on:
#
#   1. redis    — tap bm:meeting:<id>:status + tc:meeting:<id>:mutable (the raw
#                 pubsub the gateway /ws fans in) and timestamp every frame.
#   2. ws       — connect ws://$GW/ws as a browser-equivalent client, subscribe,
#                 and log every frame (the path the dashboard actually uses).
#   3. playback — GET /recordings/<rec>/master?type=audio -> raw_url -> bytes ->
#                 ffprobe; nonzero on EBML/webm failure, zero on a valid webm
#                 with an audio stream.
#   4. regress  — a handful of non-polluting curls (auth/me, meetings list, the
#                 stop route exists, the recording-receiver path) + a DOCUMENTED
#                 (not run) max-bots cap test.
#
# NOTHING here spawns a bot, stops a bot, or uploads/mutates state. The stop
# route and recording-receiver are probed with OPTIONS / existence-only checks.
#
# ---------------------------------------------------------------------------
# Required env:
#   GW          gateway host:port,            e.g. localhost:18056
#   MEETING_ID  numeric internal meeting id,  e.g. 8725  (the redis channel id)
#   TOKEN       a tx+bot-scoped API key,      e.g. vx_sk_...
#
# Optional env (per-stage; a stage is skipped with a notice if its inputs miss):
#   REC_ID      recording id for the playback stage, e.g. 42
#   PLATFORM    platform slug for the stop-route probe (default google_meet)
#   NATIVE_ID   native meeting id for the stop-route probe (default: derived
#               from the meetings list, else "abc-defg-hij")
#   GW_SCHEME   http|https for REST (default http)
#   WS_SCHEME   ws|wss   for the socket (default ws)
#   DURATION    seconds to keep the redis tap + ws client open (default 120)
#   STAGES      comma list to run a subset: redis,ws,playback,regression
#               (default: all)
#
# Redis access — provide EXACTLY ONE of:
#   REDIS_CLI="redis-cli -h <host> -p <port> [-a <pass>]"   # direct redis-cli
#   REDIS_DOCKER="<container>"                               # docker exec target;
#                                                             runs redis-cli inside
#   (REDIS_DOCKER_CLI overrides the in-container client, default: redis-cli)
#
# Dependencies: bash, curl, ffprobe (ffmpeg), and a WS client — either
#   `websocat` (preferred, zero-config) OR node with the `ws` package on
#   NODE_PATH. The ws stage prints exactly which it used / what to install.
#
# Usage:
#   GW=localhost:18056 MEETING_ID=8725 TOKEN=vx_sk_... REC_ID=42 \
#   REDIS_DOCKER=vexa-redis-1 ./bin/live-walk.sh
#
#   # subset:
#   STAGES=redis,ws GW=... MEETING_ID=... TOKEN=... REDIS_CLI="redis-cli -h 1.2.3.4" ./bin/live-walk.sh
#
# Exit code: nonzero if any RUN stage fails its assertion (notably playback
# EBML failure). Skipped stages (missing optional input) do not fail the run.
# ---------------------------------------------------------------------------
set -uo pipefail

# ---- pretty output --------------------------------------------------------
# GNU date supports %3N (ms); BSD/macOS date does not — detect once and fall
# back to whole-second stamps rather than printing a literal "3N".
if date -u +"%3N" 2>/dev/null | grep -qE '^[0-9]+$'; then _TSFMT="%H:%M:%S.%3N"; else _TSFMT="%H:%M:%S"; fi
_ts() { date -u +"$_TSFMT"; }
log()  { printf '%s | %s\n' "$(_ts)" "$*"; }
hd()   { printf '\n==================== %s ====================\n' "$*"; }
ok()   { printf '  [OK]   %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; }
skip() { printf '  [SKIP] %s\n' "$*"; }
note() { printf '  [NOTE] %s\n' "$*"; }

FAILED=0
fail() { bad "$*"; FAILED=1; }

# ---- config ---------------------------------------------------------------
GW="${GW:-}"
MEETING_ID="${MEETING_ID:-}"
TOKEN="${TOKEN:-}"
REC_ID="${REC_ID:-}"
PLATFORM="${PLATFORM:-google_meet}"
NATIVE_ID="${NATIVE_ID:-}"
GW_SCHEME="${GW_SCHEME:-http}"
WS_SCHEME="${WS_SCHEME:-ws}"
DURATION="${DURATION:-120}"
STAGES="${STAGES:-redis,ws,playback,regression}"

REST_BASE="${GW_SCHEME}://${GW}"
WS_URL="${WS_SCHEME}://${GW}/ws"

want() { case ",$STAGES," in *",$1,"*) return 0;; *) return 1;; esac; }

require_common() {
  local miss=0
  [ -n "$GW" ]         || { bad "GW unset (gateway host:port, e.g. localhost:18056)"; miss=1; }
  [ -n "$MEETING_ID" ] || { bad "MEETING_ID unset (numeric internal meeting id)"; miss=1; }
  [ -n "$TOKEN" ]      || { bad "TOKEN unset (tx+bot-scoped API key)"; miss=1; }
  [ "$miss" = 0 ] || exit 2
}

# ---- redis command builder ------------------------------------------------
# Echoes a command prefix that, when followed by redis args, runs redis-cli
# against the configured target. Returns nonzero if no access method given.
redis_cmd() {
  if [ -n "${REDIS_CLI:-}" ]; then
    printf '%s' "$REDIS_CLI"
    return 0
  fi
  if [ -n "${REDIS_DOCKER:-}" ]; then
    printf 'docker exec -i %s %s' "$REDIS_DOCKER" "${REDIS_DOCKER_CLI:-redis-cli}"
    return 0
  fi
  return 1
}

# ===========================================================================
# STAGE 1 — redis status + transcript tap
# ===========================================================================
stage_redis() {
  hd "STAGE 1 — redis tap (bm:status + tc:mutable)"
  local STATUS_CH="bm:meeting:${MEETING_ID}:status"
  local MUT_CH="tc:meeting:${MEETING_ID}:mutable"
  log "channels: $STATUS_CH , $MUT_CH"

  local RC
  if ! RC="$(redis_cmd)"; then
    skip "no redis access method — set REDIS_CLI=\"redis-cli -h <host> -p <port>\" OR REDIS_DOCKER=<container>"
    return 0
  fi
  log "redis client: $RC"

  # Connectivity ping first so we fail loud rather than silently waiting.
  if ! $RC PING >/dev/null 2>&1; then
    fail "redis PING failed via: $RC  (check host/port/container/auth)"
    return 0
  fi
  ok "redis reachable"

  log "subscribing for ${DURATION}s — stamp + assert status ordering, confirm transcripts flow."
  note "status frames are forwarded RAW by the gateway; the dashboard reads {type:'meeting.status', payload:{status:...}}."

  # Subscribe, timestamp each delivered payload, and track ordering of status
  # frames. redis-cli SUBSCRIBE emits 3 lines per message (kind, channel,
  # payload); we pair channel->payload and stamp the payload line.
  #
  # Ordering check: the dashboard's STATUS_ORDER is
  #   requested<joining<awaiting_admission<active<{stopping|completed}; failed=-1.
  # We warn (not fail) on a backwards step — live races can legitimately skip.
  #
  # We use a wrapping `timeout` if available; otherwise background + sleep + kill.
  local SUB="$RC SUBSCRIBE $STATUS_CH $MUT_CH"
  local awk_prog='
    function ord(s){
      if(s=="requested")return 0; if(s=="joining")return 1;
      if(s=="awaiting_admission")return 2; if(s=="active")return 3;
      if(s=="stopping"||s=="completed")return 4; if(s=="needs_human_help")return 3;
      if(s=="failed")return -1; return -99;
    }
    function stamp(){ cmd="date -u +" TSFMT; cmd | getline t; close(cmd); return t; }
    function dequote(x){ if(x ~ /^".*"$/){ x=substr(x,2,length(x)-2) } return x }
    BEGIN{ last=-100; nstat=0; ntx=0; }
    # In piped (non-tty) mode redis-cli prints each reply element on its own
    # line: "message", "<channel>", "<payload>" (channel may be quoted).
    # We classify by the channel-name suffix, then print the NEXT line (payload).
    /^"?subscribe"?$/ { insub=1; next }   # SUBSCRIBE confirmation: kind, channel, count
    /:status"?$/   { ch="status"; if(insub){next} next }
    /:mutable"?$/  { ch="mutable"; if(insub){next} next }
    /^"?message"?$/   { insub=0; next }
    insub==1 { insub=0; next }            # swallow the per-channel subscribe count line
    {
      payload=dequote($0);
      if(payload==""){ next }
      printf "%s | <%s> %s\n", stamp(), ch, payload;
      if(ch=="status"){
        nstat++;
        s=payload;
        gsub(/.*"status"[ ]*:[ ]*"/,"",s); gsub(/".*/,"",s);
        o=ord(s);
        if(o!=-99){
          if(o>=0 && last>=0 && o<last){
            printf "      [WARN] status went backwards: prev-ord=%d now=\"%s\"(ord=%d)\n", last, s, o;
          }
          last=o;
        }
      } else if(ch=="mutable"){ ntx++ }
    }
    END{
      printf "  ---- redis tap summary ----\n";
      printf "  status frames: %d   transcript frames: %d\n", nstat, ntx;
      if(nstat==0) printf "  [WARN] no status frames seen in window (meeting may be idle/terminal)\n";
      if(ntx==0)   printf "  [WARN] no transcript frames seen (no speech yet, or meeting not active)\n";
    }'

  if command -v timeout >/dev/null 2>&1; then
    timeout "${DURATION}s" bash -c "$SUB" 2>/dev/null | awk -v TSFMT="$_TSFMT" "$awk_prog"
  else
    bash -c "$SUB" 2>/dev/null | awk -v TSFMT="$_TSFMT" "$awk_prog" &
    local p=$!
    sleep "$DURATION"
    kill "$p" 2>/dev/null || true
    wait "$p" 2>/dev/null || true
  fi
  ok "redis tap window closed"
}

# ===========================================================================
# STAGE 2 — WS client (browser-equivalent path)
# ===========================================================================
stage_ws() {
  hd "STAGE 2 — WS client ($WS_URL)"
  # Browser-equivalent: the dashboard sends api_key as a query param (browsers
  # can't set WS headers), then sends the subscribe action, then pings.
  local FULL_WS="${WS_URL}?api_key=$(printf '%s' "$TOKEN" | sed 's/ /%20/g')"
  local SUB_MSG
  SUB_MSG="$(printf '{"action":"subscribe","meetings":[{"platform":"%s","native_id":"%s"}]}' \
                  "$PLATFORM" "${NATIVE_ID:-PLACEHOLDER_NATIVE_ID}")"
  log "subscribe frame: $SUB_MSG"
  if [ -z "${NATIVE_ID:-}" ]; then
    note "NATIVE_ID unset — using PLACEHOLDER. Set NATIVE_ID=<native meeting id> for a real subscribe ack."
  fi

  # Prefer websocat; fall back to node 'ws'.
  if command -v websocat >/dev/null 2>&1; then
    log "client: websocat"
    note "expect frames: {\"type\":\"subscribed\",...} then {\"type\":\"meeting.status\",\"payload\":{\"status\":...}} and {\"type\":\"transcript\",\"speaker\":...,\"confirmed\":[...],\"pending\":[...]}"
    # Feed the subscribe + periodic pings on stdin; stamp every received frame.
    {
      printf '%s\n' "$SUB_MSG"
      local elapsed=0
      while [ "$elapsed" -lt "$DURATION" ]; do
        sleep 25; elapsed=$((elapsed+25))
        printf '{"action":"ping"}\n'
      done
    } | { timeout "${DURATION}s" websocat -n -t "$FULL_WS" 2>/dev/null || true; } \
      | while IFS= read -r frame; do log "<WS> $frame"; done
    ok "ws window closed (websocat)"
    return 0
  fi

  if command -v node >/dev/null 2>&1 && node -e "require('ws')" >/dev/null 2>&1; then
    log "client: node 'ws'"
    WS_URL_FULL="$FULL_WS" SUB_MSG="$SUB_MSG" DURATION="$DURATION" node - <<'NODE'
const WebSocket = require('ws');
const url = process.env.WS_URL_FULL;
const sub = process.env.SUB_MSG;
const dur = parseInt(process.env.DURATION, 10) * 1000;
const ts = () => new Date().toISOString().slice(11, 23);
const log = (m) => console.log(`${ts()} | ${m}`);
const ws = new WebSocket(url);
let ping;
ws.on('open', () => {
  log('<WS> open — sending subscribe');
  ws.send(sub);
  ping = setInterval(() => { try { ws.send('{"action":"ping"}'); } catch {} }, 25000);
});
ws.on('message', (d) => log(`<WS> ${d.toString()}`));
ws.on('error', (e) => log(`<WS> error: ${e.message}`));
ws.on('close', (c, r) => log(`<WS> close code=${c} reason=${r}`));
setTimeout(() => { clearInterval(ping); try { ws.close(1000, 'done'); } catch {} ; setTimeout(() => process.exit(0), 200); }, dur);
NODE
    ok "ws window closed (node ws)"
    return 0
  fi

  skip "no WS client found. Install ONE of:"
  note "  brew install websocat        # macOS, preferred"
  note "  cargo install websocat       # any platform"
  note "  npm i -g ws && export NODE_PATH=\$(npm root -g)   # node fallback"
  note "Then re-run STAGES=ws. The frame to send is: $SUB_MSG"
}

# ===========================================================================
# STAGE 3 — playback check (master -> raw_url -> ffprobe)
# ===========================================================================
stage_playback() {
  hd "STAGE 3 — playback (master -> raw_url -> ffprobe)"
  if [ -z "$REC_ID" ]; then
    skip "REC_ID unset — skipping playback check. Set REC_ID=<recording id>."
    return 0
  fi
  if ! command -v ffprobe >/dev/null 2>&1; then
    fail "ffprobe not found (install ffmpeg) — cannot validate the webm/EBML stream."
    return 0
  fi

  local MASTER_URL="${REST_BASE}/recordings/${REC_ID}/master?type=audio"
  log "GET $MASTER_URL"
  local MASTER_JSON
  MASTER_JSON="$(curl -fsS -H "X-API-Key: ${TOKEN}" "$MASTER_URL" 2>/dev/null)" || {
    fail "master fetch failed (HTTP error / not finalized). Recording may still be finalizing."
    return 0
  }
  log "master json: $MASTER_JSON"

  # Parse raw_url without jq (portable). Field shape (meeting-api recordings/router.py):
  #   {"id":..,"type":"audio","storage_path":..,"media_file_id":..,"raw_url":"/recordings/<id>/media/<mid>/raw?type=audio","duration_seconds":..}
  local RAW_PATH
  RAW_PATH="$(printf '%s' "$MASTER_JSON" \
    | sed -n 's/.*"raw_url"[ ]*:[ ]*"\([^"]*\)".*/\1/p')"
  if [ -z "$RAW_PATH" ] || [ "$RAW_PATH" = "null" ]; then
    fail "master response has no raw_url (no playable media file yet)."
    return 0
  fi

  # raw_url is a backend-relative path; resolve against the gateway base.
  local RAW_URL
  case "$RAW_PATH" in
    http*) RAW_URL="$RAW_PATH" ;;
    /*)    RAW_URL="${REST_BASE}${RAW_PATH}" ;;
    *)     RAW_URL="${REST_BASE}/${RAW_PATH}" ;;
  esac
  log "raw_url -> $RAW_URL"

  # Stream the bytes to a temp file (follow redirects), then ffprobe.
  local TMP
  TMP="$(mktemp -t live-walk-rec.XXXXXX.webm)" || { fail "mktemp failed"; return 0; }
  trap 'rm -f "$TMP"' RETURN
  if ! curl -fsSL -H "X-API-Key: ${TOKEN}" -o "$TMP" "$RAW_URL" 2>/dev/null; then
    fail "raw byte fetch failed: $RAW_URL"
    return 0
  fi
  local SZ; SZ="$(wc -c < "$TMP" | tr -d ' ')"
  log "downloaded ${SZ} bytes -> $TMP"
  [ "${SZ:-0}" -gt 0 ] || { fail "downloaded 0 bytes"; return 0; }

  # ffprobe: require a webm/matroska container with at least one audio stream.
  local PROBE
  PROBE="$(ffprobe -v error -show_entries format=format_name:stream=codec_type \
                   -of default=nw=1 "$TMP" 2>&1)"
  local PRC=$?
  log "ffprobe: $(printf '%s' "$PROBE" | tr '\n' ' ')"
  if [ "$PRC" -ne 0 ]; then
    fail "ffprobe could not parse the stream — EBML/webm decode failure."
    return 0
  fi
  if ! printf '%s' "$PROBE" | grep -qiE 'format_name=.*(webm|matroska)'; then
    fail "container is not webm/matroska: $(printf '%s' "$PROBE" | grep format_name)"
    return 0
  fi
  if ! printf '%s' "$PROBE" | grep -qi 'codec_type=audio'; then
    fail "no audio stream present in the master."
    return 0
  fi
  ok "valid webm with an audio stream (EBML parsed, codec_type=audio present)"
}

# ===========================================================================
# STAGE 4 — regression checklist (non-polluting curls)
# ===========================================================================
# A helper that prints HTTP status for a method+path and asserts a wanted code.
http_code() { # method url [extra curl args...]
  local m="$1" u="$2"; shift 2
  curl -s -o /dev/null -w '%{http_code}' -X "$m" -H "X-API-Key: ${TOKEN}" "$@" "$u" 2>/dev/null
}

stage_regression() {
  hd "STAGE 4 — regression checklist (read-only curls)"

  # 4a. auth/me — 200 + identity from the key.
  local ME; ME="$(curl -fsS -H "X-API-Key: ${TOKEN}" "${REST_BASE}/auth/me" 2>/dev/null)"
  if [ -n "$ME" ] && printf '%s' "$ME" | grep -q '"user_id"'; then
    ok "GET /auth/me 200 — $(printf '%s' "$ME" | sed 's/.*\("user_id"[^,]*\).*/\1/')"
  else
    fail "GET /auth/me did not return a user_id (auth/key broken?). body=$ME"
  fi

  # 4b. meetings list — returns the user's meetings (scope tx). Read-only.
  local MEETINGS; MEETINGS="$(curl -fsS -H "X-API-Key: ${TOKEN}" "${REST_BASE}/meetings" 2>/dev/null)"
  if printf '%s' "$MEETINGS" | grep -q '"meetings"'; then
    local CNT; CNT="$(printf '%s' "$MEETINGS" | grep -o '"id"' | wc -l | tr -d ' ')"
    ok "GET /meetings 200 — ~${CNT} meeting object(s)"
    # Opportunistically surface a native id to feed the WS/stop probes.
    if [ -z "${NATIVE_ID:-}" ]; then
      local DERIVED
      DERIVED="$(printf '%s' "$MEETINGS" | sed -n 's/.*"platform_specific_id"[ ]*:[ ]*"\([^"]*\)".*/\1/p' | head -1)"
      [ -n "$DERIVED" ] && note "tip: a native id from the list is \"$DERIVED\" — pass NATIVE_ID=$DERIVED for ws/stop probes."
    fi
  else
    fail "GET /meetings did not return a meetings array. body head=$(printf '%s' "$MEETINGS" | head -c 200)"
  fi

  # 4c. the stop route exists (DELETE /bots/{platform}/{native}) — DO NOT actually
  #     stop a live bot. Probe existence via OPTIONS / a benign non-existent native
  #     id: a routed endpoint returns 404/401/403 (not 405 "method not allowed"
  #     and not a connection error), proving the DELETE verb is mounted.
  local SAFE_NATIVE="live-walk-nonexistent-$(date +%s)"
  local SC; SC="$(http_code DELETE "${REST_BASE}/bots/${PLATFORM}/${SAFE_NATIVE}")"
  case "$SC" in
    404|409|403|401|400|200|202|204)
      ok "DELETE /bots/{platform}/{native} routed (HTTP $SC on a nonexistent native — verb mounted, no live bot touched)" ;;
    405) fail "DELETE /bots/... returned 405 — stop verb NOT mounted at the gateway" ;;
    000) fail "DELETE /bots/... — connection failed (gateway down / wrong GW)" ;;
    *)   note "DELETE /bots/... returned HTTP $SC (routed; unexpected code — inspect)" ;;
  esac

  # 4d. the recording receiver path (POST /internal/recordings/upload). This is a
  #     bot-token-auth INTERNAL ingest route; we MUST NOT upload. Probe that the
  #     path exists by sending an empty/no-body POST and asserting it does not
  #     405. A mounted route rejects with 4xx (auth/validation), not 405/000.
  local UC; UC="$(http_code POST "${REST_BASE}/internal/recordings/upload")"
  case "$UC" in
    405) fail "POST /internal/recordings/upload returned 405 — receiver route NOT mounted" ;;
    000) note "POST /internal/recordings/upload — connection failed OR not exposed at the gateway (internal route may be cluster-only; check meeting-api directly)" ;;
    *)   ok "POST /internal/recordings/upload routed (HTTP $UC — receiver mounted; no chunk uploaded)" ;;
  esac

  # 4e. DOCUMENTED, NOT RUN — max-bots cap test (pollutes: it spawns bots).
  hd "STAGE 4e — max-bots cap test (DOCUMENTED, NOT RUN — pollutes)"
  cat <<DOC
  The concurrency cap is enforced at POST /bots in meeting-api
  (app.py:163 -> HTTP 409 when the caller's live-bot count reaches
  max_concurrent, which /auth/me reports for this key).

  To exercise it MANUALLY (this SPAWNS real bots — do NOT run in the
  happy-path walk; clean up afterwards with DELETE /bots/<platform>/<native>):

    CAP=\$(curl -fsS -H "X-API-Key: \$TOKEN" $REST_BASE/auth/me | sed -n 's/.*"max_concurrent"[ :]*\\([0-9]*\\).*/\\1/p')
    for i in \$(seq 1 \$((CAP+1))); do
      curl -s -o /dev/null -w '%{http_code}\\n' -X POST $REST_BASE/bots \\
        -H "X-API-Key: \$TOKEN" -H 'Content-Type: application/json' \\
        -d "{\\"platform\\":\\"$PLATFORM\\",\\"native_meeting_id\\":\\"cap-test-\$i\\"}"
    done
    # EXPECT: the first CAP requests -> 201; request CAP+1 -> 409 (cap hit).
    # Then DELETE every cap-test-* native id you just created.
DOC
}

# ===========================================================================
main() {
  hd "Vexa 0.12 dashboard happy-path live walk"
  log "REST base : $REST_BASE"
  log "WS url    : $WS_URL"
  log "meeting   : id=$MEETING_ID platform=$PLATFORM native=${NATIVE_ID:-<unset>}"
  log "stages    : $STAGES   duration=${DURATION}s"
  require_common

  want redis      && stage_redis
  want ws         && stage_ws
  want playback   && stage_playback
  want regression && stage_regression

  hd "RESULT"
  if [ "$FAILED" = 0 ]; then
    ok "all RUN stages passed (skipped stages do not fail the walk)"
    exit 0
  else
    bad "one or more RUN stages failed — see [FAIL] lines above"
    exit 1
  fi
}
main "$@"
