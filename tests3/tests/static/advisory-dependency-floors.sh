#!/usr/bin/env bash
# advisory-dependency-floors — release-local dependency floor guard.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$ROOT_DIR/tests3/lib/common.sh"

test_begin "advisory-dependency-floors"

if output="$(python3 - <<'PY' "$ROOT_DIR"
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
failures: list[str] = []

def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value)[:3])

dashboard_lock = root / "services/dashboard/package-lock.json"
with dashboard_lock.open() as f:
    lock = json.load(f)

postcss_versions = []
for package_path, package in (lock.get("packages") or {}).items():
    if package_path.endswith("node_modules/postcss"):
        version = package.get("version", "")
        postcss_versions.append(version)
        if version_tuple(version) < (8, 5, 10):
            failures.append(f"{package_path} has postcss {version}, expected >= 8.5.10")

if not postcss_versions:
    failures.append("services/dashboard/package-lock.json has no postcss package entry")

transcription_requirements = root / "services/transcription-service/requirements.txt"
if re.search(r"^python-multipart\b", transcription_requirements.read_text(), re.M):
    failures.append("services/transcription-service/requirements.txt still installs python-multipart")

transcription_main = (root / "services/transcription-service/main.py").read_text()
if any(token in transcription_main for token in ("File,", "UploadFile", "Form,")):
    failures.append("transcription-service still imports FastAPI multipart helpers")
if "await request.body()" in transcription_main:
    failures.append("transcription-service multipart parser reads full body before enforcing size")

requirement_paths = [
    "deploy/lite/requirements.txt",
    "services/api-gateway/requirements.txt",
    "services/meeting-api/requirements.txt",
]
for rel in requirement_paths:
    path = root / rel
    text = path.read_text()
    match = re.search(r"^python-multipart\s*>=\s*([0-9.]+)", text, re.M)
    if not match:
        failures.append(f"{rel} does not declare python-multipart>=0.0.20")
    elif version_tuple(match.group(1)) < (0, 0, 20):
        failures.append(f"{rel} has python-multipart>={match.group(1)}, expected >=0.0.20")

lite_requirements = (root / "deploy/lite/requirements.txt").read_text()
tts_requirements = (root / "services/tts-service/requirements.txt").read_text()
for package in ("piper-tts", "numpy", "langdetect"):
    if re.search(rf"^{re.escape(package)}\b", tts_requirements, re.M) and not re.search(
        rf"^{re.escape(package)}\b", lite_requirements, re.M
    ):
        failures.append(f"deploy/lite/requirements.txt is missing tts-service dependency {package}")

pyproject = root / "services/meeting-api/pyproject.toml"
pyproject_text = pyproject.read_text()
match = re.search(r'"python-multipart\s*>=\s*([0-9.]+)"', pyproject_text)
if not match:
    failures.append("services/meeting-api/pyproject.toml does not declare python-multipart>=0.0.20")
elif version_tuple(match.group(1)) < (0, 0, 20):
    failures.append(f"services/meeting-api/pyproject.toml has python-multipart>={match.group(1)}, expected >=0.0.20")

if failures:
    print("; ".join(failures))
    sys.exit(1)

print("postcss versions=" + ",".join(sorted(set(postcss_versions))) + "; transcription-service uses bounded stdlib multipart parser; remaining python-multipart floors >=0.0.20; lite carries tts-service runtime deps")
PY
)"; then
  step_pass PRE_RELEASE_SECURITY_DEPENDENCY_FLOORS "$output"
else
  step_fail PRE_RELEASE_SECURITY_DEPENDENCY_FLOORS "$output"
fi

test_end
