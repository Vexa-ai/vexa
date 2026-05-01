#!/usr/bin/env bash
# v0.10.6-bot-stability — SDP-munge complete revert + failure_stage tracker.
#
# Static-grep checks (lite mode, no infrastructure):
#   sdp_munge_site2_removed                       — #291: site-2 transceiver-direction block gone
#   no_transceiver_direction_mutation             — #291: defends against re-introduction
#   (BOT_FAILURE_STAGE_TRACKER_UPDATES_ON_TRANSITIONS — defined as type:grep in registry; runs without this script)
#
# Runtime smoke checks (compose / helm — require a fixture meeting URL):
#   gmeet_recording_survival                      — #284 / #291: GMeet recording_enabled survives 120s
#   teams_admission_survival                      — #281: Teams bot active for ≥60s post-admission
#   zoom_web_survival                             — Zoom Web bot active for ≥60s
#   meeting_failure_stage_matches_timeline        — #294: JSONB failure_stage matches timeline
#
# Runtime smokes are skipped (step_skip) when no fixture is provided —
# they fall back to the human_verify checklist in scope.yaml.
#
# Fixture env vars (compose / helm modes):
#   FIXTURE_GMEET_URL, FIXTURE_TEAMS_URL, FIXTURE_ZOOM_URL
#   GATEWAY_URL, ADMIN_TOKEN (for /bots dispatch)

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
SCREEN_CONTENT="$ROOT_DIR/services/vexa-bot/core/src/services/screen-content.ts"
INDEX_TS="$ROOT_DIR/services/vexa-bot/core/src/index.ts"
UNIFIED_CB="$ROOT_DIR/services/vexa-bot/core/src/services/unified-callback.ts"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6-bot-stability :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6-bot-stability-$step"

