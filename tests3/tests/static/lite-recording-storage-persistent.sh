#!/usr/bin/env bash
# lite-recording-storage-persistent — Lite redeploys must not erase recordings.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$ROOT_DIR/tests3/lib/common.sh"

test_begin "lite-recording-storage-persistent"

makefile="$ROOT_DIR/deploy/lite/Makefile"
dockerfile="$ROOT_DIR/deploy/lite/Dockerfile.lite"

if grep -q -- '-v .*:/var/lib/vexa/recordings' "$makefile"; then
  step_pass LITE_RECORDING_STORAGE_PERSISTENT "deploy/lite/Makefile mounts persistent storage at /var/lib/vexa/recordings"
else
  step_fail LITE_RECORDING_STORAGE_PERSISTENT "deploy/lite/Makefile runs vexa-lite without mounting /var/lib/vexa/recordings"
fi

if grep -q 'LOCAL_STORAGE_DIR=/var/lib/vexa/recordings' "$dockerfile"; then
  step_pass LITE_RECORDING_STORAGE_DIR_SSOT "Dockerfile.lite points LOCAL_STORAGE_DIR at /var/lib/vexa/recordings"
else
  step_fail LITE_RECORDING_STORAGE_DIR_SSOT "Dockerfile.lite LOCAL_STORAGE_DIR does not match persistent mount path"
fi

test_end
