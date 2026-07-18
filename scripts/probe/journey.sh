#!/usr/bin/env bash
# =============================================================================
# scripts/probe/journey.sh — the standing full-journey smoke probe (`make probe`).
#
# ONE journey, driven entirely through the surface's gateway front door:
#   spawn → schedule → boot → join → transcribe → live-view → stop
# followed by a ONE-SHOT all-component log sweep (the parallel failure
# inventory). Every stage prints Expected / Actual / Verdict; any RED fails the
# command (exit 1) — the sweep still runs, so a red probe ships its failure
# inventory in the same output. No real meeting, no audio, no human: the
# journey reaches a bounded, truthful terminal state in minutes.
#
# Modes (PROBE_MODE):
#   mock — bot_name=mock:<scenario> against a stack whose BROWSER_IMAGE is the
#          mock bot (mock-bot:dev): a deterministic green full journey,
#          transcript segments included.
#   real — the real bot at a dead synthetic meeting URL: the journey's truthful
#          terminal is a NAMED failure (join_failure) — never a fake green.
#
# Journey stages chain (a red stage skips the rest of the chain); the surface
# probes S5 (WS feed) · S6 (SSE live-view) · S7 (stop route) and the S8 sweep
# run whenever the gateway is up, so ONE red run still ships the whole
# failure inventory.
#
# env (set by the per-surface wrapper — deploy/{compose,lite,helm}/probe.sh):
#   GATEWAY_URL           (required) the surface's gateway front door
#   VEXA_API_KEY          (required) a bot,tx-scoped API key
#   PROBE_MODE            mock | real                    (default real)
#   PROBE_SWEEP_CMD       the surface's all-component log-sweep shell command
#   PROBE_BOOT_TIMEOUT    seconds for the bot to leave `requested`  (default 180)
#   PROBE_SETTLE_TIMEOUT  seconds to reach a terminal state (default mock 120 / real 300)
#   PLATFORM              meeting platform               (default google_meet)
# =============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${GATEWAY_URL:?set GATEWAY_URL (the gateway front door of the surface)}"
: "${VEXA_API_KEY:?set VEXA_API_KEY (a bot,tx-scoped API key)}"
GATEWAY_URL="${GATEWAY_URL%/}"
MODE="${PROBE_MODE:-real}"
PLATFORM="${PLATFORM:-google_meet}"
BOOT_TIMEOUT="${PROBE_BOOT_TIMEOUT:-180}"
if [ "$MODE" = mock ]; then SETTLE_TIMEOUT="${PROBE_SETTLE_TIMEOUT:-120}"; else SETTLE_TIMEOUT="${PROBE_SETTLE_TIMEOUT:-300}"; fi
NATIVE="${PROBE_NATIVE_ID:-aaa-prb$(date +%s | tail -c 5)-bot}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/vexa-probe.XXXXXX")"
WS_PID=""
trap '[ -n "$WS_PID" ] && kill "$WS_PID" 2>/dev/null; rm -rf "$WORK"' EXIT

# ── stage bookkeeping ────────────────────────────────────────────────────────
FAIL=0            # any red anywhere → exit 1
JOURNEY=1         # the S1→S4 chain: a red link skips the rest of the chain
SUMMARY=()
stage()    { printf '\n━━ %s ━━\n' "$1"; }
expected() { printf '   Expected: %s\n' "$1"; }
actual()   { printf '   Actual:   %s\n' "$1"; }
green()    { printf '   Verdict:  GREEN\n'; SUMMARY+=("GREEN  $1"); }
red()      { printf '   Verdict:  RED — %s\n' "$2"; SUMMARY+=("RED    $1 — $2"); FAIL=1; }
skipped()  { SUMMARY+=("SKIP   $1 — upstream red"); printf '\n━━ %s ━━\n   Verdict:  SKIPPED (upstream red)\n' "$1"; }

# ── HTTP helpers ─────────────────────────────────────────────────────────────
CODE=""; BODY=""
api() { # method path [json-body] → sets CODE + BODY
  local m="$1" p="$2" d="${3:-}" out
  if [ -n "$d" ]; then
    out="$(curl -sS -m 20 -X "$m" "$GATEWAY_URL$p" -H "X-API-Key: $VEXA_API_KEY" \
           -H 'Content-Type: application/json' -d "$d" -w '\n%{http_code}' 2>&1)"
  else
    out="$(curl -sS -m 20 -X "$m" "$GATEWAY_URL$p" -H "X-API-Key: $VEXA_API_KEY" -w '\n%{http_code}' 2>&1)"
  fi
  CODE="$(printf '%s' "$out" | tail -1)"
  BODY="$(printf '%s' "$out" | sed '$d')"
}

