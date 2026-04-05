# Agent Test: TTS Service

## Prerequisites
- Services running: tts-service (Docker)
- Environment: OPENAI_API_KEY set, optionally TTS_API_TOKEN
- Setup: `docker compose up -d tts-service`

## Tests

### Test 1: Basic TTS Synthesis
**Goal:** Verify text-to-speech produces audio output.
**Setup:** POST to `/v1/audio/speech` with `{"model": "tts-1", "input": "Hello world", "voice": "nova", "response_format": "pcm"}`.
**Verify:** Response is 200 with `audio/pcm` content type. Response body is non-empty binary data.
**Pass criteria:** Audio bytes received, at least 1KB of PCM data for a short phrase.

### Test 2: All Voice Options
**Goal:** Verify all 6 voices produce output.
**Setup:** Send separate requests for each voice: alloy, echo, fable, onyx, nova, shimmer.
**Verify:** All return 200 with audio data.
**Pass criteria:** 6/6 voices succeed.

### Test 3: Response Format Variants
**Goal:** Verify different audio formats (pcm, mp3, wav) work.
**Setup:** Send requests with each response_format.
**Verify:** Correct Content-Type header for each format.
**Pass criteria:** At least pcm and mp3 return valid audio.

### Test 4: API Key Enforcement
**Goal:** Verify API key auth works when TTS_API_TOKEN is configured.
**Setup:** Set TTS_API_TOKEN env var. Send request without key, with wrong key, with correct key.
**Verify:** No key -> 401. Wrong key -> 401. Correct key -> 200.
**Pass criteria:** All three cases handled correctly.
