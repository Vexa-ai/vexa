# Core Concepts

This page explains how Vexa models meetings, transcripts, and recordings, and how timestamps line up for post-meeting playback.

## The Main Entities

### User and API key

Every API request is authenticated with `X-API-Key`. In self-hosted deployments, you create users and mint tokens via the admin API:

- [`self-hosted-management.md`](self-hosted-management.md)

### Meeting

A **meeting** is the top-level record created when you request a bot:

- `platform`: `google_meet` | `teams` | `zoom`
- `native_meeting_id`: what you extracted from the meeting URL

Meetings persist after completion. If you delete a meeting, Vexa anonymizes it (for usage tracking) and purges artifacts best-effort.

### Bot session (`session_uid`)

Each bot run has a **session UID** (think: one attempt / one join). It links:

- transcript segments
- recordings and media files

This is how UIs align transcript playback to the correct audio capture timeline.

## Transcript Segments and Timing

Transcripts are stored as **segments**. Each segment has:

- `text` (the transcript string)
- `speaker` (best-effort attribution)
- `start_time` / `end_time` (seconds)

### What `start_time` means

`start_time` and `end_time` are **relative seconds from the start of the bot's audio capture pipeline**.

This is why post-meeting playback can be aligned without additional offset math:

- audio player time (`currentTime`) maps directly to segment `start_time`/`end_time`

### Word-level timestamps

As of v0.8, Vexa focuses on **segment-level** timing. Word-level timing is not exposed in the API, so UIs should highlight segments, not individual words.

## Recording vs Capture (important)

Vexa always needs to **capture audio** to transcribe.

**Recording persistence** is separate and controlled by flags:

- `recording_enabled`: whether to persist recording artifacts (audio files) to storage
- `transcribe_enabled`: whether to run/store transcription output

You can record without transcribing, transcribe without recording, or do both.

## Transcription Tiers

Vexa supports a `transcription_tier` per meeting:

- `realtime` (default): best effort low latency
- `deferred`: lower priority; useful for cost/throughput control

## Meeting Lifecycle States (high-level)

Common states you will see in logs/UI:

- `joining`: bot launched and navigating to meeting
- `awaiting_admission`: waiting room / lobby (host needs to admit)
- `active`: bot is in the meeting
- `stopped`: stop requested
- `completed`: finished processing (post-meeting artifacts should become available)

Exact naming can differ between internal services, but the above is the mental model.

## Recordings and Media Files

If `recording_enabled=true` and Vexa captured audio, post-meeting APIs include `recordings` with `media_files`.

Typical audio flow:

1. Call `GET /transcripts/{platform}/{native_meeting_id}`
2. Read:
   - `recordings[0].id`
   - `recordings[0].media_files[0].id`
3. Stream bytes:
   - `GET /recordings/{recording_id}/media/{media_file_id}/raw`

The `/raw` endpoint is designed for browser playback:

- `Content-Disposition: inline`
- `Range` requests (`206 Partial Content`) for seeking

Storage configuration:

- [`recording-storage.md`](recording-storage.md)

## Voice Agent (Meeting Interaction)

When `voice_agent_enabled: true` is passed at bot creation, the bot becomes an interactive meeting participant. An external agent can instruct it to:

- **Speak** via text-to-speech (OpenAI TTS → PulseAudio → meeting mic)
- **Read/write chat** messages in the meeting
- **Share screen** content (images, URLs, video)

The voice agent uses the same session model: all interactions happen within a single `session_uid`. Chat messages captured by the bot can optionally be injected into the transcription stream.

Full details: [Voice Agent Guide](voice-agent.md)

## Delete Semantics

Deleting a meeting is designed to be deliberate: it purges transcript artifacts and recording objects (best-effort), then anonymizes the meeting record for telemetry/usage tracking.

API details:

- [`user_api_guide.md`](user_api_guide.md)

