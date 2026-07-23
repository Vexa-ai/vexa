#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/vexa-hot-bot-browser-test.XXXXXX")
trap 'rm -rf "$TMP_ROOT"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

FAKE_BROWSER="$TMP_ROOT/fake chrome"
printf '%s\n' '#!/usr/bin/env bash' 'echo "Fake Chrome 150.1.2"' >"$FAKE_BROWSER"
chmod +x "$FAKE_BROWSER"

# Source only the helpers: hot-bot.sh guards main() with BASH_SOURCE.
source "$HERE/hot-bot.sh"

VEXA_EVAL_BROWSER_EXECUTABLE_PATH="$FAKE_BROWSER"
validate_eval_browser_runtime
[ "$EVAL_BROWSER_PATH" = "$FAKE_BROWSER" ] || fail "explicit path changed"
[ "$EVAL_BROWSER_VERSION" = "Fake Chrome 150.1.2" ] || fail "version not captured"
write_browser_runtime_provenance "$TMP_ROOT/explicit.json"
python3 - "$TMP_ROOT/explicit.json" "$FAKE_BROWSER" <<'PY' || exit 1
import json, sys
data = json.load(open(sys.argv[1]))
assert data["mode"] == "explicit-eval-override"
assert data["executable_path"] == sys.argv[2]
assert data["reported_version"] == "Fake Chrome 150.1.2"
PY

VEXA_EVAL_BROWSER_EXECUTABLE_PATH=
validate_eval_browser_runtime
write_browser_runtime_provenance "$TMP_ROOT/default.json"
python3 - "$TMP_ROOT/default.json" <<'PY' || exit 1
import json, sys
data = json.load(open(sys.argv[1]))
assert data["mode"] == "playwright-pinned-default"
assert data["executable_path"] is None
assert data["reported_version"] is None
PY

VEXA_EVAL_BROWSER_EXECUTABLE_PATH="$TMP_ROOT/missing-browser"
if validate_eval_browser_runtime 2>"$TMP_ROOT/invalid.err"; then
  fail "missing executable was accepted"
fi
grep -q 'is not executable' "$TMP_ROOT/invalid.err" || fail "invalid-path error was not actionable"

echo "PASS hot-bot browser runtime preflight + provenance"
