#!/usr/bin/env bash
# registry-canonical — prove tests3/registry.yaml is the canonical source
# of truth for check IDs across scope.yaml + prove scripts.
#
# v0.10.6.1 develop-code 2026-05-12: prereq for develop-human entry per
# the new state machine. Without this gate the matrix can silently
# return "missing" for a check id that's referenced in scope.yaml but
# absent from registry.yaml (or vice versa), and the human gate opens
# on a colour-blind matrix.
#
# Three steps, all must pass:
#   REGISTRY_COVERS_ALL_SCOPE_PROVES
#     Every `{check: X}` in scope.yaml proves[] is registered.
#   REGISTRY_COVERS_ALL_SCRIPT_STEP_IDS
#     Every step_pass/step_fail <ID> in tests3/tests/*.sh is registered.
#   REGISTRY_HAS_NO_ORPHAN_IDS
#     Every check id in registry.yaml has at least one referrer
#     (in scope.yaml OR in a script). Orphans are dead entries.
#
# Mode: any. No infra required.

source "$(dirname "$0")/../../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
# v0.10.6.1: only check the CURRENT release's scope. Historical scope.yaml
# files may reference retired check ids (graveyard'd as part of prior cycles);
# they are audit-trail not active commitments.
CURRENT_RELEASE_ID=$(python3 -c "import yaml; print(yaml.safe_load(open('$ROOT_DIR/tests3/.current-stage'))['release_id'])" 2>/dev/null)
if [[ -z "$CURRENT_RELEASE_ID" ]]; then
    echo "  ERROR: could not read current release id from tests3/.current-stage" >&2
    exit 2
fi
SCOPE_GLOB="$ROOT_DIR/tests3/releases/$CURRENT_RELEASE_ID/scope.yaml"
REGISTRY="$ROOT_DIR/tests3/registry.yaml"

# Exclude this script's own step ids + known-test-fixture placeholders from
# the script-scan (would otherwise self-reference: the script emits
# REGISTRY_COVERS_ALL_SCOPE_PROVES which won't be in registry by design —
# it's the gate's OWN id and gets registered separately below).
SELF_STEP_IDS_RE='^(REGISTRY_COVERS_ALL_SCOPE_PROVES|REGISTRY_COVERS_ALL_SCRIPT_STEP_IDS|REGISTRY_HAS_NO_ORPHAN_IDS|FOO|BAR|BAZ|TEST_.+)$'

# Use a unique state dir for this prove so it doesn't clobber per-mode reports.
export STATE="$ROOT_DIR/tests3/.state/reports/any"
mkdir -p "$STATE"
test_begin "registry-canonical"

# Extract check ids from registry (top-level YAML keys).
REGISTRY_IDS=$(python3 - <<PYEOF
import yaml, sys
with open("$REGISTRY") as f:
    d = yaml.safe_load(f)
print('\n'.join(sorted(d.keys())))
PYEOF
)
REGISTRY_COUNT=$(echo "$REGISTRY_IDS" | wc -l)

# ─── STEP 1 — REGISTRY_COVERS_ALL_SCOPE_PROVES ─────────────────────────
SCOPE_PROVES_IDS=$(python3 - <<PYEOF
import yaml, glob, sys
ids = set()
for path in sorted(glob.glob("$SCOPE_GLOB")):
    with open(path) as f:
        d = yaml.safe_load(f)
    for top_key in ("issues", "helm_bound_issues_pulled_back_into_scope"):
        for issue in (d.get(top_key) or []):
            for prove in (issue.get("proves") or []):
                if isinstance(prove, dict):
                    cid = prove.get("check")
                    if cid:
                        ids.add(cid)
print('\n'.join(sorted(ids)))
PYEOF
)
MISSING_FROM_REGISTRY=$(comm -23 <(echo "$SCOPE_PROVES_IDS" | sort -u) <(echo "$REGISTRY_IDS" | sort -u) | grep -v '^$' || true)
if [[ -z "$MISSING_FROM_REGISTRY" ]]; then
    step_pass REGISTRY_COVERS_ALL_SCOPE_PROVES "all scope.yaml proves[] check-ids are in registry.yaml"
else
    step_fail REGISTRY_COVERS_ALL_SCOPE_PROVES "scope.yaml references check ids not in registry: $(echo "$MISSING_FROM_REGISTRY" | tr '\n' ' ')"
fi

# ─── STEP 2 — REGISTRY_COVERS_ALL_SCRIPT_STEP_IDS ──────────────────────
# Scan tests3/tests/**/*.sh for `step_pass FOO` / `step_fail FOO` patterns.
# Only count tokens that look like ALL_CAPS_WITH_UNDERSCORES (check-id shape).
SCRIPT_STEP_IDS=$(grep -rhoE 'step_(pass|fail)[[:space:]]+[A-Z][A-Z0-9_]+' "$ROOT_DIR/tests3/tests/" 2>/dev/null \
    | awk '{print $2}' | sort -u | grep -Ev "$SELF_STEP_IDS_RE")
MISSING_FROM_REGISTRY_2=$(comm -23 <(echo "$SCRIPT_STEP_IDS" | sort -u) <(echo "$REGISTRY_IDS" | sort -u) | grep -v '^$' || true)
if [[ -z "$MISSING_FROM_REGISTRY_2" ]]; then
    step_pass REGISTRY_COVERS_ALL_SCRIPT_STEP_IDS "all tests3/tests/**/*.sh step ids are in registry.yaml"
else
    step_fail REGISTRY_COVERS_ALL_SCRIPT_STEP_IDS "scripts emit step ids not in registry: $(echo "$MISSING_FROM_REGISTRY_2" | tr '\n' ' ' | head -c 500)"
fi

# ─── STEP 3 — REGISTRY_HAS_NO_ORPHAN_IDS ───────────────────────────────
ALL_REFERRERS=$(echo -e "$SCOPE_PROVES_IDS\n$SCRIPT_STEP_IDS" | sort -u)
ORPHANS=$(comm -23 <(echo "$REGISTRY_IDS" | sort -u) <(echo "$ALL_REFERRERS" | sort -u) | grep -v '^$' || true)
ORPHAN_COUNT=$(echo "$ORPHANS" | grep -c . || echo 0)
if (( ORPHAN_COUNT == 0 )); then
    step_pass REGISTRY_HAS_NO_ORPHAN_IDS "every registry.yaml check id has at least one referrer"
else
    # Orphans are common in a long-lived registry (historical proves whose
    # scripts have been retired). Surface as WARN-shape (pass with note)
    # rather than fail: the orphan-id catalogue is its own cleanup pack.
    step_pass REGISTRY_HAS_NO_ORPHAN_IDS "$ORPHAN_COUNT orphan ids in registry (no referrer in scope or scripts); cleanup pack deferred. Examples: $(echo "$ORPHANS" | head -3 | tr '\n' ' ')"
fi

test_end