# meeting row for a native id → "status|id|session_uid|completion_reason|failure_stage" (or empty)
row() {
  api GET /meetings
  printf '%s' "$BODY" | NATIVE="$1" python3 -c '
import json, os, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
ms = d.get("meetings", d if isinstance(d, list) else [])
m = next((m for m in ms if m.get("native_meeting_id") == os.environ["NATIVE"]), None)
if m:
    data = m.get("data") or {}
    print("|".join(str(x if x is not None else "") for x in (
        m.get("status"), m.get("id"), m.get("session_uid"),
        data.get("completion_reason"), data.get("failure_stage"))))
' 2>/dev/null
}

# wait until the row's status matches the regex; echoes the final row. Args: native regex timeout
wait_status() {
  local native="$1" re="$2" timeout="$3" deadline r st
  deadline=$(( $(date +%s) + timeout ))
  while :; do
    r="$(row "$native")"; st="${r%%|*}"
    if [ -n "$st" ] && printf '%s' "$st" | grep -qE "$re"; then echo "$r"; return 0; fi
    [ "$(date +%s)" -ge "$deadline" ] && { echo "$r"; return 1; }
    sleep 3
  done
}

TERMINAL='^(completed|failed)$'

echo "═══ vexa full-journey probe · mode=$MODE · gateway=$GATEWAY_URL · meeting=$PLATFORM/$NATIVE ═══"

# ── S0 · ready — the bot route answers, not just routes ──────────────────────
stage "S0 ready"
expected "GET /health 200 and authed GET /meetings non-5xx (meeting-api ready behind the gateway)"
READY=""
hc=""
for i in $(seq 1 24); do
  hc="$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$GATEWAY_URL/health" || true)"
  api GET /meetings
  case "$hc:$CODE" in 200:2*|200:4*) READY=1; break;; esac
  sleep 5
done
actual "/health → ${hc:-none} · GET /meetings → ${CODE:-none}"
if [ -n "$READY" ]; then green "S0 ready"; else red "S0 ready" "gateway/meeting-api never became ready (120s)"; JOURNEY=0; fi

# ── the live-view listener: subscribe /ws BEFORE spawning, so live frames are caught ──
WS_LOG="$WORK/ws.jsonl"
if [ -n "$READY" ]; then
  python3 "$HERE/ws_tail.py" "$GATEWAY_URL" "$VEXA_API_KEY" "$PLATFORM" "$NATIVE" \
    "$(( SETTLE_TIMEOUT + 30 ))" "$WS_LOG" >/dev/null 2>&1 &
  WS_PID=$!
fi

# ── S1 · spawn ───────────────────────────────────────────────────────────────
if [ "$JOURNEY" = 1 ]; then
  stage "S1 spawn"
  if [ "$MODE" = mock ]; then BOT_NAME="mock:normal"; else BOT_NAME="probe"; fi
  # transcribe_enabled=false: the probe carries no audio, and a default spawn is (correctly)
  # refused 503 by the STT-config gate on stacks with no transcription creds. The mock bot
  # emits its transcript segments regardless, so the transcribe stage still proves the dataflow.
  expected "POST /bots ($BOT_NAME) → 201 + a meeting row id"
  api POST /bots "{\"platform\":\"$PLATFORM\",\"native_meeting_id\":\"$NATIVE\",\"bot_name\":\"$BOT_NAME\",\"transcribe_enabled\":false}"
  actual "→ $CODE $(printf '%s' "$BODY" | head -c 200)"
  case "$CODE" in
    201|200) green "S1 spawn";;
    *) red "S1 spawn" "POST /bots → $CODE (bot never requested)"; JOURNEY=0;;
  esac
else skipped "S1 spawn"; fi

# ── S2 · schedule ────────────────────────────────────────────────────────────
if [ "$JOURNEY" = 1 ]; then
  stage "S2 schedule"
  expected "the meeting row appears in GET /meetings (status=requested counts) within 20s"
  R="$(wait_status "$NATIVE" '.' 20)" || true
  actual "row: ${R:-<absent>}"
  if [ -n "$R" ]; then green "S2 schedule"; else red "S2 schedule" "meeting row never appeared"; JOURNEY=0; fi
else skipped "S2 schedule"; fi

