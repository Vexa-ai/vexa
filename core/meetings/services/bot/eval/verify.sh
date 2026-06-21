#!/usr/bin/env bash
# verify.sh — OFFLINE self-test of the verdict oracle. No meeting, no bbb, no redis.
# Proves the gate FAILS on the committed mis-attribution fixture and PASSES on a clean one.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVAL_DIR="$HERE/../../../eval"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
NOTRUTH="$TMP/no-truth.jsonl"   # absent on purpose → analyze-only scoring

# Clean gmeet fixture: self-IDs match labels, no consecutive same-speaker, no seg_ labels.
cat > "$TMP/clean.json" <<'JSON'
{ "platform":"google_meet","native_meeting_id":"clean-001","segments":[
  {"segment_id":"ch0:1","speaker":"spk-Anna","text":"This is Anna and here is the agenda for today.","start":1.0,"end":3.5,"completed":true},
  {"segment_id":"ch1:1","speaker":"spk-Boris","text":"Boris here, I think we should ship it on Friday.","start":4.0,"end":7.0,"completed":true},
  {"segment_id":"ch0:2","speaker":"spk-Anna","text":"Sounds good, thanks everyone for joining the call.","start":8.0,"end":10.0,"completed":true}
] }
JSON

fail=0
echo "── TEST 1: clean gmeet fixture → expect PASS (exit 0) ──"
if PLATFORM=google_meet NATIVE_ID=clean-001 TRUTH_LOG="$NOTRUTH" node "$HERE/verdict.mjs" "$TMP/clean.json" >/dev/null; then
  echo "  ✓ PASS as expected"; else echo "  ✗ expected PASS, got FAIL"; fail=1; fi

echo "── TEST 2: committed mis-attribution fixture → expect FAIL (exit 1) ──"
if PLATFORM=zoom NATIVE_ID=flag-fixture-001 LANE=mixed TRUTH_LOG="$NOTRUTH" \
     node "$HERE/verdict.mjs" "$EVAL_DIR/replay-fixture/transcript-misattr.json" "$TMP/flags.json" >/dev/null; then
  echo "  ✗ expected FAIL, got PASS"; fail=1; else echo "  ✓ FAIL as expected"; fi

echo "── TEST 3: attribution maps the flagged issue to a brick ──"
if node "$HERE/attribute.mjs" "$TMP/flags.json" | grep -q '@vexa/gmeet-pipeline'; then
  echo "  ✓ attributed to @vexa/gmeet-pipeline"; else echo "  ✗ attribution did not name the brick"; fail=1; fi

echo
[ $fail -eq 0 ] && echo "✅ verify: the verdict oracle gates correctly (clean→PASS, misattr→FAIL→brick)." \
              || { echo "❌ verify: self-test FAILED."; exit 1; }
