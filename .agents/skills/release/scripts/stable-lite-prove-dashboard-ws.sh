#!/usr/bin/env bash
set -euo pipefail

FIXTURE="${FIXTURE:-.agents/releases/0.10.6.3/baseline/ws-fixture.json}"
OUT="${OUT:-.agents/releases/0.10.6.3/baseline/dashboard-ws-frame-proof.json}"
PROOF_STDOUT="${PROOF_STDOUT:-.agents/releases/0.10.6.3/baseline/dashboard-ws-frame-proof.stdout.log}"
PROOF_STDERR="${PROOF_STDERR:-.agents/releases/0.10.6.3/baseline/dashboard-ws-frame-proof.stderr.log}"
LITE_CONTAINER="${LITE_CONTAINER:-vexa-1063-lite}"
PUBLISH_COUNT="${PUBLISH_COUNT:-8}"
PUBLISH_DELAY_SECONDS="${PUBLISH_DELAY_SECONDS:-2}"
INITIAL_WAIT_SECONDS="${INITIAL_WAIT_SECONDS:-8}"
TIMEOUT_MS="${TIMEOUT_MS:-45000}"

TOKEN="$(jq -r .token "$FIXTURE")"
MEETING_ID="$(jq -r .meeting_id "$FIXTURE")"
NATIVE_ID="$(jq -r .native_id "$FIXTURE")"
DASHBOARD_URL="$(jq -r .dashboard_url "$FIXTURE")"
EXPECT_TEXT="${EXPECT_TEXT:-stable-0106-live-ws-$(date +%s)}"

mkdir -p "$(dirname "$OUT")"

env DASHBOARD_AUTH_TOKEN="$TOKEN" \
  .agents/skills/release/scripts/dashboard-ws-frame-proof.mjs \
    --dashboard-url "$DASHBOARD_URL" \
    --meeting-id "$MEETING_ID" \
    --platform google_meet \
    --native-id "$NATIVE_ID" \
    --auth-cookie-name vexa-token \
    --auth-token "$TOKEN" \
    --legacy-native-only \
    --expect-text "$EXPECT_TEXT" \
    --out "$OUT" \
    --timeout-ms "$TIMEOUT_MS" \
    > "$PROOF_STDOUT" 2> "$PROOF_STDERR" &
PROOF_PID=$!

sleep "$INITIAL_WAIT_SECONDS"
for i in $(seq 1 "$PUBLISH_COUNT"); do
  frame_text="$EXPECT_TEXT frame-$i"
  seg_id="stable-0106-$i"
  payload="$(
    jq -cn \
      --arg text "$frame_text" \
      --arg speaker "Stable Baseline" \
      --arg seg_id "$seg_id" \
      --argjson mid "$MEETING_ID" \
      '{
        type: "transcript",
        meeting: { id: $mid },
        speaker: $speaker,
        confirmed: [{
          segment_id: $seg_id,
          start: 0,
          end_time: 1,
          absolute_start_time: "2026-05-23T00:00:00Z",
          absolute_end_time: "2026-05-23T00:00:01Z",
          text: $text,
          speaker: $speaker,
          language: "en",
          completed: true
        }],
        pending: []
      }'
  )"
  docker exec "$LITE_CONTAINER" redis-cli PUBLISH "tc:meeting:${MEETING_ID}:mutable" "$payload" >/dev/null
  sleep "$PUBLISH_DELAY_SECONDS"
done

wait "$PROOF_PID"
jq --arg expected "$EXPECT_TEXT" '. + {published_expected_prefix: $expected}' "$OUT"