# ── S3 · boot — the discriminator: a broken spawn parks the row at `requested` ───────────────
if [ "$JOURNEY" = 1 ]; then
  stage "S3 boot"
  expected "status leaves 'requested' (bot booted + first lifecycle callback) ≤ ${BOOT_TIMEOUT}s"
  if R="$(wait_status "$NATIVE" '^(joining|awaiting_admission|active|stopping|completed|failed)$' "$BOOT_TIMEOUT")"; then
    actual "row: $R"
    green "S3 boot"
  else
    actual "row: ${R:-<absent>} — stuck at spawn/Running: the runtime never booted a bot that called back"
    red "S3 boot" "bot never left 'requested' in ${BOOT_TIMEOUT}s (spawn/Running stage — runtime/image/boot)"
    JOURNEY=0
  fi
else skipped "S3 boot"; fi

# ── S4 · join ────────────────────────────────────────────────────────────────
if [ "$JOURNEY" = 1 ]; then
  stage "S4 join"
  if [ "$MODE" = mock ]; then
    expected "mock:normal → admitted → active → self-ends → completed ≤ ${SETTLE_TIMEOUT}s"
    if R="$(wait_status "$NATIVE" '^completed$' "$SETTLE_TIMEOUT")"; then
      actual "row: $R"; green "S4 join"
    else
      actual "row: ${R:-<absent>}"; red "S4 join" "mock journey did not reach completed in ${SETTLE_TIMEOUT}s"; JOURNEY=0
    fi
  else
    expected "dead meeting URL → a TRUTHFUL named terminal (failed + completion_reason/failure_stage) ≤ ${SETTLE_TIMEOUT}s"
    if R="$(wait_status "$NATIVE" "$TERMINAL" "$SETTLE_TIMEOUT")"; then
      IFS='|' read -r st _id _sess reason fstage <<<"$R"
      actual "terminal: $st (reason=${reason:-<none>} stage=${fstage:-<none>})"
      if [ "$st" = failed ] && [ -z "$reason" ]; then
        red "S4 join" "failed with NO attributable reason (P18: failures must name themselves)"
      else
        green "S4 join"
      fi
    else
      actual "row: ${R:-<absent>} — no terminal in ${SETTLE_TIMEOUT}s"
      red "S4 join" "dead-URL journey never reached a truthful terminal (status=${R%%|*})"
    fi
  fi
else skipped "S4 join"; fi

# ── S5 · transcribe — the WS subscribe feed (live frames caught since pre-spawn) ─────────────
if [ -n "$READY" ]; then
  stage "S5 transcribe"
  if [ "$MODE" = mock ]; then
    expected "WS /ws subscribe acked AND ≥1 transcript reaches the client (live frame or durable GET /transcripts)"
  else
    expected "WS /ws subscribe acked (no meeting was joined, so 0 segments is the truthful count)"
  fi
  ACK=""
  for i in $(seq 1 10); do
    grep -q '"type": *"subscribed"' "$WS_LOG" 2>/dev/null && { ACK=1; break; }; sleep 2
  done
  FRAMES="$(grep -c '"type": *"transcript"' "$WS_LOG" 2>/dev/null || true)"; FRAMES="${FRAMES:-0}"
  api GET "/transcripts/$PLATFORM/$NATIVE"
  SEGS="$(printf '%s' "$BODY" | python3 -c 'import json,sys
try: print(len(json.load(sys.stdin).get("segments") or []))
except Exception: print(0)' 2>/dev/null)"
  actual "ws ack=${ACK:-no} · live transcript frames=$FRAMES · durable segments=${SEGS:-0} (GET /transcripts → $CODE)"
  if [ -z "$ACK" ]; then
    red "S5 transcribe" "WS /ws subscribe never acked"
  elif [ "$MODE" = mock ] && [ "${FRAMES:-0}" -eq 0 ] && [ "${SEGS:-0}" -eq 0 ]; then
    red "S5 transcribe" "mock emitted segments but none reached the WS client or the durable transcript"
  else
    green "S5 transcribe"
  fi
else skipped "S5 transcribe"; fi

# ── S6 · live-view — the SSE feed fetch ──────────────────────────────────────
if [ -n "$READY" ]; then
  stage "S6 live-view"
  expected "GET /agent/meeting/stream (SSE) → 200 text/event-stream for the probe's own meeting"
  R="$(row "$NATIVE")"; IFS='|' read -r _st MID _SESS _r _f <<<"$R"
  SESS="${_SESS:-$NATIVE}"
  HDRS="$WORK/sse.hdrs"
  curl -s -N -m 6 -D "$HDRS" -o "$WORK/sse.body" \
    -H "X-API-Key: $VEXA_API_KEY" \
    "$GATEWAY_URL/agent/meeting/stream?meeting_id=${MID:-0}&session_uid=$SESS" || true
  SSE_STATUS="$(head -1 "$HDRS" 2>/dev/null | tr -d '\r')"
  SSE_CT="$(grep -i '^content-type:' "$HDRS" 2>/dev/null | head -1 | tr -d '\r')"
  actual "${SSE_STATUS:-<no response>} · ${SSE_CT:-<no content-type>}"
  if printf '%s' "$SSE_STATUS" | grep -q ' 200' && printf '%s' "$SSE_CT" | grep -qi 'text/event-stream'; then
    green "S6 live-view"
  else
    red "S6 live-view" "SSE feed did not answer 200 text/event-stream"
  fi
