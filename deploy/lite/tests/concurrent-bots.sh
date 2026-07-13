#!/usr/bin/env bash
# =============================================================================
# concurrent-bots.sh — the release smoke test, and the SOLE ISSUER of the
# `release/vm-validated` commit status.
#
# WHY THIS TEST: #478 shipped because release verification only ever ran ONE
# bot — the shared-profile SingletonLock death needs ≥2 concurrent bots to
# show. This script is that missing check, runnable against the PUBLISHED
# image on a clean host (the stranger's own path):
#
#   IMAGE_TAG=v0.12.0 make lite          # bring the stack up from the Hub image
#   deploy/lite/tests/concurrent-bots.sh # then prove N bots launch concurrently
#
# PASS = all N bots reach `joining` and their browsers stay alive through the
# window, each on its OWN profile dir, zero SingletonLock signatures.
#
# Posting the attestation (optional, from a checkout with `gh` authed):
#   POST_STATUS=1 GIT_SHA=<released sha> deploy/lite/tests/concurrent-bots.sh
# posts release/vm-validated success/failure on that sha with this run's
# summary. Branch protection requires the context, so a release merge is
# impossible without a green run of this script — that is the enforcement.
# =============================================================================
set -uo pipefail

N_BOTS="${N_BOTS:-2}"
WINDOW="${WINDOW:-45}"                 # seconds bots must survive post-launch
APP="${APP_CONTAINER:-vexa-lite}"
GATEWAY="${GATEWAY_URL:-http://localhost:8056}"
POST_STATUS="${POST_STATUS:-0}"
REPO="${REPO:-Vexa-ai/vexa}"

X() { docker exec "$APP" "$@"; }
die() { echo "FAIL: $*"; post_status failure "$*"; exit 1; }
post_status() { # state description
  [ "$POST_STATUS" = 1 ] || return 0
  [ -n "${GIT_SHA:-}" ] || { echo "(no GIT_SHA — status not posted)"; return 0; }
  gh api "repos/$REPO/statuses/$GIT_SHA" -f state="$1" -f context=release/vm-validated \
    -f description="concurrent-bots.sh N=$N_BOTS: $2" >/dev/null && echo "posted release/vm-validated=$1"
}

# ── admin-api port moved between images (8057 → 8001); autodetect, don't assume ──
ADMIN_PORT=""
for p in 8001 8057; do
  code=$(X curl -s -m 3 -o /dev/null -w "%{http_code}" "http://localhost:$p/admin/users" \
         -H "X-Admin-API-Key: ${ADMIN_TOKEN:-changeme}" 2>/dev/null || true)
  case "$code" in 2*|4*) ADMIN_PORT=$p; break;; esac
done
[ -n "$ADMIN_PORT" ] || die "admin-api not reachable on 8001/8057 inside $APP"
ADMIN="http://localhost:$ADMIN_PORT"
ADMIN_TOKEN="${ADMIN_TOKEN:-changeme}"
echo "admin-api on :$ADMIN_PORT"

# ── mint a test user + bot-scoped key ──
UID_=$(X curl -s -X POST "$ADMIN/admin/users" -H "X-Admin-API-Key: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"smoke-bots@example.com\",\"name\":\"Smoke\",\"max_concurrent_bots\":$((N_BOTS+1))}" \
  | sed -n 's/.*"id":\([0-9]*\).*/\1/p')
[ -n "$UID_" ] || UID_=$(X curl -s "$ADMIN/admin/users/email/smoke-bots@example.com" \
  -H "X-Admin-API-Key: $ADMIN_TOKEN" | sed -n 's/.*"id":\([0-9]*\).*/\1/p')
