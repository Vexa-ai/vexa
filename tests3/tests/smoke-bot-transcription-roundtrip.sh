#!/usr/bin/env bash
# smoke-bot-transcription-roundtrip — verify the bot's TranscriptionClient
# auth + URL configuration can complete a synth->whisper roundtrip against
# the operator-side transcription endpoint.
#
# Steps:
#   roundtrip
#     - Read TRANSCRIPTION_SERVICE_URL + TRANSCRIPTION_SERVICE_TOKEN from the
#       deployed runtime container for the current mode.
#     - Assert the deployed runtime values match deploy/compose/.env, the LOCAL
#       deployment SSOT. Do not accept caller-env overrides or fallback files.
#     - POST tests3/testdata/test-speech-en.wav to the URL with
#       Authorization: Bearer <token>.
#     - Assert HTTP 200 + non-empty `segments` in the JSON response.
#
# Mode: compose + helm (any mode with a configured transcription endpoint).
#
# This prove was missing in v0.10.6.1's first 4 validate iterations. The
# matrix had per-scope-item proves but ZERO end-to-end check that the bot's
# transcription credentials would actually authenticate against the
# operator's lb. Result: bot 10060 ran 30/30 chunks at HTTP 401 with the
# wrong token, validate⁴ went GREEN, human-gate caught it as a regression.
#
# Added under protocol-exception #4 (validate-coverage gap closure) per
# triage 2026-05-11T19:07:23Z.

set -euo pipefail

# shellcheck source=lib/common.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$ROOT_DIR/tests3/lib/common.sh"

STEP="${1:-roundtrip}"

