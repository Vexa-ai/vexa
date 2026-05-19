#!/usr/bin/env bash
# scope-proof-gate-local — prove every scope-bound LOCAL check that should be
# validated before develop-human is actually present and green in the reports.
#
# Why this exists:
# - `registry-canonical.sh` proves the catalog is internally consistent.
# - `walkability-smoke.sh` proves the stack is human-walkable.
# - This script closes the remaining gap: every scope.yaml prove that is
#   runnable on LOCAL=1 (`lite` / `compose`) must have PASS evidence in the
#   latest LOCAL reports before the human gate opens.
#
# Usage:
#   bash tests3/tests/static/scope-proof-gate.sh
#   bash tests3/tests/static/scope-proof-gate.sh --scope tests3/releases/<id>/scope.yaml

set -euo pipefail

source "$(dirname "$0")/../../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
T3="$ROOT_DIR/tests3"
CURRENT_STAGE_FILE="$T3/.current-stage"

SCOPE_PATH=""
while [ $# -gt 0 ]; do
    case "$1" in
        --scope)
            SCOPE_PATH="$2"
            shift 2
            ;;
        *)
            echo "unknown arg: $1" >&2
            exit 2
            ;;
    esac
done

if [[ -z "$SCOPE_PATH" ]]; then
    CURRENT_RELEASE_ID=$(python3 - <<PYEOF
import yaml
with open("$CURRENT_STAGE_FILE") as f:
    data = yaml.safe_load(f) or {}
print(data.get("release_id", ""))
PYEOF
)
    if [[ -z "$CURRENT_RELEASE_ID" ]]; then
        echo "  ERROR: could not read current release id from tests3/.current-stage" >&2
        exit 2
    fi
    SCOPE_PATH="$T3/releases/$CURRENT_RELEASE_ID/scope.yaml"
fi

if [[ ! -f "$SCOPE_PATH" ]]; then
    echo "  ERROR: scope file not found: $SCOPE_PATH" >&2
    exit 2
fi

# Emit a structured report without polluting per-mode LOCAL matrix directories.
export STATE="$T3/.state"
export DEPLOY_MODE="none"
mkdir -p "$STATE"
echo "none" > "$STATE/deploy_mode"
test_begin "scope-proof-gate-local"

RESULT="$(python3 - <<PYEOF
import json
import os
import sys
from collections import OrderedDict

try:
    import yaml
except ImportError:
    print(json.dumps({"error": "PyYAML missing"}))
    sys.exit(2)

scope_path = os.path.abspath("$SCOPE_PATH")
t3 = os.path.abspath("$T3")
local_modes = ["lite", "compose"]
reports_by_mode = OrderedDict()

for mode in local_modes:
    state_dir = os.path.join(t3, f".state-{mode}")
    report_dir = os.path.join(state_dir, "reports", mode)
    mode_reports = {}
    if os.path.isdir(report_dir):
        for name in sorted(os.listdir(report_dir)):
            if not name.endswith(".json") or name == "summary.json":
                continue
            path = os.path.join(report_dir, name)
            try:
                with open(path) as f:
                    data = json.load(f)
            except Exception:
                continue
            test_name = data.get("test") or name[:-5]
            steps = {}
            for step in data.get("steps") or []:
                sid = step.get("id")
                if sid:
                    steps[sid] = step.get("status", "missing")
            mode_reports[test_name] = {
                "status": data.get("status", "missing"),
                "steps": steps,
            }
    reports_by_mode[mode] = mode_reports

def eval_proof(mode, proof):
    mode_reports = reports_by_mode.get(mode, {})
    if "test" in proof and "step" in proof:
        report = mode_reports.get(proof["test"])
        if not report:
            return "missing", f"{proof['test']}/{proof['step']} missing report"
        status = report["steps"].get(proof["step"], "missing")
        return status, f"{proof['test']}/{proof['step']}"
    if "check" in proof:
        scan_order = sorted(
            mode_reports.items(),
            key=lambda kv: (0 if kv[0].startswith("smoke-") else 1, kv[0]),
        )
        for report_name, report in scan_order:
            status = report["steps"].get(proof["check"])
            if status:
                return status, f"{report_name}/{proof['check']}"
        return "missing", f"{proof['check']} missing in reports"
    return "missing", "invalid proof binding"

with open(scope_path) as f:
    scope = yaml.safe_load(f) or {}

issues = scope.get("scope") or scope.get("issues") or []
checked = 0
failures = []
skipped = []

for issue in issues:
    iid = issue.get("id", "?")
    for proof in issue.get("proves") or []:
        proof_modes = proof.get("modes") or local_modes
        target_modes = [m for m in local_modes if m in proof_modes]
        if not target_modes:
            skipped.append(iid)
            continue
        tag = proof.get("test") or proof.get("check") or "?"
        if "step" in proof:
            tag = f"{tag}/{proof['step']}"
        for mode in target_modes:
            checked += 1
            status, resolved = eval_proof(mode, proof)
            if status != "pass":
                failures.append({
                    "issue": iid,
                    "mode": mode,
                    "proof": tag,
                    "resolved": resolved,
                    "status": status,
                })

print(json.dumps({
    "checked": checked,
    "failures": failures,
    "skipped_count": len(skipped),
}))
PYEOF
)"

CHECKED="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["checked"])' "$RESULT")"
FAIL_COUNT="$(python3 -c 'import json,sys; print(len(json.loads(sys.argv[1])["failures"]))' "$RESULT")"
SKIPPED_COUNT="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["skipped_count"])' "$RESULT")"

if [[ "$CHECKED" == "0" ]]; then
    step_fail SCOPE_LOCAL_PROOFS_ALL_GREEN "no LOCAL scope proofs were evaluated from $(basename "$SCOPE_PATH"); human gate would be blind"
elif [[ "$FAIL_COUNT" == "0" ]]; then
    step_pass SCOPE_LOCAL_PROOFS_ALL_GREEN "$CHECKED LOCAL scope proof cells green; $SKIPPED_COUNT stage-only proof groups deferred"
else
    DETAILS="$(python3 - <<PYEOF
import json
data = json.loads("""$RESULT""")
items = []
for f in data["failures"][:8]:
    items.append(f"{f['issue']}:{f['mode']}:{f['proof']}={f['status']}")
print("; ".join(items))
PYEOF
)"
    step_fail SCOPE_LOCAL_PROOFS_ALL_GREEN "$FAIL_COUNT of $CHECKED LOCAL scope proof cells not green. Examples: $DETAILS"
fi

test_end