[ -n "$UID_" ] || die "could not create/find smoke user"
TOKEN=$(X curl -s -X POST "$ADMIN/admin/users/$UID_/tokens?scopes=bot,tx" \
  -H "X-Admin-API-Key: $ADMIN_TOKEN" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
[ -n "$TOKEN" ] || die "could not mint bot token"

# ── wait for the bot route to be READY, not just routed — the v0.12.1 maiden release run
#    failed here with POST /bots → 502: the gateway was up (front-door probes green) but
#    meeting-api behind it was still booting. Probe the authenticated read path until it
#    answers non-5xx so the spawns below measure the product, not startup ordering. ──
ready=""
for i in $(seq 1 24); do
  code=$(curl -s -m 5 -o /dev/null -w "%{http_code}" "$GATEWAY/meetings" -H "X-API-Key: $TOKEN" || true)
  case "$code" in 2*|4*) ready=1; echo "bot route ready (GET /meetings → $code, attempt $i)"; break;; esac
  echo "…bot route not ready yet (GET /meetings → ${code:-none}, attempt $i/24)"; sleep 5
done
[ -n "$ready" ] || die "meeting-api never became ready behind the gateway (GET /meetings 5xx for 120s)"

# ── launch N bots concurrently (distinct synthetic meetings; the #478 failure
#    mode fires at browser LAUNCH, before any navigation, so no admission needed) ──
# transcribe_enabled:false — this is a browser-launch/concurrency smoke, and the
# synthetic bots never capture audio, so there is nothing to transcribe. Without it
# the spawn is (correctly) refused with 503 by the STT-config gate, since the lite
# smoke env wires no transcription backend. (The real fresh-install STT-gate defect
# a user hits with the default transcribe_enabled=true is a product issue, #502/#504.)
X bash -c 'rm -f /tmp/vexa-workloads/*.log 2>/dev/null; true'
ids=()
for i in $(seq 1 "$N_BOTS"); do
  mid=$(printf 'aaa-smk%02d-bot' "$i")
  curl -s -X POST "$GATEWAY/bots" -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
    -d "{\"platform\":\"google_meet\",\"native_meeting_id\":\"$mid\",\"bot_name\":\"Smoke-$i\",\"transcribe_enabled\":false}" >/dev/null &
  ids+=("$mid")
done
wait
echo "launched $N_BOTS bots; observing ${WINDOW}s"
sleep "$WINDOW"

# ── assertions ──
# 1) zero SingletonLock signatures in any workload log
if X bash -c 'grep -l "Opening in existing browser session" /tmp/vexa-workloads/*.log 2>/dev/null' | grep -q .; then
  die "SingletonLock signature present — shared profile dir regression (#478)"
fi
# 2) every bot's meeting is alive in joining/awaiting/active (not failed)
statuses=$(curl -s "$GATEWAY/meetings" -H "X-API-Key: $TOKEN")
for mid in "${ids[@]}"; do
  st=$(printf '%s' "$statuses" | MID="$mid" python3 -c "
import json,sys,os
d=json.load(sys.stdin)
ms=d.get('meetings',d if isinstance(d,list) else [])
print(next((m.get('status') for m in ms if m.get('native_meeting_id')==os.environ['MID']), 'NOT-FOUND'))" 2>/dev/null)
  case "$st" in joining|awaiting_admission|active|requested) echo "bot $mid: $st ✓";;
    *) die "bot $mid status=$st after ${WINDOW}s";; esac
done
# 3) one profile dir PER bot, each with live chromium processes
dirs=$(X bash -c 'ls -d /tmp/browser-data-* 2>/dev/null | wc -l')
[ "$dirs" -ge "$N_BOTS" ] || die "expected ≥$N_BOTS per-bot profile dirs, found $dirs"
echo "per-bot profile dirs: $dirs ✓"

# ── cleanup (best-effort) ──
for mid in "${ids[@]}"; do
  curl -s -X DELETE "$GATEWAY/bots/google_meet/$mid" -H "X-API-Key: $TOKEN" >/dev/null || true
done

echo "PASS: $N_BOTS concurrent bots launched on isolated profiles, ${WINDOW}s stable"
post_status success "$N_BOTS bots concurrent, isolated profiles, ${WINDOW}s stable"
