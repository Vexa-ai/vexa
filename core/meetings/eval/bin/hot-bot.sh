#!/usr/bin/env bash
# hot-bot — run the listening bot FROM LOCAL SOURCE against the real staging path.
#
# A code change costs a process restart, not an image build. The transcript still travels
# the full production path (real STT, real redis, real collector) and lands on the REAL
# dashboard a customer sees — never a viewer we invent.
#
#   ./bin/hot-bot.sh <platform> <meeting-url>
#     ./bin/hot-bot.sh teams 'https://teams.microsoft.com/meet/123?p=abc'
#
# HOW IT STAYS FAITHFUL: meeting-api mints the bot's whole invocation into ONE env var
# (VEXA_BOT_CONFIG, invocation.v1 / ADR-0002). We let it spawn normally, LIFT that payload
# off the pod, delete the pod, rewrite only the in-cluster hostnames to local forwards, and
# run the same bytes locally. Nothing about the config is hand-rolled, so a hot run and a
# deployed run differ in exactly one variable: which machine executes the TypeScript.
#
# SECURITY: this points a local process at STAGING. Never prod. The invocation carries a
# meeting token and an STT token — it is written to a 0600 file and never echoed.
set -uo pipefail

PLATFORM="${1:?platform (teams|google_meet|zoom|jitsi)}"
MEETING_URL="${2:?meeting url}"
BOT_NAME="${HOT_BOT_NAME:-Vexa HotLocal}"
NS=vexa-staging
API="${VEXA_API:-https://api.staging.vexa.ai}"
DASH="${VEXA_DASHBOARD:-https://dashboard.staging.vexa.ai}"
KEYFILE="${VEXA_API_KEYFILE:?VEXA_API_KEYFILE must point at a file holding the staging API key}"
HERE="$(cd "$(dirname "$0")/../.." && pwd)"             # core/meetings
BOT="$HERE/services/bot"
RUN="${HOT_BOT_RUN:-$HOME/vexa-test-rig/hot-bot/$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$RUN"; chmod 700 "$RUN"

# Local ports for the cluster-internal deps the bot writes to.
P_REDIS=16379; P_STT=18500; P_MAPI=18080
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/vexa-platform.yaml}"

fwd() {  # svc local remote — idempotent; a live forward is reused
  local svc=$1 lp=$2 rp=$3
  if nc -z 127.0.0.1 "$lp" 2>/dev/null; then echo "  ✓ :$lp already forwarded ($svc)"; return; fi
  kubectl -n $NS port-forward "svc/$svc" "$lp:$rp" >>"$RUN/portforward.log" 2>&1 &
  for _ in $(seq 1 40); do nc -z 127.0.0.1 "$lp" 2>/dev/null && { echo "  ✓ :$lp → $svc:$rp"; return; }; sleep 0.25; done
  echo "  ✗ could not forward $svc:$rp → :$lp — see $RUN/portforward.log" >&2; exit 1
}

echo "▶ forwarding cluster deps"
fwd vexa-platform-vexa-redis            $P_REDIS 6379
fwd vexa-platform-transcription-gateway $P_STT   8084
fwd vexa-platform-vexa-meeting-api      $P_MAPI  8080

echo "▶ asking meeting-api to mint a real invocation"
# The native id is the meeting's IDENTITY, not its URL. Get this wrong and the record still
# spawns and still transcribes, but the dashboard builds /transcripts/<platform>/<native> from
# it — a native id carrying "https://" and slashes makes that URL malformed, the client's fetch
# throws, and the page renders "This page couldn't load" over a perfectly healthy meeting.
NATIVE=$(python3 - "$PLATFORM" "$MEETING_URL" <<'PY'
import re, sys
from urllib.parse import urlparse
platform, url = sys.argv[1], sys.argv[2]
path = urlparse(url).path.strip('/')
if platform == 'teams':
    m = re.search(r'meetup-join/([^/?]+)', url) or re.search(r'/meet/([^/?]+)', url)
    out = m.group(1) if m else path
else:                       # jitsi room name · gmeet code · zoom id — the last path segment
    out = path.split('/')[-1]
if not out or '/' in out:
    sys.exit(f'cannot derive a native meeting id from {url!r}')
print(out)
PY
) || { echo "✗ $NATIVE" >&2; exit 1; }
SPAWN=$(curl -s --max-time 40 -X POST "$API/bots" -H "X-API-Key: $(cat "$KEYFILE")" \
  -H 'Content-Type: application/json' \
  -d "{\"platform\":\"$PLATFORM\",\"native_meeting_id\":\"$NATIVE\",\"meeting_url\":\"$MEETING_URL\",\"bot_name\":\"$BOT_NAME\"}")
