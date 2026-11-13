#!/bin/sh
# Two starts racing over one stale link.pid: at most one line runs (re-review of 7f918007, M). Run: sh tests/vexa-link-pid.test.sh
set -u
here=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d); trap 'pkill -f "vexa-link http://127.0.0.1:1/" 2>/dev/null; rm -rf "$work"' EXIT
fail=0; r=0
while [ $r -lt 15 ]; do
  r=$((r + 1)); export HOME="$work/r$r"; mkdir -p "$HOME/.vexa/link"
  sh -c 'exit 0' & dead=$!; wait $dead; echo $dead > "$HOME/.vexa/link/link.pid"        # a pid that is gone
  VEXA_LISTEN_KEY=k sh "$here/bin/vexa-link" "http://127.0.0.1:1/" > "$work/a$r" 2>&1 & a=$!
  VEXA_LISTEN_KEY=k sh "$here/bin/vexa-link" "http://127.0.0.1:1/" > "$work/b$r" 2>&1 & b=$!
  sleep 2
  alive=0; kill -0 $a 2>/dev/null && alive=$((alive + 1)); kill -0 $b 2>/dev/null && alive=$((alive + 1))
  touch "$HOME/.vexa/link/stop"; kill $a $b 2>/dev/null; wait $a $b 2>/dev/null
  [ $alive -le 1 ] || { echo "FAIL round $r: $alive lines run"; fail=1; }
done
[ $fail = 0 ] && echo "ok   15 races: never two lines"
exit $fail