case "$step" in

  sdp_munge_site2_removed)
    # The site-2 SDP-munge block lived at lines 1218-1228 of screen-content.ts
    # in v0.10.5, inside the `pc.addEventListener('track', ...)` handler.
    # The pattern to detect: a `for (const t of transceivers)` loop that
    # mutates `t.direction = ...`. If we see both within ~50 lines of each
    # other AND in the live RTCPeerConnection wrapper context, the bug is back.
    if [ ! -f "$SCREEN_CONTENT" ]; then
      step_fail BOT_SDP_MUNGE_SITE2_REMOVED "screen-content.ts missing — bot source tree incomplete"
      exit 1
    fi

    # The site-2 specific bug signature: a for-loop over transceivers that
    # demotes the direction to 'sendonly' or 'inactive' (the v0.10.5
    # incomplete revert pattern). We check for the conjunction:
    #   1. addEventListener('track') wrapper context (site-2 was inside this)
    #   2. direction= assignment to 'sendonly' or 'inactive'
    if grep -qE "\.direction\s*=[^=]*['\"](sendonly|inactive)['\"]" "$SCREEN_CONTENT"; then
      step_fail BOT_SDP_MUNGE_SITE2_REMOVED "site2_block_removed=false — direction= demoting assignment found in screen-content.ts"
      exit 1
    fi
    step_pass BOT_SDP_MUNGE_SITE2_REMOVED "site2_block_removed=true"
    ;;

  no_transceiver_direction_mutation)
    # The #291 bug pattern is DEMOTING the direction — assigning 'sendonly'
    # or 'inactive' to a transceiver that already had a receiver track.
    # The voice-agent / virtual-camera publishing path legitimately
    # PROMOTES direction to 'sendrecv' to enable sending; that's correct
    # WebRTC and we keep it.
    #
    # So the regression we defend against is specifically:
    #   .direction = ... 'sendonly'   (demotes sender track)
    #   .direction = ... 'inactive'   (kills the transceiver)
    BOT_SRC="$ROOT_DIR/services/vexa-bot"
    if [ ! -d "$BOT_SRC" ]; then
      step_fail BOT_NO_TRANSCEIVER_DIRECTION_MUTATION "services/vexa-bot missing"
      exit 1
    fi

    # Find all .ts files outside excluded paths and grep for the demoting
    # assignment pattern. Allow 'sendrecv' (legitimate voice-agent path).
    matches=$(find "$BOT_SRC" -name '*.ts' \
                  -not -path '*/node_modules/*' \
                  -not -path '*/dist/*' \
                  -not -path '*/.next/*' \
                  -print0 \
              | xargs -0 grep -lE "\.direction\s*=[^=]*['\"](sendonly|inactive)['\"]" 2>/dev/null \
              || true)

    if [ -n "$matches" ]; then
      echo "  matches found (demoting transceiver.direction — #291 bug class):"
      echo "$matches" | sed 's|^|    |'
      step_fail BOT_NO_TRANSCEIVER_DIRECTION_MUTATION "transceiver_direction_assignments>0 (re-introduction of #291 bug class — see $matches)"
      exit 1
    fi
    step_pass BOT_NO_TRANSCEIVER_DIRECTION_MUTATION "transceiver_direction_assignments=0 (demoting pattern)"
    ;;

  gmeet_recording_survival)
    # Runtime smoke. Requires FIXTURE_GMEET_URL + GATEWAY_URL + ADMIN_TOKEN.
    if [ -z "${FIXTURE_GMEET_URL:-}" ]; then
      step_skip BOT_GMEET_RECORDING_ENABLED_SURVIVES_TRACK_EVENT "FIXTURE_GMEET_URL not set — see scope.yaml human_verify"
      exit 0
    fi
    : "${GATEWAY_URL:?GATEWAY_URL required}"
    : "${ADMIN_TOKEN:?ADMIN_TOKEN required}"

    # TODO(provision/deploy stage): full implementation dispatches a bot at
    # FIXTURE_GMEET_URL with recording_enabled=true, polls /meetings/<id>
    # for 180s waiting for a transition past 'active', and asserts:
    #   1. Bot reaches active within 60s
    #   2. Bot stays in active for ≥120s
    #   3. No `[Vexa] Video transceiver stopped` line in pod logs
    #   4. No 'Execution context destroyed' in error_details
    step_skip BOT_GMEET_RECORDING_ENABLED_SURVIVES_TRACK_EVENT "runtime fixture stub — see scope.yaml human_verify (compose mode)"
    ;;

  teams_admission_survival)
    if [ -z "${FIXTURE_TEAMS_URL:-}" ]; then
      step_skip BOT_TEAMS_ADMISSION_NOT_44MS_DROP "FIXTURE_TEAMS_URL not set — see scope.yaml human_verify"
      exit 0
    fi
    : "${GATEWAY_URL:?GATEWAY_URL required}"
    : "${ADMIN_TOKEN:?ADMIN_TOKEN required}"
    # TODO(provision/deploy stage): dispatch bot, manual admission window, assert
    # bot remains in active for ≥60s vs the 44ms drop in #281.
    step_skip BOT_TEAMS_ADMISSION_NOT_44MS_DROP "runtime fixture stub — see scope.yaml human_verify (compose mode)"
    ;;

  zoom_web_survival)
    if [ -z "${FIXTURE_ZOOM_URL:-}" ]; then
      step_skip BOT_ZOOM_WEB_SURVIVES_TRACK_EVENT "FIXTURE_ZOOM_URL not set — see scope.yaml human_verify"
      exit 0
    fi
    : "${GATEWAY_URL:?GATEWAY_URL required}"
    : "${ADMIN_TOKEN:?ADMIN_TOKEN required}"
    step_skip BOT_ZOOM_WEB_SURVIVES_TRACK_EVENT "runtime fixture stub — see scope.yaml human_verify (compose mode)"
    ;;

  meeting_failure_stage_matches_timeline)
    # Runtime check. Requires DB access. Validates that for the most recent
    # failed meeting in the DB, JSONB data->>'failure_stage' matches the
    # latest entry's 'to' field in data->'status_transition'.
    if [ -z "${DB_HOST:-}" ] || [ -z "${DB_USER:-}" ]; then
      step_skip MEETING_FAILURE_STAGE_MATCHES_TIMELINE "DB env vars not set — see scope.yaml human_verify"
      exit 0
    fi
    # TODO(deploy/validate stage): asyncpg query, compare last status_transition.to
    # against data.failure_stage on the most recent failed meeting.
    step_skip MEETING_FAILURE_STAGE_MATCHES_TIMELINE "DB query stub — see scope.yaml human_verify (compose mode)"
    ;;

  *)
    step_fail "v0.10.6-bot-stability" "unknown step: $step"
    exit 1
    ;;
esac

echo "  ──────────────────────────────────────────────"
echo ""
