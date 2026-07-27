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

- **Purpose:** apply backpressure before an OpenAI-compatible STT dependency so independent
  participant lanes cannot flood a single CPU Whisper worker.
- **Local Lite value:** `1`.
- **Enabled behavior:** at most the configured number of STT HTTP requests execute concurrently;
  participant buffers continue accumulating audio while waiting.
- **Disabled/upstream-compatible behavior:** unset, empty, or `0`; requests are not limited by the
  Alloy adapter.
- **Scope:** STT request scheduling only. It does not change capture, speaker lanes, transcript
  contracts, or the selected Whisper model.

### `ALLOY_STT_LANGUAGE_MODE`

- **Purpose:** allow multilingual and code-switching meetings without pinning one language for the
  whole session.
- **Local Lite value:** `auto`.
- **Enabled behavior (`auto`):** no language is sent to Whisper; the model detects the language for
  each submitted audio window, including Russian/English switching.
- **Disabled/upstream-compatible behavior (`configured`, unset, or empty):** Vexa forwards the
  invocation-configured language when one exists.
- **Scope:** language selection at the STT boundary only. It does not change transcript formatting
  or speaker attribution.

### `ALLOY_STT_CHANNEL_BACKPRESSURE`

- **Purpose:** prevent successive turns of one physical Meet audio channel from creating an
  unbounded queue while CPU Whisper is still processing an earlier turn.
- **Local Lite value:** `1`.
- **Enabled behavior:** each `ch-N` has one active STT request and one replaceable latest pending
  request. A newer pending turn supersedes an older one before it reaches Whisper.
- **Disabled/upstream-compatible behavior:** unset, empty, or `0`; every turn is dispatched using
  Vexa's original scheduling.
- **Scope:** Google Meet per-channel STT scheduling only. Capture, transcript schemas, and
  cross-channel concurrency remain unchanged.

### `ALLOY_STT_TELEMETRY`

- **Purpose:** make real STT pressure observable instead of inferring it from transcript pauses.
- **Local Lite value:** `1`.
- **Enabled behavior:** each Google Meet bot publishes a short-lived per-meeting snapshot to Redis;
  Meeting API returns only the signed-in owner's active snapshots; Gateway forwards the endpoint;
  Terminal polls once per second while visible and shows global plus per-meeting queue health.
- **Disabled/upstream-compatible behavior:** unset or `0` outside Lite; no tracker, Redis publisher,
  telemetry endpoint data, or active UI polling is created. Normal capture and transcription are
  unchanged.
- **Metrics:** active requests, waiting channels, queued audio seconds, capture-to-processing lag,
  exponential moving-average RTF, processed/superseded windows, freshness, and the last worker
  error.
- **Transport:** Redis keys use `alloy:stt:telemetry:v1:{meeting_id}` with a short TTL. Meeting API
  reads only exact keys for verified owner-scoped running meetings; it never scans Redis.
- **Scope:** diagnostics only. The flag does not alter STT cadence, buffering, transcript text,
  language detection, speaker attribution, or meeting lifecycle.

### `NEXT_PUBLIC_ALLOY_HIDE_EMPTY_ROOM_COUNT`

- **Purpose:** do not present Vexa's placeholder `participants: []` as a real Meet roster count.
- **Local Lite build value:** `1`.
- **Enabled behavior:** hide `0 in the room`; display a positive count if a real roster is added
  later.
- **Disabled/upstream-compatible behavior:** unset, empty, or `0`; preserve Vexa's original label.
- **Scope:** terminal UI only. This flag does not implement participant discovery.
- **Build-time note:** this `NEXT_PUBLIC_*` value is compiled into the terminal bundle and requires
  rebuilding the Lite image.

### `ALLOY_SKIP_HF_CACHE_WARM`

- **Purpose:** keep local Lite rebuilds offline when the mixed-lane pyannote cache is irrelevant to
  the Google Meet-only validation path.
- **Local Lite build value:** `1`.
- **Enabled behavior:** skip `warm-hf-cache.mjs` during the bot-builder stage.
- **Disabled/upstream-compatible behavior:** unset, empty, or `0`; preserve Vexa's original
  best-effort cache warm.
- **Scope:** image build only. Runtime model selection and Google Meet/Whisper behavior are
  unchanged.

## Current local profile

The local Vexa Lite installation uses:

```env
ALLOY_STT_MAX_CONCURRENCY=1
ALLOY_STT_CHANNEL_BACKPRESSURE=1
ALLOY_STT_LANGUAGE_MODE=auto
ALLOY_STT_TELEMETRY=1
NEXT_PUBLIC_ALLOY_HIDE_EMPTY_ROOM_COUNT=1
ALLOY_SKIP_HF_CACHE_WARM=1
```

To restore the upstream-compatible mode without rebuilding:

```env
ALLOY_STT_MAX_CONCURRENCY=0
ALLOY_STT_CHANNEL_BACKPRESSURE=0
ALLOY_STT_LANGUAGE_MODE=configured
ALLOY_STT_TELEMETRY=0
NEXT_PUBLIC_ALLOY_HIDE_EMPTY_ROOM_COUNT=0
ALLOY_SKIP_HF_CACHE_WARM=0
```
