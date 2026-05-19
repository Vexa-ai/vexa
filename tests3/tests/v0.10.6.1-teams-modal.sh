#!/usr/bin/env bash
# v0.10.6.1-teams-modal — Teams 'Continue without audio or video' modal
# is dismissed by the bot when it appears on light-meetings/launch.
#
# Steps:
#   modal_dismissed   Static (always):
#                       - selectors.ts exports teamsContinueWithoutMediaSelectors
#                         and the list covers at least 3 plausible DOM patterns.
#                       - join.ts imports the selector list and references
#                         it in the pre-join click sequence.
#                       - the success-log string is present ("Dismissed
#                         'Continue without audio or video' modal" or equivalent).
#                     Runtime (compose, optional):
#                       - if the bot stack is up AND a fixture light-meetings
#                         URL is set via env TEAMS_LIGHT_MEETING_URL, dispatch
#                         a Teams bot; wait up to 60s for the modal-dismiss
#                         log line. SKIP if env unset or stack unreachable.
#
# Why this check exists (#226 / PR #283): Teams bot got stuck on the
# light-meetings/launch confirmation modal. PR #283 added selectors +
# dispatch; this script pins the regression invariant.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
SEL="$ROOT_DIR/services/vexa-bot/core/src/platforms/msteams/selectors.ts"
JOIN="$ROOT_DIR/services/vexa-bot/core/src/platforms/msteams/join.ts"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-teams-modal :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-teams-modal-$step"

