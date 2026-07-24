#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BOT="$(cd "$HERE/../../services/bot" && pwd)"
TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/vexa-hot-bot-custody-test.XXXXXX")

cleanup() {
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

source "$HERE/hot-bot.sh"

custody_cli() {
  if [ -f "$BOT/dist/custody-admission.js" ]; then
    node "$BOT/dist/custody-admission.js" "$1"
    return
  fi
  (cd "$BOT" && pnpm exec tsx src/custody-admission.ts "$1")
}

expect_red() {
  local root=$1 expected=$2 log=$3 status=0
  require_custody_admission "$root" custody_cli >"$log" 2>&1 || status=$?
  [ "$status" -eq 4 ] || {
    echo "FAIL: custody admission returned $status, expected 4: $(cat "$log")" >&2
    exit 1
  }
  grep -q "$expected" "$log" || {
    echo "FAIL: custody admission did not report $expected: $(cat "$log")" >&2
    exit 1
  }
}

expect_red "$TMP_ROOT/missing" "kind=stored-object-missing" "$TMP_ROOT/missing.log"

INCOMPLETE_ROOT="$TMP_ROOT/incomplete"
mkdir -p "$INCOMPLETE_ROOT/0000000000000000000000000000000000000000000000000000000000000000"
printf 'null\n' >"$INCOMPLETE_ROOT/0000000000000000000000000000000000000000000000000000000000000000/receipt.json"
expect_red "$INCOMPLETE_ROOT" "kind=stored-object-incomplete" "$TMP_ROOT/incomplete.log"

python3 - "$TMP_ROOT/valid" "$TMP_ROOT/corrupt" <<'PY'
import hashlib, json, pathlib, sys

valid_root, corrupt_root = map(pathlib.Path, sys.argv[1:])
header = {
    "type": "captured_signal_header",
    "v": 1,
    "platform": "jitsi",
    "native_meeting_id": "hot-bot-custody-test",
    "language": "en",
    "lane": "mixed",
    "sample_rate": 16000,
    "started_at": "2024-06-10T06:13:20.000Z",
}

def write_store(root, records):
    body = "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records).encode()
    digest = hashlib.sha256(body).hexdigest()
    directory = root / digest
    directory.mkdir(parents=True)
    key = f"{digest}/session.captured-signal.jsonl"
    (directory / "session.captured-signal.jsonl").write_bytes(body)
    receipt = {
        "type": "captured-signal-custody-receipt",
        "v": 1,
        "complete": True,
        "algorithm": "sha256",
        "digest": digest,
        "bytes": len(body),
        "records": len(records),
        "key": key,
    }
    (directory / "receipt.json").write_text(json.dumps(receipt, separators=(",", ":")) + "\n")

write_store(valid_root, [header])
write_store(corrupt_root, [header, {
    "type": "not-a-captured-signal-record",
    "payload": "corrupt-semantic-record",
}])
PY

VALID_OUTPUT=$(require_custody_admission "$TMP_ROOT/valid" custody_cli)
grep -q 'CUSTODY_ADMITTED .*independent_readback=true' <<<"$VALID_OUTPUT" || {
  echo "FAIL: valid custody was not admitted: $VALID_OUTPUT" >&2
  exit 1
}
expect_red "$TMP_ROOT/corrupt" "kind=stored-object-incomplete" "$TMP_ROOT/corrupt.log"

STALE_LOG="$TMP_ROOT/stale.log"
stale_status=0
require_fresh_custody_root "$TMP_ROOT/valid" >"$STALE_LOG" 2>&1 || stale_status=$?
[ "$stale_status" -eq 4 ] || {
  echo "FAIL: stale custody root returned $stale_status, expected 4" >&2
  exit 1
}
grep -q 'kind=stale-custody-root' "$STALE_LOG" || {
  echo "FAIL: stale custody root did not fail closed: $(cat "$STALE_LOG")" >&2
  exit 1
}
require_fresh_custody_root "$TMP_ROOT/fresh"

echo "PASS hot-bot custody admission: valid receipt required; missing/incomplete/corrupt/stale evidence stays RED"
