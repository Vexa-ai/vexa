#!/usr/bin/env bash
# debug-rate — fire repeated joins at a controlled cadence from THIS host's
# egress IP, logging each outcome, to find where the platform starts to
# rate-limit / block the IP by attempt frequency.
#
#   COUNT=10 GAP=30 URLS=urls.txt bash scripts/debug-rate.sh
#   (for a DATACENTER egress: ssh to a cloud box, rsync this dir, run there)
#
#   URLS  = file with one FRESH meeting URL per line — rate-limits key on attempt
#           frequency from an IP, not on the room, so use distinct meetings.
#   COUNT = max attempts (default 10).  GAP = seconds between attempts (default 30).
#
# Outcome per attempt → /tmp/mj-rate.log. The attempt # where JOIN-STATE flips to
# `blocked` is the throttle threshold for this egress at this cadence.
#
# NOTE: reliable *automatic* block detection is #444 (the visible-challenge /
# blank-page detector). Until it lands, also watch the live browser at
# http://localhost:6080/vnc.html — a black/blank page or a captcha is the block.
set -euo pipefail

COUNT=${COUNT:-10}; GAP=${GAP:-30}
URLS=${URLS:?set URLS=<file with one meet url per line>}
IMAGE=meet-join-debug
LOG=/tmp/mj-rate.log

docker image inspect "$IMAGE" >/dev/null 2>&1 || make image
: > "$LOG"
echo "rate probe: up to $COUNT attempts, ${GAP}s apart, egress = $(curl -s --max-time 5 ifconfig.me || echo '?')"

i=0
while IFS= read -r url; do
  [ -z "${url// }" ] && continue
  i=$((i + 1)); [ "$i" -gt "$COUNT" ] && break
  ts=$(date +%H:%M:%S)
  state=$(docker run --rm \
            -v "$PWD/src:/pkg/src" -v "$PWD/scripts:/pkg/scripts" \
            -e MEETING_URL="$url" "$IMAGE" 2>&1 \
          | grep -oE "JOIN-STATE: [a-z_]+" | tail -1 || true)
  line="[$ts] #$i  ${state:-JOIN-STATE: ?}  ($url)"
  echo "$line" | tee -a "$LOG"
  sleep "$GAP"
done < "$URLS"

echo "→ $LOG — attempt # where JOIN-STATE flips to 'blocked' = the throttle threshold."