case "$step" in
  modal_dismissed)
    failed=0

    # ── STATIC: selectors + dispatch wiring ───────────────────────
    if [ ! -f "$SEL" ]; then
      echo "    FAIL: msteams/selectors.ts missing"
      failed=1
    else
      if ! grep -q "teamsContinueWithoutMediaSelectors" "$SEL"; then
        echo "    FAIL: selectors.ts missing teamsContinueWithoutMediaSelectors export"
        failed=1
      fi
      # Selector list should reference the user-facing modal text + an
      # aria-label fallback + ideally a dialog-scoped variant. At least
      # 3 entries that mention "Continue without audio or video".
      n=$(grep -c "Continue without audio or video" "$SEL" 2>/dev/null || echo 0)
      if [ "${n:-0}" -lt 3 ]; then
        echo "    FAIL: selectors.ts has only $n entries matching 'Continue without audio or video' (need >= 3 for robustness)"
        failed=1
      fi
    fi

    if [ ! -f "$JOIN" ]; then
      echo "    FAIL: msteams/join.ts missing"
      failed=1
    else
      if ! grep -q "teamsContinueWithoutMediaSelectors" "$JOIN"; then
        echo "    FAIL: join.ts does not import teamsContinueWithoutMediaSelectors"
        failed=1
      fi
      # The pre-join click sequence should reference the modal.
      if ! grep -qE "Continue without audio or video|continueWithoutMedia" "$JOIN"; then
        echo "    FAIL: join.ts has no logic referencing the 'Continue without audio or video' flow"
        failed=1
      fi
      # The success log must be present (regression signature).
      if ! grep -qE "Dismissed.*Continue without audio or video|Continue without audio or video.*[Dd]ismissed" "$JOIN"; then
        echo "    FAIL: join.ts missing success-log line ('Dismissed \"Continue without audio or video\" modal')"
        failed=1
      fi
      if grep -n 'button:has-text("Continue without audio or video"), button:has-text("Continue")' "$JOIN" >/dev/null; then
        echo "    FAIL: modal-specific path still includes broad button:has-text(\"Continue\") fallback"
        failed=1
      fi
      if ! grep -q "dismissTeamsContinueWithoutMediaModal" "$JOIN"; then
        echo "    FAIL: join.ts missing dedicated no-A/V modal dismissal helper"
        failed=1
      fi
    fi

    # ── RUNTIME (optional, compose) ───────────────────────────────
    set +e
    GATEWAY="${GATEWAY_URL:-}"
    MODE_DETECTED="$(cat "$STATE/deploy_mode" 2>/dev/null || echo "")"
    [ -z "$GATEWAY" ] && [ "$MODE_DETECTED" = "compose" ] && GATEWAY="http://localhost:8056"

    if [ -z "${TEAMS_LIGHT_MEETING_URL:-}" ]; then
      echo "    info: runtime check skipped (TEAMS_LIGHT_MEETING_URL not set)"
    elif [ -z "$GATEWAY" ] || ! curl -sS -o /dev/null --max-time 3 "$GATEWAY/health" >/dev/null 2>&1; then
      echo "    info: runtime check skipped (no gateway reachable)"
    else
      ADMIN="${ADMIN_API_KEY:-changeme}"
      TOKEN_FILE="${TEAMS_MODAL_TOKEN_FILE:-/tmp/v0106_teams_modal_token}"
      if [ ! -f "$TOKEN_FILE" ]; then
        USER_ID=$(curl -sS -X POST "$GATEWAY/admin/users" \
          -H "X-Admin-API-Key: $ADMIN" -H "Content-Type: application/json" \
          -d '{"email":"teamsmodal@example.com","name":"teams-modal"}' 2>/dev/null \
          | python3 -c "import sys,json
try:
    d=json.load(sys.stdin); print(d.get('id',''))
except Exception:
    print('')" 2>/dev/null)
        if [ -z "$USER_ID" ]; then
          USER_ID=$(curl -sS -H "X-Admin-API-Key: $ADMIN" \
            "$GATEWAY/admin/users?email=teamsmodal@example.com" 2>/dev/null \
            | python3 -c "import sys,json
try:
    d=json.load(sys.stdin); print(d[0]['id'] if d else '')
except Exception:
    print('')" 2>/dev/null)
        fi
        if [ -n "$USER_ID" ]; then
          TOK=$(curl -sS -X POST "$GATEWAY/admin/users/$USER_ID/tokens" \
            -H "X-Admin-API-Key: $ADMIN" -H "Content-Type: application/json" -d '{}' 2>/dev/null \
            | python3 -c "import sys,json
try:
    d=json.load(sys.stdin); print(d.get('token',''))
except Exception:
    print('')" 2>/dev/null)
          [ -n "$TOK" ] && echo "$TOK" > "$TOKEN_FILE"
        fi
      fi

      if [ ! -f "$TOKEN_FILE" ] || [ -z "$(cat "$TOKEN_FILE")" ]; then
        echo "    info: runtime check skipped (could not provision test token)"
      else
        TOKEN=$(cat "$TOKEN_FILE")
        DISP=$(curl -sS -X POST "$GATEWAY/bots" \
          -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
          -d "{\"platform\":\"teams\",\"meeting_url\":\"$TEAMS_LIGHT_MEETING_URL\",\"bot_name\":\"teams-modal-probe\"}" 2>/dev/null)
        MEETING_ID=$(echo "$DISP" | python3 -c "import sys,json
try:
    d=json.load(sys.stdin); print(d.get('id') or d.get('meeting_id') or '')
except Exception:
    print('')")
        if [ -z "$MEETING_ID" ]; then
          echo "    FAIL runtime: dispatch returned no meeting_id"
          echo "$DISP" | head -5 | sed 's/^/      /'
          failed=1
        else
          echo "    runtime: dispatched bot meeting_id=$MEETING_ID; waiting up to 60s for modal-dismiss log"
          # Find the bot container; grep logs for the success line.
          DEADLINE=$(( $(date +%s) + 60 ))
          dismissed=0
          while [ "$(date +%s)" -lt "$DEADLINE" ]; do
            CONTAINER=$(docker ps --filter "name=meeting-" --format '{{.Names}}' 2>/dev/null \
              | grep -v meeting-api | head -1)
            if [ -n "$CONTAINER" ]; then
              if docker logs "$CONTAINER" 2>&1 | grep -qE "Dismissed.*Continue without audio or video|Continue without audio or video.*[Dd]ismissed"; then
                dismissed=1; break
              fi
            fi
            sleep 2
          done
          if [ "$dismissed" = "1" ]; then
            echo "    ok  runtime: modal-dismiss log line observed"
          else
            echo "    FAIL runtime: modal-dismiss log not observed within 60s"
            failed=1
          fi
          # Best-effort cleanup.
          curl -sS -X DELETE "$GATEWAY/bots/$MEETING_ID" -H "X-API-Key: $TOKEN" >/dev/null 2>&1 || true
        fi
      fi
    fi
    set -e

    if (( failed == 0 )); then
      step_pass TEAMS_CONTINUE_NO_AV_MODAL_DISMISSED "selector list + dispatch wiring intact (runtime: dismiss log observed when fixture URL provided)"
    else
      step_fail TEAMS_CONTINUE_NO_AV_MODAL_DISMISSED "one or more checks failed (see above)"
    fi
    ;;
  *)
    echo "  unknown step: $step" >&2
    exit 64
    ;;
esac
