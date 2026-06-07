#!/usr/bin/env bash
# Local bot debug helper for the stitch hot-mount stack.
# Spawns a bot to MEET_URL against the LOCAL compose stack and tails its logs.
# The bot runs the bind-mounted host dist (services/vexa-bot/core/dist), so
#   edit src -> `npx tsc --skipLibCheck` -> re-run this -> new code runs. No image rebuild.
#
# Usage: make bot-debug MEET_URL=https://meet.google.com/xxx-xxxx-xxx
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[ -f .env ] || { echo "ERROR: no .env in $ROOT — is the stack set up?"; exit 1; }

: "${MEET_URL:?usage: make bot-debug MEET_URL=https://meet.google.com/xxx-xxxx-xxx}"

# --- meet code from URL (xxx-xxxx-xxx) ---
CODE="$(printf '%s' "$MEET_URL" | sed -E 's#^https?://meet\.google\.com/##; s#[/?].*$##')"
[ -n "$CODE" ] || { echo "ERROR: could not parse meet code from $MEET_URL"; exit 1; }

# --- stack coordinates from .env ---
get(){ grep -E "^$1=" .env | head -1 | cut -d= -f2-; }
PROJ="$(get COMPOSE_PROJECT_NAME)"; PROJ="${PROJ:-stitchdebug}"
GW="$(get API_GATEWAY_HOST_PORT)"; GW="${GW:-28076}"
ADM="$(get ADMIN_API_PORT)"; ADM="${ADM:-28077}"
ADMTOK="$(get ADMIN_TOKEN)"

# --- API token: reuse cached, else mint via admin-api ---
TF="/tmp/.${PROJ}_token"
TOK="${BOT_DEBUG_TOKEN:-}"
if [ -z "$TOK" ] && [ -f "$TF" ]; then TOK="$(cat "$TF")"; fi
if [ -z "$TOK" ]; then
  UID2="$(curl -s -X POST "http://localhost:$ADM/admin/users" -H 'Content-Type: application/json' \
    -H "X-Admin-API-Key: $ADMTOK" -d '{"email":"botdebug@vexa.ai","name":"botdebug"}' \
    | python3 -c 'import json,sys;print(json.load(sys.stdin).get("id",""))' 2>/dev/null || true)"
  TOK="$(curl -s -X POST "http://localhost:$ADM/admin/users/$UID2/tokens" -H "X-Admin-API-Key: $ADMTOK" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin).get("token",""))' 2>/dev/null || true)"
  [ -n "$TOK" ] && printf '%s' "$TOK" > "$TF"
fi
[ -n "$TOK" ] || { echo "ERROR: could not obtain an API token (check admin-api on :$ADM)"; exit 1; }

echo "→ spawning bot to meet '$CODE' via local gateway :$GW (project $PROJ)"
RESP="$(curl -s -X POST "http://localhost:$GW/bots" -H 'Content-Type: application/json' \
  -H "X-API-Key: $TOK" -d "{\"platform\":\"google_meet\",\"native_meeting_id\":\"$CODE\",\"bot_name\":\"dbg\"}")"
CID="$(printf '%s' "$RESP" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("bot_container_id",""))' 2>/dev/null || true)"
[ -n "$CID" ] || { echo "ERROR: bot request failed: $RESP"; exit 1; }

echo "→ bot container: $CID   (running bind-mounted host dist)"
echo "→ tailing logs (Ctrl-C to stop; the bot keeps running):"
echo "------------------------------------------------------------"
# wait for the container to appear, then follow
for _ in $(seq 1 20); do docker inspect "$CID" >/dev/null 2>&1 && break; sleep 1; done
exec docker logs -f "$CID"
