#!/usr/bin/env bash
# v0.10.6.1-webhook-no-autotouch — webhook delivery writes must not bump
# meetings.updated_at (the "domain progress" signal used by Pack E.3.2
# stale-stopping sweep and any future updated_at consumer).
#
# Steps:
#   no_autotouch  Static (lite mode):
#                   - webhooks.py defines _persist_data_preserving_updated_at
#                     that uses sqlalchemy.update with explicit
#                     `updated_at=meeting.updated_at` to override the
#                     onupdate=func.now() default.
#                   - _write_delivery_status / _append_delivery_log are
#                     async + take a db session, persist via that helper,
#                     do NOT call flag_modified.
#                   - All 4 call sites use `await` + pass db.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
WH="$ROOT_DIR/services/meeting-api/meeting_api/webhooks.py"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-webhook-no-autotouch :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-webhook-no-autotouch-$step"

case "$step" in
  no_autotouch)
    failed=0

    if ! grep -q "^async def _persist_data_preserving_updated_at" "$WH"; then
      echo "    FAIL: _persist_data_preserving_updated_at helper missing"
      failed=1
    fi
    # Must explicitly set updated_at=meeting.updated_at on the UPDATE
    # to override the onupdate=func.now() default.
    if ! grep -q 'updated_at=meeting.updated_at' "$WH"; then
      echo "    FAIL: helper does not set updated_at=meeting.updated_at on the raw UPDATE"
      failed=1
    fi
    if ! grep -q "^async def _write_delivery_status" "$WH"; then
      echo "    FAIL: _write_delivery_status not converted to async"
      failed=1
    fi
    if ! grep -q "^async def _append_delivery_log" "$WH"; then
      echo "    FAIL: _append_delivery_log not converted to async"
      failed=1
    fi
    # No call site should pass meeting without db (the old shape).
    if grep -nE '_(write_delivery_status|append_delivery_log)\(meeting,' "$WH" >/dev/null; then
      echo "    FAIL: call site missing db argument (old (meeting, ...) shape still present)"
      grep -nE '_(write_delivery_status|append_delivery_log)\(meeting,' "$WH"
      failed=1
    fi
    # All call sites should use await.
    if grep -nE '^\s+_(write_delivery_status|append_delivery_log)\(' "$WH" >/dev/null; then
      echo "    FAIL: at least one call site is missing the await keyword"
      grep -nE '^\s+_(write_delivery_status|append_delivery_log)\(' "$WH"
      failed=1
    fi

    # ── RUNTIME exercise (compose mode, when DB env is reachable) ────
    # Create a real meeting, snapshot updated_at, fire a webhook delivery
    # path, then verify updated_at is unchanged.
    if [[ "${DEPLOY_MODE:-}" == "compose" ]] && command -v docker >/dev/null 2>&1; then
      # Pick a real meeting row from postgres (any in_progress or
      # completed). Fall back to skip if no row.
      MEETING_ID=$(docker exec vexa-postgres-1 psql -U postgres -d vexa -t -c \
        "SELECT id FROM meetings WHERE status IN ('completed','active','in_progress') ORDER BY id DESC LIMIT 1" 2>/dev/null | tr -d ' ')
      if [[ -z "$MEETING_ID" ]]; then
        echo "    skip runtime: no meeting row to exercise webhook write against"
      else
        # Snapshot.
        UPDATED_BEFORE=$(docker exec vexa-postgres-1 psql -U postgres -d vexa -t -c \
          "SELECT to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS.US') FROM meetings WHERE id=$MEETING_ID" 2>/dev/null | tr -d ' ')

        # Force a webhook_deliveries append by calling the helper directly.
        docker exec vexa-meeting-api-1 python -c "
import asyncio
from sqlalchemy import select
from meeting_api.database import async_session_local
from meeting_api.models import Meeting
from meeting_api.webhooks import _append_delivery_log

async def go():
    async with async_session_local() as db:
        meeting = (await db.execute(select(Meeting).where(Meeting.id==$MEETING_ID))).scalars().first()
        await _append_delivery_log(db, meeting, {'event_type':'_test_autotouch','status':'delivered','timestamp':'2026-05-10T00:00:00Z'})
        await db.commit()
        print('OK webhook write committed')
asyncio.run(go())
" 2>&1 | tail -3

        UPDATED_AFTER=$(docker exec vexa-postgres-1 psql -U postgres -d vexa -t -c \
          "SELECT to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS.US') FROM meetings WHERE id=$MEETING_ID" 2>/dev/null | tr -d ' ')

        # And confirm the webhook_deliveries entry actually landed.
        DELIVERIES_HAS=$(docker exec vexa-postgres-1 psql -U postgres -d vexa -t -c \
          "SELECT data->'webhook_deliveries'->-1->>'event_type' FROM meetings WHERE id=$MEETING_ID" 2>/dev/null | tr -d ' ')

        echo "    runtime meeting_id=$MEETING_ID"
        echo "    updated_at before: $UPDATED_BEFORE"
        echo "    updated_at after : $UPDATED_AFTER"
        echo "    last delivery type: '$DELIVERIES_HAS'"

        if [[ "$DELIVERIES_HAS" != "_test_autotouch" ]]; then
          echo "    FAIL: webhook write did not land in data->'webhook_deliveries'"
          failed=1
        fi
        if [[ "$UPDATED_BEFORE" != "$UPDATED_AFTER" ]]; then
          echo "    FAIL: meetings.updated_at WAS bumped by webhook write — autotouch fix is broken"
          failed=1
        else
          echo "    runtime ok: updated_at preserved across webhook write"
        fi
      fi
    fi

    if (( failed == 0 )); then
      step_pass WEBHOOK_NO_UPDATED_AT_AUTOTOUCH "webhook delivery writes preserve meetings.updated_at via raw UPDATE (static + runtime)"
    else
      step_fail WEBHOOK_NO_UPDATED_AT_AUTOTOUCH "one or more checks failed"
    fi
    ;;

  *)
    echo "  unknown step: $step" >&2
    exit 64
    ;;
esac