MEETING_ID=$(printf '%s' "$SPAWN" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("id",""))')
POD=$(printf '%s' "$SPAWN" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("bot_container_id",""))')
[ -n "$MEETING_ID" ] && [ -n "$POD" ] || { echo "✗ spawn failed: $SPAWN" >&2; exit 1; }
echo "  meeting $MEETING_ID · pod $POD"

echo "▶ lifting VEXA_BOT_CONFIG off the pod"
for _ in $(seq 1 60); do
  RAW=$(kubectl -n $NS get pod "vexa-$POD" -o jsonpath='{.spec.containers[0].env[?(@.name=="VEXA_BOT_CONFIG")].value}' 2>/dev/null)
  [ -n "$RAW" ] && break; sleep 1
done
[ -n "$RAW" ] || { echo "✗ pod never exposed VEXA_BOT_CONFIG" >&2; exit 1; }

echo "▶ retiring the cluster pod — the local process takes this session"
kubectl -n $NS delete pod "vexa-$POD" --now >/dev/null 2>&1

# Rewrite ONLY the in-cluster hostnames. Everything else — tokens, ids, timeouts, feature
# flags — is the payload meeting-api minted, byte for byte.
printf '%s' "$RAW" | python3 -c "
import sys, json
inv = json.load(sys.stdin)
sub = {'vexa-platform-vexa-redis:6379': '127.0.0.1:$P_REDIS',
       'vexa-platform-transcription-gateway:8084': '127.0.0.1:$P_STT',
       'vexa-platform-vexa-meeting-api:8080': '127.0.0.1:$P_MAPI',
       'meeting-api:8080': '127.0.0.1:$P_MAPI'}
def fix(v):
    if isinstance(v, str):
        for a, b in sub.items(): v = v.replace(a, b).replace(a.split(':')[0] + '.$NS.svc.cluster.local:' + a.split(':')[1], b)
    return v
inv = {k: fix(v) for k, v in inv.items()}
inv['captureSignalEnabled'] = True          # a hot run is a fixture-producing run
json.dump(inv, open('$RUN/invocation.json', 'w'))
print('  rewrote: ' + ', '.join(k for k, v in inv.items() if isinstance(v, str) and '127.0.0.1' in v))
" || { echo "✗ invocation rewrite failed" >&2; exit 1; }
chmod 600 "$RUN/invocation.json"

echo
echo "═══════════════════════════════════════════════════════════════"
echo "  WATCH:  $DASH/meetings/$MEETING_ID"
echo "  source: $(cd "$HERE" && git log --oneline -1)"
echo "  run:    $RUN"
echo "═══════════════════════════════════════════════════════════════"
echo
# The page-side capture bundle ships baked into the container image at /app. Outside it we
# must build it and point the bot at it — without this the browser injects nothing and the
# bot captures NO audio while looking otherwise healthy (it joins, it reports admitted).
# Build with tsc — the SAME compiler the image uses — and run dist/, not the sources.
# `tsx` is tempting for a hot loop but it is not faithful here: its esbuild transform wraps
# functions for keepNames, and a wrapped function handed to page.evaluate() carries a
# `__name` reference the page has never heard of, so every page-side bridge dies with
# "ReferenceError: __name is not defined" while the bot still joins and reports healthy.
# tsc costs seconds, not the fifteen minutes an image costs. That is still hot.
echo "▶ building (tsc + page-side capture bundle)"
(cd "$BOT" && npm run --silent build >>"$RUN/build.log" 2>&1) \
  || { echo "✗ build failed — see $RUN/build.log" >&2; exit 1; }
BUNDLE="$BOT/dist/browser-utils.global.js"
[ -s "$BUNDLE" ] || { echo "✗ bundle missing at $BUNDLE" >&2; exit 1; }
echo "  ✓ dist/ + $(wc -c <"$BUNDLE" | tr -d ' ')-byte page bundle"

cd "$BOT" && VEXA_BOT_CONFIG="$(cat "$RUN/invocation.json")" \
  VEXA_CAPTURE_SIGNAL_DIR="$RUN/capture" \
  VEXA_BROWSER_UTILS_PATH="$BUNDLE" \
  node dist/index.js 2>&1 | tee "$RUN/bot.log"
