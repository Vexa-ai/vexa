# ALLOY local customizations

This file is the registry for downstream Alloy behavior carried by this Vexa checkout. It
separates local product decisions from upstream Vexa defaults and provides an explicit rollback
path for every customization.

## Mandatory marking and compatibility rules

- Environment variables and feature flags owned by Alloy use the `ALLOY_*` prefix.
- Alloy-owned code blocks and explanatory comments are marked `ALLOY:`.
- Alloy-specific diagnostics use the `[ALLOY]` prefix.
- An absent or disabled Alloy flag must preserve an upstream-compatible Vexa path.
- Existing Vexa contracts, APIs, services, and identifiers keep their original names.
- Custom logic belongs at one responsible boundary and is reused by callers. Follow DRY and
  SOLID proportionately to reduce duplication and coupling and to keep rollback and testing clear.
- Add every new Alloy switch to the registry below in the same change that introduces it.

## Configuration registry

### `ALLOY_STT_MAX_CONCURRENCY`

- **Purpose:** apply backpressure before an OpenAI-compatible STT dependency so the independent
  participant lanes owned by one bot cannot flood that bot's transcription client.
- **Default:** `0`.
- **Approved local pilot value:** `1`.
- **Enabled behavior:** at most the configured number of STT HTTP requests execute concurrently
  inside one bot process; participant buffers continue accumulating audio while waiting. This is
  not a shared limit across bot processes, meetings, or the Whisper service.
- **Disabled/rollback behavior:** unset, empty, or `0` leaves requests unlimited by the Alloy
  adapter. Set `0` and restart Lite so newly spawned bots use the upstream-compatible path.
- **Scope:** STT request scheduling only. It does not change capture, speaker lanes, transcript
  contracts, or the selected Whisper model.

### `ALLOY_STT_LANGUAGE_MODE`

- **Purpose:** allow multilingual and code-switching meetings without pinning one language for the
  whole session.
- **Default:** `configured`.
- **Approved local pilot value:** `auto`.
- **Enabled behavior (`auto`):** no language is sent to Whisper; the model detects the language for
  each submitted audio window. This is the mechanism intended to support Russian, English, and
  switching between them; the real multilingual meeting result is not yet validated.
- **Backend prerequisite:** Lite's bundled `Systran/faster-whisper-tiny.en` default and
  `Systran/faster-whisper-small.en` example are English-only. The local multilingual pilot uses
  the separate make variable `WHISPER_MODEL=Systran/faster-whisper-small`; it is not an ALLOY flag
  or a request-language pin, and does not by itself establish a live PASS.
- **Disabled/rollback behavior (`configured`, unset, or empty):** Vexa forwards the
  invocation-configured language when one exists. Set `configured` and restart Lite so newly
  spawned bots use the upstream-compatible path.
- **Scope:** language selection at the STT boundary only. It does not change transcript formatting
  or speaker attribution.

### `ALLOY_STT_CHANNEL_BACKPRESSURE`

- **Purpose:** prevent successive turns of one physical Meet audio channel from creating an
  unbounded queue while CPU Whisper is still processing an earlier turn.
- **Default:** `0`.
- **Approved local pilot value:** `1`.
- **Enabled behavior:** each `ch-N` has one active STT request and one replaceable latest pending
  request. A newer pending turn supersedes an older one before it reaches Whisper.
- **Disabled/rollback behavior:** unset, empty, or `0` dispatches every turn using Vexa's original
  scheduling. Set `0` and restart Lite so newly spawned bots use that path.
- **Scope:** Google Meet per-channel STT scheduling only. Capture, transcript schemas, and
  cross-channel concurrency remain unchanged.

### `ALLOY_STT_TELEMETRY`

- **Purpose:** make real STT pressure observable instead of inferring it from transcript pauses.
- **Default:** `0`.
- **Approved local pilot value:** `1`.
- **Enabled behavior:** each Google Meet bot publishes a short-lived per-meeting snapshot to Redis;
  Meeting API reads exact keys for owner-owned active meetings, silently omits invalid snapshots,
  and computes the sealed aggregate and its health; Gateway forwards `GET /alloy/stt/status`;
  Terminal calls `GET /api/alloy/stt/status` once per second only while visible. Timer and
  visibility triggers reuse the current promise within one active polling generation. Stop/restart
  may start a fresh request while the invalidated generation's network call is still pending;
  generation fencing ignores that old result. Transcript or workspace sharing does not grant
  telemetry access.
- **Disabled/rollback behavior:** unset, empty, or `0` leaves the Meeting API and Gateway routes
  absent. Terminal creates no hook, subscription, timer, or fetch and renders the upstream reset
  footer. Set `0` and restart Lite; normal capture and transcription remain unchanged.
- **Metrics:** active requests, waiting channels, queued audio seconds, capture-to-processing lag,
  exponential moving-average RTF, processed/superseded windows, freshness, and the last worker
  error.
- **Transport:** Redis keys use `alloy:stt:telemetry:v1:{meeting_id}` with a short TTL. Meeting API
  uses one bounded `MGET` for verified owner-owned active meetings; it never scans Redis. Invalid,
  incompatible, version-mismatched, and non-finite snapshots are silently omitted.
- **Contract:** sealed `alloy-stt-telemetry.v1` owns `Snapshot`, the server-computed `Aggregate`,
  and `StatusResponse`. Redis transport failure returns its unavailable response and never changes
  transcription state.
- **Meaning of global:** the Terminal displays one aggregate across the current owner's meetings;
  this does not turn `ALLOY_STT_MAX_CONCURRENCY` into a global limiter.
