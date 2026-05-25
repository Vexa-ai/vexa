#!/usr/bin/env bash
# ==============================================================================
# build-zoom-sdk.sh — operator-facing Zoom Meeting SDK bootstrap
# ==============================================================================
#
# Pack 0.10.6x-pack-zoom-sdk-restore-capability-boundary (epic #370).
#
# Self-hosted Vexa operators run this script ONCE on their build host to
# bootstrap the `vexa-bot:sdk` image. It does NOT redistribute any Zoom
# proprietary binary; it instructs the operator to download the SDK
# from Zoom under their own Marketplace EULA, then verifies the layout,
# installs build deps, and invokes node-gyp.
#
# Usage:
#   scripts/build-zoom-sdk.sh                    # interactive: prints
#                                                # instructions and validates
#   scripts/build-zoom-sdk.sh --validate-only    # CI / smoke: checks shape
#                                                # without invoking build
#   scripts/build-zoom-sdk.sh --build            # full build (after SDK drop)
#
# This script's SHAPE is exercised by the synthetic gate (build-script
# smoke). The actual binary build is operator-only and is the gating
# step for the live human Zoom meeting gate.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SDK_DIR="${REPO_ROOT}/services/vexa-bot/core/src/platforms/zoom/native/zoom_meeting_sdk"
BOT_DIR="${REPO_ROOT}/services/vexa-bot"
MODE="${1:---help}"

print_help() {
    cat <<'EOF'
build-zoom-sdk.sh — bootstrap the vexa-bot:sdk image

Steps an operator must complete manually BEFORE running this script
with --build:

  1. Register a Zoom Marketplace app (General App / Server-to-Server
     OAuth). Note the ZOOM_CLIENT_ID and ZOOM_CLIENT_SECRET.

  2. From the Marketplace dashboard, download the Linux Meeting SDK
     archive. The OSS-licensed Vexa repo cannot redistribute this
     archive — you accept Zoom's EULA when you download it.

  3. Extract the archive into:
       services/vexa-bot/core/src/platforms/zoom/native/zoom_meeting_sdk/
     The directory should contain (at minimum):
       - libmeetingsdk.so
       - qt_libs/
       - h/  (headers — already tracked in repo)

  4. Run: scripts/build-zoom-sdk.sh --validate-only
     to confirm the layout is correct.

  5. Run: scripts/build-zoom-sdk.sh --build
     to build the addon and the vexa-bot:sdk image.

Flags:
  --help             Print this help.
  --validate-only    Check layout + .gitignore + deps; no build.
  --build            Validate then build native addon + Docker image.

This script never commits, pushes, or caches the SDK binary anywhere.
EOF
}

assert_gitignored() {
    local pattern="$1"
    # The .gitignore line should be present; we don't try to evaluate
    # the rule, just confirm the operator hasn't accidentally untracked it.
    if ! grep -q -F "${pattern}" "${BOT_DIR}/.gitignore"; then
        echo "ERROR: services/vexa-bot/.gitignore is missing the rule for: ${pattern}"
        echo "       Refusing to proceed — the Zoom SDK binary would be at risk of being committed."
        exit 1
    fi
}

validate_layout() {
    echo "[1/5] validating .gitignore license firewall..."
    assert_gitignored "libmeetingsdk.so"
    assert_gitignored "qt_libs/"
    echo "      OK"

    echo "[2/5] checking SDK staging directory..."
    if [[ ! -d "${SDK_DIR}" ]]; then
        echo "ERROR: ${SDK_DIR} does not exist."
        echo "       Create it and drop the Zoom Meeting SDK archive contents into it."
        exit 1
    fi
    echo "      OK (${SDK_DIR})"

    echo "[3/5] checking SDK headers (always-present, not gitignored)..."
    if [[ ! -d "${SDK_DIR}/h" ]]; then
        echo "ERROR: ${SDK_DIR}/h is missing. The Zoom SDK headers should ship in-repo."
        exit 1
    fi
    echo "      OK"

    echo "[4/5] checking proprietary binaries (gitignored, operator-supplied)..."
    local missing=()
    [[ -f "${SDK_DIR}/libmeetingsdk.so" ]] || missing+=("libmeetingsdk.so")
    [[ -d "${SDK_DIR}/qt_libs" ]] || missing+=("qt_libs/")
    if (( ${#missing[@]} > 0 )); then
        echo "      MISSING (this is expected on a fresh clone): ${missing[*]}"
        echo "      Drop the Zoom Marketplace SDK archive into ${SDK_DIR} and re-run."
        return 1
    fi
    echo "      OK (binaries present)"

    echo "[5/5] checking binding.gyp..."
    if [[ ! -f "${BOT_DIR}/binding.gyp" ]]; then
        echo "ERROR: ${BOT_DIR}/binding.gyp is missing."
        exit 1
    fi
    echo "      OK"
    return 0
}

do_build() {
    if ! validate_layout; then
        echo
        echo "Validation failed. Resolve the issues above before --build."
        exit 1
    fi

    echo
    echo "[build] installing build dependencies (apt: build-essential cmake qtbase5-dev libssl-dev) ..."
    echo "        (sudo apt install build-essential cmake python3 libssl-dev qtbase5-dev)"
    echo "        Skipping in this script — assumed present on the build host."

    echo "[build] invoking node-gyp ..."
    (
        cd "${BOT_DIR}"
        npm install --ignore-scripts
        npm run build:native
    )

    echo "[build] building vexa-bot:sdk Docker image ..."
    (
        cd "${REPO_ROOT}"
        docker build \
            --platform linux/amd64 \
            -f services/vexa-bot/Dockerfile.sdk \
            -t vexaai/vexa-bot:sdk \
            services/vexa-bot
    )

    echo
    echo "Done. To use the SDK image, set BOT_IMAGE_NAME=vexaai/vexa-bot:sdk"
    echo "and ZOOM_CLIENT_ID + ZOOM_CLIENT_SECRET in your meeting-api env."
}

case "${MODE}" in
    --help|-h|"")
        print_help
        ;;
    --validate-only)
        # Even when binaries are absent (fresh clone), we still pass the
        # "shape check" so CI can run this against a stubbed tree.
        validate_layout || {
            echo
            echo "Shape OK, binaries absent. Re-run with --build after dropping the SDK."
        }
        ;;
    --build)
        do_build
        ;;
    *)
        echo "Unknown mode: ${MODE}" >&2
        print_help
        exit 2
        ;;
esac