case "$STEP" in
    roundtrip)
        test_begin smoke-bot-transcription-roundtrip-roundtrip

        MODE="$(_detect_mode_cached)"

        # Source URL + token from the deployed runtime only. The SSOT file is
        # used only for equality checks, never as a rescue path.
        case "$MODE" in
            lite)
                URL="$(docker exec vexa-lite printenv TRANSCRIPTION_SERVICE_URL 2>/dev/null || true)"
                TOKEN="$(docker exec vexa-lite printenv TRANSCRIPTION_SERVICE_TOKEN 2>/dev/null || true)"
                ;;
            compose)
                URL="$(docker exec vexa-runtime-api-1 printenv TRANSCRIPTION_SERVICE_URL 2>/dev/null || true)"
                TOKEN="$(docker exec vexa-runtime-api-1 printenv TRANSCRIPTION_SERVICE_TOKEN 2>/dev/null || true)"
                ;;
            helm)
                NS="${KUBE_NAMESPACE:-default}"
                RUNTIME_POD="$(kubectl -n "$NS" get pod -l app.kubernetes.io/component=runtime-api -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
                if [ -n "$RUNTIME_POD" ]; then
                    URL="$(kubectl -n "$NS" exec "$RUNTIME_POD" -- printenv TRANSCRIPTION_SERVICE_URL 2>/dev/null || true)"
                    TOKEN="$(kubectl -n "$NS" exec "$RUNTIME_POD" -- printenv TRANSCRIPTION_SERVICE_TOKEN 2>/dev/null || true)"
                fi
                ;;
            *)
                URL=""
                TOKEN=""
                ;;
        esac

        if [ -z "$URL" ]; then
            step_fail SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP "TRANSCRIPTION_SERVICE_URL not set in deployed runtime container"
            exit 0
        fi
        if [ -z "$TOKEN" ]; then
            step_fail SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP "TRANSCRIPTION_SERVICE_TOKEN not set in deployed runtime container"
            exit 0
        fi

        case "$MODE" in
            compose|lite)
                SSOT_ENV="$ROOT_DIR/deploy/compose/.env"
                if [ "$MODE" = "lite" ]; then
                    SSOT_ENV="$ROOT_DIR/.env"
                    SSOT_URL="$(awk -F= '$1 == "LITE_TRANSCRIPTION_SERVICE_URL" { sub(/^[^=]*=/, ""); print; exit }' "$SSOT_ENV" 2>/dev/null || true)"
                    SSOT_TOKEN="$(awk -F= '$1 == "LITE_TRANSCRIPTION_SERVICE_TOKEN" { sub(/^[^=]*=/, ""); print; exit }' "$SSOT_ENV" 2>/dev/null || true)"
                else
                    SSOT_URL="$(awk -F= '$1 == "TRANSCRIPTION_SERVICE_URL" { sub(/^[^=]*=/, ""); print; exit }' "$SSOT_ENV" 2>/dev/null || true)"
                    SSOT_TOKEN="$(awk -F= '$1 == "TRANSCRIPTION_SERVICE_TOKEN" { sub(/^[^=]*=/, ""); print; exit }' "$SSOT_ENV" 2>/dev/null || true)"
                fi
                if [ -z "$SSOT_URL" ] || [ -z "$SSOT_TOKEN" ]; then
                    step_fail SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP "deploy/compose/.env SSOT is missing TRANSCRIPTION_SERVICE_URL or TRANSCRIPTION_SERVICE_TOKEN"
                    exit 0
                fi
                if [ "$URL" != "$SSOT_URL" ]; then
                    step_fail SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP "runtime TRANSCRIPTION_SERVICE_URL does not match deploy/compose/.env SSOT"
                    exit 0
                fi
                if [ "$TOKEN" != "$SSOT_TOKEN" ]; then
                    step_fail SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP "runtime TRANSCRIPTION_SERVICE_TOKEN does not match deploy/compose/.env SSOT"
                    exit 0
                fi
                ;;
            helm)
                NS="${KUBE_NAMESPACE:-default}"
                SECRET_TOKEN="$(kubectl -n "$NS" get secret vexa-vexa-secrets -o jsonpath='{.data.TRANSCRIPTION_SERVICE_TOKEN}' 2>/dev/null | base64 -d 2>/dev/null || true)"
                if [ -z "$SECRET_TOKEN" ]; then
                    step_fail SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP "helm transcription token SSOT missing from vexa-vexa-secrets"
                    exit 0
                fi
                if [ "$TOKEN" != "$SECRET_TOKEN" ]; then
                    step_fail SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP "runtime TRANSCRIPTION_SERVICE_TOKEN does not match helm secret SSOT"
                    exit 0
                fi
                if echo "$TOKEN" | grep -q 'local-''test-token'; then
                    step_fail SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP "runtime transcription URL/token contains forbidden placeholder"
                    exit 0
                fi
                ;;
        esac

        SAMPLE="$ROOT_DIR/tests3/testdata/test-speech-en.wav"
        if [ ! -r "$SAMPLE" ]; then
            step_fail SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP "audio sample missing: $SAMPLE"
            exit 0
        fi

        # If URL hostname is internal, proxy the curl
        # through the deployed runtime container so DNS + token match the
        # stack being validated. Otherwise use host curl directly.
        OUT_FILE="$(mktemp -t smoke-transcription-XXXXXX.json)"
        # Preserve common.sh's JSON-report flush while still cleaning the
        # temporary transcription response.
        trap 'rm -f "$OUT_FILE"; _flush_test_report' EXIT INT TERM

        POST_SCRIPT='
import sys, os, urllib.request, urllib.error, uuid
url, token, sample = sys.argv[1], sys.argv[2], sys.argv[3]
with open(sample, "rb") as f:
    body = f.read()
boundary = uuid.uuid4().hex
parts = (
    f"--{boundary}\r\n"
    f"Content-Disposition: form-data; name=\"model\"\r\n\r\n"
    f"whisper-1\r\n"
    f"--{boundary}\r\n"
    f"Content-Disposition: form-data; name=\"file\"; filename=\"sample.wav\"\r\n"
    f"Content-Type: audio/wav\r\n\r\n"
).encode() + body + f"\r\n--{boundary}--\r\n".encode()
req = urllib.request.Request(
    url, data=parts, method="POST",
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    },
)
try:
    with urllib.request.urlopen(req, timeout=90) as r:
        print(r.status, flush=True)
        sys.stdout.flush()
        os.write(2, r.read())
except urllib.error.HTTPError as e:
    print(e.code, flush=True)
    os.write(2, e.read())
except Exception as e:
    print(0, flush=True)
    os.write(2, f"transport-error: {e}".encode())