- **Scope:** diagnostics only. The flag does not alter STT cadence, buffering, transcript text,
  language detection, speaker attribution, or meeting lifecycle.

### `NEXT_PUBLIC_ALLOY_HIDE_EMPTY_ROOM_COUNT`

- **Purpose:** do not present Vexa's placeholder `participants: []` as a real Meet roster count.
- **Default:** `0`.
- **Approved local pilot build value:** `1`.
- **Enabled behavior:** hide `0 in the room`; display a positive count if a real roster is added
  later.
- **Disabled/rollback behavior:** unset, empty, or `0` preserves Vexa's original label. Set `0` and
  rebuild the Lite image.
- **Scope:** terminal UI only. This flag does not implement participant discovery.
- **Build-time note:** this `NEXT_PUBLIC_*` value is compiled into the terminal bundle and requires
  rebuilding the Lite image.

### `ALLOY_SKIP_HF_CACHE_WARM`

- **Purpose:** keep local Lite rebuilds offline when the mixed-lane pyannote cache is irrelevant to
  the Google Meet-only validation path.
- **Default:** `0`.
- **Approved local pilot build value:** `1`.
- **Enabled behavior:** skip `warm-hf-cache.mjs` during the bot-builder stage.
- **Disabled/rollback behavior:** unset, empty, or `0` preserves Vexa's original best-effort cache
  warm. Set `0` and rebuild the Lite image.
- **Scope:** image build only. Runtime model selection and Google Meet/Whisper behavior are
  unchanged.

### `ALLOY_LITE_BUNDLED_PYTHON`

- **Purpose:** let a clean local Lite build use a compatible Python 3.12 without relying on `uv`
  to download managed CPython from GitHub.
- **Default:** `0` or unset.
- **Approved local pilot build value:** `1`.
- **Enabled behavior (`1` exactly):** select the ALLOY Python bootstrap stage, copy `/usr/local`
  from pinned `python:3.12-slim-bullseye`, verify Python 3.12, and then run the same five existing
  service-venv commands.
- **Disabled/rollback behavior:** unset, empty, `0`, or another value selects the original
  Playwright-jammy base and leaves every `uv venv --python 3.12` path unchanged. Set `0` and rebuild
  Lite to roll back.
- **Scope:** Lite image build only. Runtime service behavior, dependency commands, models,
  transcription, and other deployment shapes are unchanged.
- **Configuration boundary:** the Makefile is the only resolver for the public flag. It normalizes
  exact `1` to the bundled stage for both local and multi-architecture builds; all other values
  resolve to the upstream stage.

### `ALLOY_STT_HEALTHCHECK`

- **Purpose:** correct the bundled third-party Whisper image's unusable `curl` self-probe for a
  local Lite run without changing the image or transcription contract.
- **Default:** `0` or unset.
- **Enabled behavior (`1` exactly):** replace only the health command with a Python `GET /health`
  probe while preserving the 5-second interval, 3-second timeout, and 30 retries.
- **Disabled/rollback behavior:** unset, empty, `0`, or another value leaves the third-party image
  healthcheck unchanged. Set `0` and recreate `vexa-lite-whisper` to roll back.
- **Activation lifecycle:** the override is applied only when Lite creates `vexa-lite-whisper`.
  Remove an existing container before starting Lite with `ALLOY_STT_HEALTHCHECK=1` so Make
  recreates it with the Python probe; the automatic lifecycle otherwise remains unchanged.
- **Scope:** Docker self-health reporting only; the image, model, STT API, and transcription path
  are unchanged.
- **Configuration boundary:** this is a Make/ambient opt-in, not an `.env` profile entry; Lite does
  not import `ALLOY_STT_HEALTHCHECK` from `ENV_FILE`.

## Approved local pilot profile (explicit overrides)

These are the approved values for the next local pilot start and build. They are explicit
overrides, not evidence that the currently running image contains the final source:

```env
ALLOY_STT_MAX_CONCURRENCY=1
ALLOY_STT_CHANNEL_BACKPRESSURE=1
ALLOY_STT_LANGUAGE_MODE=auto
ALLOY_STT_TELEMETRY=1
NEXT_PUBLIC_ALLOY_HIDE_EMPTY_ROOM_COUNT=1
ALLOY_SKIP_HF_CACHE_WARM=1
ALLOY_LITE_BUNDLED_PYTHON=1
```

Restore the upstream-compatible runtime path and restart Lite so new bot processes inherit:

```env
ALLOY_STT_MAX_CONCURRENCY=0
ALLOY_STT_CHANNEL_BACKPRESSURE=0
ALLOY_STT_LANGUAGE_MODE=configured
ALLOY_STT_TELEMETRY=0
```

Restore the upstream-compatible build path and rebuild the Lite image with:

```env
NEXT_PUBLIC_ALLOY_HIDE_EMPTY_ROOM_COUNT=0
ALLOY_SKIP_HF_CACHE_WARM=0
ALLOY_LITE_BUNDLED_PYTHON=0
```

## Evidence status

Focused source, contract, configuration, architecture, and component checks cover the implemented
opt-in boundaries, real slot lifecycle accounting, active-turn preservation, strict owner-only
lookup, sealed server aggregation, visibility-aware Terminal polling, and the Lite bundled-Python
build-selection contract.

Still pending, and therefore not claimed here:

- the explicit integration lanes against a disposable Redis database;
- a clean Dockerfile build from the integrated source and proof that the running image matches it;
- a real Google Meet journey through audio, Whisper, Redis, API, and Terminal;
- real Russian, English, and code-switch transcription;
- queue, lag, RTF, and recovery observations under real load.
