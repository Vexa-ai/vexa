# ALLOY customizations

## `ALLOY_STT_HEALTHCHECK`

- Surface: Vexa Lite `LOCAL_STT=1`.
- Default: `0` or unset — retain the third-party Whisper image healthcheck.
- Enabled: `1` — override only the health command with a Python `GET /health` probe while
  preserving the `5s` interval, `3s` timeout, and `30` retries.
- Rollback: set `ALLOY_STT_HEALTHCHECK=0` and recreate `vexa-lite-whisper`.
- Scope: Docker self-health reporting only; the image, model, STT API, and transcription path are
  unchanged.
