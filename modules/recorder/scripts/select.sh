#!/usr/bin/env bash
# Select captures from S3 by partition + meta.json fields — NO database.
#   select.sh --platform google_meet --min-speakers 3 [--date 2026-06-12]
set -euo pipefail
BUCKET="${TELEMETRY_S3_BUCKET:?set TELEMETRY_S3_BUCKET}"
EP="${TELEMETRY_S3_ENDPOINT:+--endpoint-url $TELEMETRY_S3_ENDPOINT}"
PLATFORM=""; MIN_SPEAKERS=0; DATE=""
while [ $# -gt 0 ]; do case "$1" in
  --platform) PLATFORM="$2"; shift 2;;
  --min-speakers) MIN_SPEAKERS="$2"; shift 2;;
  --date) DATE="$2"; shift 2;;
  *) shift;;
esac; done
PREFIX="telemetry/capture/v1/${PLATFORM:+platform=$PLATFORM/}${DATE:+date=$DATE/}"
# list meta.json under the prefix, filter by num_speakers
aws s3 ls "s3://$BUCKET/$PREFIX" --recursive $EP | awk '{print $4}' | grep '/meta.json$' | while read -r key; do
  ns=$(aws s3 cp "s3://$BUCKET/$key" - $EP 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin).get('num_speakers',0))" 2>/dev/null || echo 0)
  [ "$ns" -ge "$MIN_SPEAKERS" ] && echo "speakers=$ns  s3://$BUCKET/${key%meta.json}"
done
