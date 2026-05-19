#!/usr/bin/env bash
# test-account-bot-limit — local validation account must support the parallel
# compose/lite/fresh-bot human gate without tripping the concurrent-bot limiter.

set -euo pipefail

source "$(dirname "$0")/../lib/common.sh"
source "$ROOT/tests3/lib/test-account.env"

MODE="${1:-}"
if [[ "$MODE" == "--mode" ]]; then
  MODE="${2:-}"
elif [[ "$MODE" == --mode=* ]]; then
  MODE="${MODE#--mode=}"
fi
if [[ -z "$MODE" ]]; then
  MODE="$(cat "$STATE/deploy_mode" 2>/dev/null || detect_mode)"
fi

case "$MODE" in
  compose) PG_CONTAINER="vexa-postgres-1" ;;
  lite) PG_CONTAINER="vexa-lite-postgres" ;;
  *)
    echo "Usage: $0 --mode compose|lite" >&2
    exit 2
    ;;
esac

test_begin "test-account-bot-limit"

row="$(docker exec "$PG_CONTAINER" psql -U postgres -d vexa -A -F $'\t' -t -c \
  "SELECT id || E'\t' || email || E'\t' || max_concurrent_bots FROM users WHERE email = '$TEST_ACCOUNT_EMAIL' LIMIT 1" \
  2>/dev/null || true)"

if [[ -z "$row" ]]; then
  step_fail TEST_ACCOUNT_BOT_LIMIT_IS_CANONICAL "$TEST_ACCOUNT_EMAIL is missing in $PG_CONTAINER"
else
  user_id="$(printf '%s' "$row" | awk -F $'\t' '{print $1}')"
  limit="$(printf '%s' "$row" | awk -F $'\t' '{print $3}')"
  if [[ "$limit" == "$TEST_ACCOUNT_MAX_CONCURRENT_BOTS" ]]; then
    step_pass TEST_ACCOUNT_BOT_LIMIT_IS_CANONICAL "$TEST_ACCOUNT_EMAIL max_concurrent_bots=$TEST_ACCOUNT_MAX_CONCURRENT_BOTS in $MODE"
  else
    step_fail TEST_ACCOUNT_BOT_LIMIT_IS_CANONICAL "$TEST_ACCOUNT_EMAIL max_concurrent_bots=$limit in $MODE, expected $TEST_ACCOUNT_MAX_CONCURRENT_BOTS"
  fi

  active_count="$(docker exec "$PG_CONTAINER" psql -U postgres -d vexa -A -t -c \
    "SELECT count(*) FROM meetings WHERE user_id = $user_id AND platform <> 'browser_session' AND status IN ('requested','joining','awaiting_admission','active')" \
    2>/dev/null | tr -d '[:space:]' || echo 0)"
  stale_rows="$(docker exec "$PG_CONTAINER" psql -U postgres -d vexa -A -F $'\t' -t -c \
    "SELECT id || ':' || platform || ':' || platform_specific_id || ':' || status || ':' || created_at FROM meetings WHERE user_id = $user_id AND platform <> 'browser_session' AND status IN ('requested','joining','awaiting_admission','active') AND created_at < now() - interval '2 hours' ORDER BY id" \
    2>/dev/null || true)"

  if [[ -z "$stale_rows" ]]; then
    step_pass TEST_ACCOUNT_NO_STALE_SLOT_CONSUMERS "no stale non-terminal $TEST_ACCOUNT_EMAIL bot rows in $MODE; active_count=$active_count/$TEST_ACCOUNT_MAX_CONCURRENT_BOTS"
  else
    step_fail TEST_ACCOUNT_NO_STALE_SLOT_CONSUMERS "$(printf '%s' "$stale_rows" | head -5 | tr '\n' ' ')"
  fi
fi

test_end