'

        ATTEMPT=0
        while :; do
            ATTEMPT=$((ATTEMPT + 1))
            : > "$OUT_FILE"
            if echo "$URL" | grep -qE '^https?://transcription-(lb|dev)(/|:)'; then
                case "$MODE" in
                    lite) PROBE_CONTAINER="vexa-lite" ;;
                    helm)
                        NS="${KUBE_NAMESPACE:-default}"
                        RUNTIME_POD="$(kubectl -n "$NS" get pod -l app.kubernetes.io/component=runtime-api -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
                        [ -n "$RUNTIME_POD" ] || { step_fail SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP "runtime-api pod not found"; exit 0; }
                        kubectl -n "$NS" cp "$SAMPLE" "$RUNTIME_POD":/tmp/smoke-sample.wav >/dev/null 2>&1 \
                            || { step_fail SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP "kubectl cp to $RUNTIME_POD failed"; exit 0; }
                        HTTP_CODE="$(kubectl -n "$NS" exec "$RUNTIME_POD" -- python3 -c "$POST_SCRIPT" "$URL" "$TOKEN" /tmp/smoke-sample.wav 2>"$OUT_FILE")"
                        kubectl -n "$NS" exec "$RUNTIME_POD" -- rm -f /tmp/smoke-sample.wav 2>/dev/null || true
                        ;;
                    *) PROBE_CONTAINER="vexa-runtime-api-1" ;;
                esac
                if [ "$MODE" != "helm" ]; then
                    docker cp "$SAMPLE" "$PROBE_CONTAINER":/tmp/smoke-sample.wav >/dev/null 2>&1 \
                        || { step_fail SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP "docker cp to $PROBE_CONTAINER failed"; exit 0; }
                fi
                # Run python inside meeting-api: stdout=HTTP_CODE, stderr=body.
                # We capture stderr (body) to OUT_FILE and stdout to a var.
                if [ "$MODE" != "helm" ]; then
                    HTTP_CODE="$(docker exec "$PROBE_CONTAINER" python3 -c "$POST_SCRIPT" "$URL" "$TOKEN" /tmp/smoke-sample.wav 2>"$OUT_FILE")"
                    docker exec "$PROBE_CONTAINER" rm -f /tmp/smoke-sample.wav 2>/dev/null || true
                fi
            else
                # External / host-reachable URL: use host curl directly.
                HTTP_CODE="$(curl -sS -o "$OUT_FILE" -w '%{http_code}' \
                    --max-time 20 \
                    -H "Authorization: Bearer $TOKEN" \
                    -F "model=whisper-1" \
                    -F "file=@$SAMPLE;type=audio/wav" \
                    "$URL")"
            fi

            if [ "$HTTP_CODE" != "503" ] || [ "$ATTEMPT" -ge 3 ]; then
                break
            fi
            info "transcription service busy (HTTP 503), retrying attempt $((ATTEMPT + 1))/3..."
            sleep $((ATTEMPT * 5))
        done

        if [ "$HTTP_CODE" != "200" ]; then
            BODY="$(head -c 200 "$OUT_FILE" 2>/dev/null || echo '<empty>')"
            step_fail SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP "HTTP $HTTP_CODE from $URL — body: $BODY"
            exit 0
        fi

        SEG_COUNT="$(python3 -c "
import json, sys
try:
  d = json.load(open('$OUT_FILE'))
  segs = d.get('segments') or d.get('text') or []
  print(len(segs) if isinstance(segs, list) else (1 if segs else 0))
except Exception as e:
  print(0)
")"
        if [ "$SEG_COUNT" = "0" ]; then
            BODY="$(head -c 200 "$OUT_FILE" 2>/dev/null || echo '<empty>')"
            step_fail SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP "HTTP 200 but no segments returned — body: $BODY"
            exit 0
        fi

        step_pass SMOKE_BOT_TRANSCRIPTION_ROUNDTRIP "HTTP 200 + $SEG_COUNT segment(s) returned from $URL"
        ;;

    *)
        echo "FAIL: unknown step '$STEP' (expected: roundtrip)" >&2
        exit 2
        ;;
esac
