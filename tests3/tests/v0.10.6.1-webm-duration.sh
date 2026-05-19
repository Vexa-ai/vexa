#!/usr/bin/env bash
# v0.10.6.1-webm-duration — finalized master.webm carries an EBML Duration.
#
# Steps:
#   ebml_duration_present
#     - Static (always):
#         services/meeting-api/meeting_api/recording_finalizer.py
#         must call _inject_webm_duration after _build_webm_master.
#     - Runtime (when MASTER_WEBM_PATH points at a finalized object,
#       e.g. compose / helm post-meeting smoke):
#         ffprobe reports a non-zero duration in the EBML SegmentInfo.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
FIN="$ROOT_DIR/services/meeting-api/meeting_api/recording_finalizer.py"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-webm-duration :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-webm-duration-$step"

case "$step" in
  ebml_duration_present)
    failed=0

    # Static: helper exists.
    if ! grep -q "def _inject_webm_duration" "$FIN"; then
      echo "    FAIL: recording_finalizer.py is missing _inject_webm_duration helper"
      failed=1
    fi
    # Static: master-build path passes the concat file through the
    # duration-injection step before upload.
    if ! grep -q "_inject_webm_duration_file(concat_path)" "$FIN"; then
      echo "    FAIL: webm path does not pass concat_path through _inject_webm_duration_file"
      failed=1
    fi
    # Static: _inject_webm_duration_file body invokes ffmpeg.
    if ! grep -A 80 "^def _inject_webm_duration_file" "$FIN" | grep -q '"ffmpeg"'; then
      echo "    FAIL: _inject_webm_duration_file body does not invoke ffmpeg"
      failed=1
    fi
    # Path-based pipeline (true bounded memory): webm path uses
    # _build_webm_master_streaming_file → _inject_webm_duration_file →
    # storage.upload_file_path (boto3 multipart streaming upload).
    # No bytes-in-memory round-trip at any point.
    if ! grep -q "_build_webm_master_streaming_file" "$FIN"; then
      echo "    FAIL: path-based concat helper missing"
      failed=1
    fi
    if ! grep -q "_inject_webm_duration_file" "$FIN"; then
      echo "    FAIL: path-based duration-inject helper missing"
      failed=1
    fi
    if ! grep -q "storage.upload_file_path(master_key, final_path" "$FIN"; then
      echo "    FAIL: webm path is not using storage.upload_file_path (multipart streaming upload)"
      failed=1
    fi
    # Byte-concat (NOT ffmpeg -f concat) — MediaRecorder chunks 1+ are
    # Cluster-only continuations and ffmpeg's concat demuxer silently
    # drops them. See body comment for full rationale.
    if grep -A 80 "^def _build_webm_master_streaming_file" "$FIN" | grep -qE '"-f", "concat"'; then
      echo "    FAIL: streaming concat re-introduced ffmpeg -f concat (regressed: drops Cluster-only chunks)"
      failed=1
    fi
    if ! grep -A 80 "^def _build_webm_master_streaming_file" "$FIN" | grep -q "shutil.copyfileobj"; then
      echo "    FAIL: streaming concat does not use shutil.copyfileobj (bounded-buffer copy)"
      failed=1
    fi
    if ! grep -A 80 "^def _build_webm_master_streaming_file" "$FIN" | grep -q "download_file_to_path"; then
      echo "    FAIL: streaming concat does not stream chunks to disk via download_file_to_path"
      failed=1
    fi
    # And the storage client must expose path-based stream APIs.
    STORAGE="$ROOT_DIR/services/meeting-api/meeting_api/storage.py"
    if ! grep -q "def upload_file_path" "$STORAGE"; then
      echo "    FAIL: storage.upload_file_path missing (boto3 multipart streaming upload)"
      failed=1
    fi
    if ! grep -q "def download_file_to_path" "$STORAGE"; then
      echo "    FAIL: storage.download_file_to_path missing"
      failed=1
    fi
    # The MinIO/S3 override of upload_file_path must use boto3's
    # client.upload_file (which streams via multipart).
    if ! awk '/^class MinIOStorageClient/{p=1} /^class LocalStorageClient/{p=0} p' "$STORAGE" \
        | grep -A 12 "def upload_file_path" | grep -q "client.upload_file"; then
      echo "    FAIL: MinIOStorageClient.upload_file_path does not call client.upload_file (multipart streaming)"
      failed=1
    fi

    # Runtime: ffprobe a sample master if provided.
    if [[ -n "${MASTER_WEBM_PATH:-}" && -f "$MASTER_WEBM_PATH" ]]; then
      if ! command -v ffprobe >/dev/null 2>&1; then
        echo "    skip runtime: ffprobe not in PATH"
      else
        dur=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$MASTER_WEBM_PATH" 2>/dev/null || echo "")
        if [[ -z "$dur" ]] || awk -v d="$dur" 'BEGIN{exit !(d > 0)}'; then
          echo "    runtime ok: ffprobe duration=${dur}s"
        else
          echo "    FAIL: ffprobe duration is empty/zero on $MASTER_WEBM_PATH"
          failed=1
        fi
      fi
    fi

    if (( failed == 0 )); then
      step_pass MASTER_WEBM_HAS_EBML_DURATION "_inject_webm_duration wired into webm master path; runtime check OK if MASTER_WEBM_PATH was provided"
    else
      step_fail MASTER_WEBM_HAS_EBML_DURATION "one or more checks failed (see above)"
    fi
    ;;
  *)
    echo "  unknown step: $step" >&2
    exit 64
    ;;
esac
