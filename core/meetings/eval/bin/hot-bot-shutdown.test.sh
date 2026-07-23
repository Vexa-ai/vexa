#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/vexa-hot-bot-shutdown-test.XXXXXX")
LAUNCHER_PID=

cleanup() {
  if [ -n "$LAUNCHER_PID" ]; then
    kill -KILL "$LAUNCHER_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

wait_for_ready() {
  local log_file=$1
  for _ in $(seq 1 100); do
    grep -q '^WORKER-READY ' "$log_file" 2>/dev/null && return
    sleep 0.05
  done
  fail "worker did not become ready; output: $(cat "$log_file" 2>/dev/null || true)"
}

run_signal_case() {
  local signal=$1
  local script_copy="$TMP_ROOT/hot-bot-$signal.sh"
  local bot_log="$TMP_ROOT/bot-$signal.log"
  local launcher_log="$TMP_ROOT/launcher-$signal.log"
  cp "$HERE/hot-bot.sh" "$script_copy"

  # Start the launcher as a fresh process-group leader, matching a foreground terminal job.
  # Sending its group INT/TERM below is the deterministic equivalent of terminal delivery.
  python3 -c '
import os, signal, sys
os.setpgrp()
signal.signal(signal.SIGINT, signal.SIG_DFL)
signal.signal(signal.SIGTERM, signal.SIG_DFL)
os.execvp(sys.argv[1], sys.argv[1:])
' bash "$script_copy" --_run-logged-worker "$bot_log" \
    node -e '
      let terms = 0;
      process.on("SIGINT", () => console.log("WORKER-UNEXPECTED-INT"));
      process.on("SIGTERM", () => {
        terms += 1;
        console.log(`WORKER-TERM ${terms}`);
        setTimeout(() => {
          console.log("WORKER-FINAL-LIFECYCLE");
          process.exit(0);
        }, 100);
      });
      console.log(`WORKER-READY ${process.pid}`);
      setInterval(() => {}, 1000);
    ' >"$launcher_log" 2>&1 &
  LAUNCHER_PID=$!
  wait_for_ready "$bot_log"

  # Mutate the on-disk source while its worker is active. A parsed main() is insulated: the
  # running invocation must drain once, not jump into new bytes or re-enter the launch tail.
  printf '%s\n' 'echo SOURCE-MUTATION-EXECUTED; exit 91' >"$script_copy"
  kill "-$signal" "-$LAUNCHER_PID"
  # A second stop while draining must not become a second SIGTERM to node.
  kill -TERM "$LAUNCHER_PID" 2>/dev/null || true

  set +e
  wait "$LAUNCHER_PID"
  local status=$?
  set -e
  LAUNCHER_PID=

  [ "$status" -eq 0 ] || fail "$signal launcher exit=$status; $(cat "$launcher_log")"
  [ "$(grep -c '^WORKER-TERM ' "$bot_log")" -eq 1 ] \
    || fail "$signal did not forward exactly one SIGTERM; $(cat "$bot_log")"
  ! grep -q '^WORKER-UNEXPECTED-INT$' "$bot_log" \
    || fail "$signal leaked terminal INT directly to node"
  grep -q '^WORKER-FINAL-LIFECYCLE$' "$bot_log" \
    || fail "$signal returned before final lifecycle output drained"
  ! grep -q 'SOURCE-MUTATION-EXECUTED' "$launcher_log" \
    || fail "$signal execution re-entered mutated source"
}

run_signal_case INT
run_signal_case TERM

NORMAL_LOG="$TMP_ROOT/bot-normal.log"
set +e
bash "$HERE/hot-bot.sh" --_run-logged-worker "$NORMAL_LOG" \
  node -e 'console.log("WORKER-NORMAL-EXIT"); process.exit(7)'
NORMAL_STATUS=$?
set -e
[ "$NORMAL_STATUS" -eq 7 ] || fail "normal worker exit changed: got $NORMAL_STATUS, expected 7"
grep -q '^WORKER-NORMAL-EXIT$' "$NORMAL_LOG" || fail "normal worker output was not drained"

echo "PASS hot-bot graceful shutdown, source-mutation insulation, and normal exit propagation"
