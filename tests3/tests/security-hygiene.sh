#!/usr/bin/env bash
# Security hygiene — static checks that defend against dependency CVEs,
# info-disclosure exposure, and transitive npm vulns.
#
# Step IDs (stable — bound to features/security-hygiene/dods.yaml):
#   h11_pin             — every requirements*.txt with httpx/uvicorn pins h11>=0.16.0 (CVE-2025-43859)
#   docs_gate           — every services/**/main.py FastAPI app reads VEXA_ENV and default-denies docs
#   vexa_bot_npm_audit  — services/vexa-bot npm audit reports 0 HIGH and 0 CRITICAL vulnerabilities
#
# Static — runs without any deployment. All three steps are repo-grep /
# local-tool based.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"

echo ""
echo "  security-hygiene"
echo "  ──────────────────────────────────────────────"

test_begin security-hygiene

# ── Step: h11_pin ────────────────────────────────────────────────
# Every requirements*.txt that declares httpx or uvicorn must also pin
# h11>=0.16.0 explicitly. Prevents transitive silent downgrade under a
# future httpx/uvicorn bump.
missing=""
while IFS= read -r req; do
    if grep -qE '^(httpx|uvicorn)\b' "$req"; then
        if ! grep -qE '^h11[><=!]+\s*0\.(1[6-9]|[2-9][0-9])' "$req"; then
            missing+=" ${req#$ROOT_DIR/}"
        fi
    fi
done < <(find "$ROOT_DIR" -name 'requirements*.txt' \
    -not -path '*/node_modules/*' \
    -not -path '*/.venv/*' \
    -not -path '*/.git/*')

if [ -z "$missing" ]; then
    step_pass h11_pin "every httpx/uvicorn requirements*.txt pins h11>=0.16.0"
else
    step_fail h11_pin "missing h11>=0.16.0 pin in:$missing"
fi

# ── Step: docs_gate ──────────────────────────────────────────────
# Every services/**/main.py that instantiates FastAPI must pass
# docs_url / redoc_url / openapi_url as expressions that depend on
# VEXA_ENV (default-deny when VEXA_ENV=production).
missing=""
while IFS= read -r main_py; do
    # Only care about files that actually construct FastAPI.
    if ! grep -qE 'FastAPI\s*\(' "$main_py"; then
        continue
    fi
    if ! grep -q 'VEXA_ENV' "$main_py"; then
        missing+=" ${main_py#$ROOT_DIR/}"
        continue
    fi
    for field in docs_url redoc_url openapi_url; do
        if ! grep -qE "${field}\s*=.*(_PUBLIC_DOCS|public_docs|VEXA_ENV)" "$main_py"; then
            missing+=" ${main_py#$ROOT_DIR/}:${field}"
        fi
    done
done < <(find "$ROOT_DIR/services" -type f -name 'main.py' -not -path '*/__pycache__/*')

if [ -z "$missing" ]; then
    step_pass docs_gate "every FastAPI app env-gates docs_url/redoc_url/openapi_url on VEXA_ENV"
else
    step_fail docs_gate "missing VEXA_ENV-gated docs in:$missing"
fi

# ── Step: vexa_bot_npm_audit ────────────────────────────────────
# Run `npm audit` on both vexa-bot workspaces and assert 0 high + 0
# critical. Uses --audit-level=high to filter noise; --json for parseable
# output. Shell-outs kept on a single machine; no network beyond npm's
# normal registry.
audit_ok=1
audit_summary=""
for pkg_dir in services/vexa-bot services/vexa-bot/core; do
    if [ ! -f "$ROOT_DIR/$pkg_dir/package-lock.json" ]; then
        continue
    fi
    audit_json=$(cd "$ROOT_DIR/$pkg_dir" && npm audit --audit-level=high --json 2>/dev/null || true)
    counts=$(echo "$audit_json" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    v = d.get('metadata', {}).get('vulnerabilities', {})
    print(f\"high={v.get('high', 0)} critical={v.get('critical', 0)}\")
except Exception as e:
    print(f'parse_error={e}')
")
    audit_summary+=" $pkg_dir[$counts]"
    # Extract high & critical; fail if either > 0
    if echo "$counts" | grep -qE '(high=[1-9]|critical=[1-9]|parse_error)'; then
        audit_ok=0
    fi
done

if [ "$audit_ok" = "1" ]; then
    step_pass vexa_bot_npm_audit "0 HIGH / 0 CRITICAL npm vulnerabilities across vexa-bot workspaces${audit_summary}"
else
    step_fail vexa_bot_npm_audit "HIGH or CRITICAL npm vulnerabilities found:${audit_summary}"
fi

echo "  ──────────────────────────────────────────────"
echo ""
