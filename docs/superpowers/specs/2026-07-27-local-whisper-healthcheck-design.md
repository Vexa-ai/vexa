# Local Whisper healthcheck correction

Status: approved

## Context

The bundled `LOCAL_STT=1` path runs the third-party
`fedirz/faster-whisper-server:latest-cpu` image. Its baked Docker healthcheck invokes
`curl`, but that image does not contain `curl`. The service itself is running and
`GET /health` returns HTTP 200, while Docker reports the container as unhealthy.

The image contains `python3`, and the server already runs under Python. Inspection
found no application or entrypoint script that invokes `curl`; the missing binary
affects the baked healthcheck only.

## Goal

Make Docker report the local Whisper container healthy when its existing
`http://localhost:8000/health` endpoint returns HTTP 200, without changing
transcription behavior or maintaining a derived Whisper image.

## Design

At the Vexa Lite `docker run` boundary, override only the third-party image's
healthcheck command with a Python standard-library HTTP probe. Preserve the
image's command, model configuration, network, ports, restart policy, and health
timing.

The override remains an ALLOY-owned opt-in:
`ALLOY_STT_HEALTHCHECK=1` selects the Python probe; absent, empty, or `0`
retains the upstream image healthcheck unchanged. The default and rollback
value are `0`. The launch override explicitly preserves the image's observed
5-second interval, 3-second timeout, and 30 retries. The switch, default,
enabled behavior, and rollback value are documented in
`docs/ALLOY-CUSTOMIZATIONS.md`.

## Verification

1. A narrow static regression first proves the Lite launch command does not
   contain the override when the switch is disabled and does contain the Python
   probe when enabled.
2. Recreate only `vexa-lite-whisper` with the switch enabled.
3. Confirm the direct endpoint remains HTTP 200 and Docker transitions to
   `healthy`.
4. Confirm the Whisper process command and selected image are unchanged.

## Non-goals

- Installing `curl` into a running container.
- Building or publishing a derived Whisper image.
- Changing the STT API, model, language handling, or transcription output.
- Changing healthchecks for other Vexa services.
