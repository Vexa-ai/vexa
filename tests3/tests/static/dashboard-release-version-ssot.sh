#!/usr/bin/env bash
# dashboard-release-version-ssot — dashboard images must disclose the canonical
# release version they were built for, not stale retagged bundle metadata.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$ROOT_DIR/tests3/lib/common.sh"

test_begin "dashboard-release-version-ssot"

root_version="$(tr -d '[:space:]' < "$ROOT_DIR/VERSION")"
chart_app_version="$(python3 - <<'PY' "$ROOT_DIR/deploy/helm/charts/vexa/Chart.yaml"
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r"^\s*appVersion\s*:\s*['\"]?([^'\"\s]+)", text, re.M)
print(m.group(1) if m else "")
PY
)"

if [ -n "$root_version" ] && [ "$root_version" = "$chart_app_version" ]; then
  step_pass DASHBOARD_RELEASE_VERSION_CANONICAL_SOURCE "VERSION and Helm appVersion both declare $root_version"
else
  step_fail DASHBOARD_RELEASE_VERSION_CANONICAL_SOURCE "VERSION=$root_version Chart.appVersion=$chart_app_version"
fi

generator="$ROOT_DIR/services/dashboard/scripts/generate-release-version.js"
dockerfile="$ROOT_DIR/services/dashboard/Dockerfile"
lite_dockerfile="$ROOT_DIR/deploy/lite/Dockerfile.lite"
package_json="$ROOT_DIR/services/dashboard/package.json"

guard_source="$(rg -n 'readRootVersion|VEXA_REPO_ROOT|does not match|VERSION=.*Chart.yaml' "$generator" 2>/dev/null || true)"
dashboard_guard="$(rg -n 'COPY VERSION /repo/VERSION|COPY deploy/helm/charts/vexa/Chart.yaml /repo/deploy/helm/charts/vexa/Chart.yaml|ENV VEXA_REPO_ROOT=/repo|npm run assert-release-version' "$dockerfile" 2>/dev/null || true)"
lite_guard="$(rg -n 'COPY VERSION /repo/VERSION|COPY deploy/helm/charts/vexa/Chart.yaml /repo/deploy/helm/charts/vexa/Chart.yaml|ENV VEXA_REPO_ROOT=/repo|npm run assert-release-version' "$lite_dockerfile" 2>/dev/null || true)"

if [ -n "$guard_source" ] && [ "$(printf '%s\n' "$dashboard_guard" | wc -l | tr -d ' ')" -ge 4 ] && [ "$(printf '%s\n' "$lite_guard" | wc -l | tr -d ' ')" -ge 4 ]; then
  step_pass DASHBOARD_RELEASE_VERSION_DOCKER_BUILD_GUARD "dashboard and lite Docker builds validate env overrides against canonical VERSION/Chart.yaml"
else
  step_fail DASHBOARD_RELEASE_VERSION_DOCKER_BUILD_GUARD "$(printf '%s\n%s\n%s' "$guard_source" "$dashboard_guard" "$lite_guard" | head -12 | tr '\n' ' ')"
fi

assert_script="$ROOT_DIR/services/dashboard/scripts/assert-release-version.js"
package_script="$(jq -r '.scripts["assert-release-version"] // ""' "$package_json")"
assert_checks="$(rg -n 'EXPECTED_VEXA_OSS_VERSION|compiled dashboard bundle|generated version|\\.next' "$assert_script" 2>/dev/null || true)"

if [ "$package_script" = "node scripts/assert-release-version.js" ] && [ -n "$assert_checks" ]; then
  step_pass DASHBOARD_RELEASE_VERSION_BUNDLE_ASSERTION "dashboard build has package script that asserts generated identity is present in compiled bundle"
else
  step_fail DASHBOARD_RELEASE_VERSION_BUNDLE_ASSERTION "package_script=$package_script assert_checks=$(printf '%s' "$assert_checks" | head -5 | tr '\n' ' ')"
fi

test_end