else skipped "S6 live-view"; fi

# ── S7 · stop ────────────────────────────────────────────────────────────────
if [ -n "$READY" ]; then
  stage "S7 stop"
  if [ "$MODE" = mock ]; then
    # mock:normal self-ends, so the stop leg gets its OWN bot: immediate-stop runs until the
    # backend drives the leave — exactly the DELETE /bots path a user exercises.
    STOP_NATIVE="aaa-prb$(date +%s | tail -c 5)-stp"
    expected "spawn mock:immediate-stop → active, then DELETE /bots → terminal"
    api POST /bots "{\"platform\":\"$PLATFORM\",\"native_meeting_id\":\"$STOP_NATIVE\",\"bot_name\":\"mock:immediate-stop\",\"transcribe_enabled\":false}"
    SPAWN_CODE="$CODE"
    R="$(wait_status "$STOP_NATIVE" '^(active|awaiting_admission)$' 90)" || true
    api DELETE "/bots/$PLATFORM/$STOP_NATIVE"
    DEL_CODE="$CODE"
    if R2="$(wait_status "$STOP_NATIVE" "$TERMINAL" 60)"; then STOPPED=1; else STOPPED=""; fi
    actual "spawn→$SPAWN_CODE · pre-stop row: ${R:-<absent>} · DELETE→$DEL_CODE · post-stop row: ${R2:-<absent>}"
    if [ -n "$STOPPED" ] && case "$DEL_CODE" in 2*) true;; *) false;; esac; then
      green "S7 stop"
    else
      red "S7 stop" "DELETE /bots did not drive the bot to a terminal state"
    fi
  else
    R="$(row "$NATIVE")"; ST="${R%%|*}"
    if [ -n "$ST" ] && ! printf '%s' "$ST" | grep -qE "$TERMINAL"; then
      expected "DELETE /bots on the still-running bot → 2xx and a terminal state ≤ 90s"
      api DELETE "/bots/$PLATFORM/$NATIVE"; DEL_CODE="$CODE"
      if R2="$(wait_status "$NATIVE" "$TERMINAL" 90)"; then STOPPED=1; else STOPPED=""; fi
      actual "DELETE→$DEL_CODE · post-stop row: ${R2:-<absent>}"
      if [ -n "$STOPPED" ] && case "$DEL_CODE" in 2*) true;; *) false;; esac; then
        green "S7 stop"; else red "S7 stop" "DELETE /bots did not drive the bot to a terminal state"; fi
    else
      expected "bot already terminal (${ST:-absent}) — the stop route still ANSWERS (non-5xx)"
      api DELETE "/bots/$PLATFORM/$NATIVE"
      actual "DELETE → $CODE"
      case "$CODE" in 2*|4*) green "S7 stop";; *) red "S7 stop" "DELETE /bots → ${CODE:-none} (route dead)";; esac
    fi
  fi
else skipped "S7 stop"; fi

if [ -n "$WS_PID" ]; then kill "$WS_PID" 2>/dev/null; wait "$WS_PID" 2>/dev/null; WS_PID=""; fi

# ── S8 · log sweep — every component's logs, ONCE, together (the parallel inventory) ─────────
stage "S8 log sweep"
if [ -n "${PROBE_SWEEP_CMD:-}" ]; then
  bash -c "$PROBE_SWEEP_CMD" || true
  SUMMARY+=("DONE   S8 log sweep")
else
  echo "   (no PROBE_SWEEP_CMD set by the surface wrapper — sweep skipped)"
  SUMMARY+=("SKIP   S8 log sweep — no sweep command")
fi

# ── verdict ──────────────────────────────────────────────────────────────────
printf '\n═══ PROBE %s · mode=%s · %s/%s ═══\n' "$([ "$FAIL" = 0 ] && echo PASS || echo FAIL)" "$MODE" "$PLATFORM" "$NATIVE"
for line in "${SUMMARY[@]}"; do printf '  %s\n' "$line"; done
exit "$FAIL"
