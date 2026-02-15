# Vexa Bot

A meeting bot that joins video conferences on Google Meet, Microsoft Teams, or Zoom, captures audio in real time, and streams it to a [WhisperLive](https://github.com/collabora/WhisperLive) transcription server. It also detects active speakers and reports participant changes throughout the session.

## Table of Contents

- [How It Works](#how-it-works)
- [Supported Platforms](#supported-platforms)
- [Project Structure](#project-structure)
- [Configuration](#configuration)
- [Running with Docker (Production)](#running-with-docker-production)
- [Development](#development)
- [Platform Deep Dive](#platform-deep-dive)
- [Shared Services](#shared-services)
- [Adding a New Platform](#adding-a-new-platform)

---

## How It Works

Every bot instance runs inside a Docker container and follows the same lifecycle, regardless of platform:

```
Join meeting -> Wait for admission -> Start recording -> Stream audio to WhisperLive -> Leave on signal or timeout
```

The lifecycle is orchestrated by a shared flow controller (`runMeetingFlow`) that delegates platform-specific behavior through a strategy pattern. Each platform provides its own implementations for joining, admission detection, audio capture, speaker detection, removal monitoring, and leaving.

A Redis subscriber listens for control-plane commands (`leave`, `reconfigure`) on channel `bot_commands:meeting:<meeting_id>`, allowing external systems to control the bot at runtime.

Status callbacks are sent via HTTP POST to the bot-manager at each lifecycle stage: `joining`, `awaiting_admission`, `active`, `completed`, `failed`.

## Supported Platforms

| Platform | Approach | Browser | Audio Capture | Speaker Detection |
|----------|----------|---------|---------------|-------------------|
| **Google Meet** | Browser automation | Chrome + Stealth Plugin | DOM `<audio>`/`<video>` elements via Web Audio API | MutationObserver on CSS class changes |
| **MS Teams** | Browser automation | MS Edge (required) | RTCPeerConnection hook intercepts WebRTC audio tracks | Voice-level outline element + CSS class detection |
| **Zoom** | Native SDK | None | SDK raw audio callback (fallback: PulseAudio capture) | SDK `onActiveSpeakerChange` callback |

## Zoom SDK Licensing and Setup

Zoom Meeting SDK binaries are not auto-downloaded by this repo. You must obtain them from Zoom and place them under:

`core/src/platforms/zoom/native/zoom_meeting_sdk/`

Important:

- Zoom Meeting SDK is proprietary (not open source). We cannot redistribute SDK binaries in this open-source repository.
- Because of Zoom SDK license restrictions, binaries must be downloaded directly from Zoom by each user/team.
- Keep `libmeetingsdk.so`, `qt_libs/`, and other SDK binaries out of git (already enforced in `.gitignore`).
- Each team/user should download the SDK from Zoom directly and accept Zoom's current API/SDK terms.
- Native build may skip when SDK binaries are missing, but runtime now fails fast for Zoom with a clear "Zoom SDK native addon is not available" error.

Quick check:

```bash
ls core/src/platforms/zoom/native/zoom_meeting_sdk/libmeetingsdk.so
```

## Project Structure

```
vexa-bot/
  binding.gyp                         # Native addon build config (Zoom C++ wrapper)
  package.json                        # Root workspace (workspaces: core, cli)
  Dockerfile                          # Production 3-stage build (native + TS + runtime)
  Makefile                            # Hot dev kit (build, rebuild, test, publish)
  run-zoom-bot.sh                     # Convenience script to launch a Zoom bot in Docker

  core/
    package.json                      # Core dependencies (playwright, redis, zod, etc.)
    tsconfig.json
    build-browser-utils.js            # Bundles browser-side services into a single IIFE
    Dockerfile                        # Simplified build (TS only, no native addon)
    entrypoint.sh                     # Container entrypoint (Xvfb, PulseAudio, null sink)

    src/
      index.ts                        # runBot() — main orchestrator
      docker.ts                       # Docker entry point — reads BOT_CONFIG env var
      types.ts                        # BotConfig type definition
      constans.ts                     # Browser args, user-agent string

      platforms/
        shared/
          meetingFlow.ts              # Strategy-pattern flow controller (all platforms use this)

        googlemeet/
          index.ts                    # Wires Google Meet strategies into runMeetingFlow
          join.ts                     # Navigate, fill name, mute, click "Ask to join"
          admission.ts                # Multi-indicator check (requires 2+ signals)
          recording.ts               # Audio capture + speaker detection via CSS classes
          leave.ts                    # Click "Leave call" button
          removal.ts                  # DOM text scanning for "Meeting ended", etc.
          selectors.ts                # All CSS/aria selectors for Google Meet UI

        msteams/
          index.ts                    # Wires Teams strategies into runMeetingFlow
          join.ts                     # Multi-step join + RTCPeerConnection audio hook
          admission.ts                # Hangup button visibility as admission signal
          recording.ts               # Audio capture + voice-level speaker detection
          leave.ts                    # Click hangup button
          removal.ts                  # DOM scanning for "you've been removed"
          selectors.ts                # All CSS/aria selectors for Teams UI

        zoom/
          index.ts                    # Wires Zoom strategies into runMeetingFlow
          sdk-manager.ts              # Loads native C++ addon, JWT auth, meeting management
          strategies/
            join.ts                   # SDK initialize -> authenticate -> joinMeeting
            admission.ts              # No-op (SDK handles admission internally)
            prepare.ts                # No-op (no browser to prepare)
            recording.ts             # SDK raw audio or PulseAudio fallback
            leave.ts                  # SDK leaveMeeting() + cleanup
            removal.ts               # SDK onMeetingStatus event monitoring
          native/
            src/zoom_wrapper.cpp      # N-API C++ wrapper around Zoom Meeting SDK
            zoom_meeting_sdk/         # Zoom SDK binaries, headers, Qt libs

      services/
        index.ts                      # Service exports
        audio.ts                      # Node-side AudioService (DOM media element capture)
        whisperlive.ts                # WebSocket client to WhisperLive server
        unified-callback.ts           # HTTP callbacks to bot-manager

      utils/
        index.ts                      # Utility exports
        browser.ts                    # Browser-side BrowserAudioService + BrowserWhisperLiveService
        injection.ts                  # Injects bundled browser utils into page context
        websocket.ts                  # General-purpose WebSocket manager with reconnection
```

## Configuration

The bot is configured via a `BOT_CONFIG` JSON string passed as an environment variable. It is validated at startup using Zod.

### BotConfig Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `platform` | `"google_meet"` \| `"zoom"` \| `"teams"` | Yes | Target platform |
| `meetingUrl` | `string \| null` | Yes | Full meeting URL |
| `botName` | `string` | Yes | Display name for the bot in the meeting |
| `token` | `string` | Yes | HS256 JWT for authentication |
| `connectionId` | `string` | Yes | Unique connection identifier |
| `nativeMeetingId` | `string` | Yes | Platform-native meeting ID |
| `meeting_id` | `number` | Yes | Internal meeting ID (used for Redis channel) |
| `redisUrl` | `string` | Yes | Redis connection URL |
| `automaticLeave.waitingRoomTimeout` | `number` | Yes | Max ms to wait in waiting room/lobby |
| `automaticLeave.noOneJoinedTimeout` | `number` | Yes | Max ms to wait if no one else joins |
| `automaticLeave.everyoneLeftTimeout` | `number` | Yes | Ms to wait after all participants leave |
| `language` | `string` | No | Transcription language (e.g. `"en"`) |
| `task` | `string` | No | Transcription task type |
| `container_name` | `string` | No | Docker container name |
| `reconnectionIntervalMs` | `number` | No | WebSocket reconnection interval |
| `botManagerCallbackUrl` | `string` | No | URL for lifecycle status callbacks |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `BOT_CONFIG` | JSON string with the full bot configuration (see above) |
| `WHISPER_LIVE_URL` | WebSocket URL of the WhisperLive server (e.g. `ws://whisperlive:9090/ws`) |
| `WL_MAX_CLIENTS` | Max WhisperLive client connections |
| `ZOOM_CLIENT_ID` | Zoom SDK client ID (Zoom platform only) |
| `ZOOM_CLIENT_SECRET` | Zoom SDK client secret (Zoom platform only) |

## Running with Docker (Production)

### 1. Build the Docker image

From the `vexa-bot/` root directory:

```bash
docker build -t vexa-bot .
```

This runs a 3-stage build:
1. **Native builder** — compiles the Zoom C++ addon via `node-gyp` (skips gracefully if SDK is absent)
2. **TypeScript builder** — compiles TS, bundles browser utils, installs Playwright browsers and MS Edge
3. **Runtime** — minimal image with Xvfb, PulseAudio, and all built artifacts

### 2. Run the bot

```bash
docker run --rm \
  --platform linux/amd64 \
  --network your_docker_network \
  -e BOT_CONFIG='{
    "platform": "google_meet",
    "meetingUrl": "https://meet.google.com/abc-defg-hij",
    "botName": "Vexa",
    "token": "your-jwt-token",
    "connectionId": "unique-connection-id",
    "nativeMeetingId": "abc-defg-hij",
    "meeting_id": 1,
    "redisUrl": "redis://redis:6379/0",
    "automaticLeave": {
      "waitingRoomTimeout": 300000,
      "noOneJoinedTimeout": 300000,
      "everyoneLeftTimeout": 300000
    }
  }' \
  -e WHISPER_LIVE_URL=ws://whisperlive:9090/ws \
  vexa-bot
```

### Zoom shortcut

Use the provided convenience script:

```bash
export ZOOM_MEETING_URL="https://us05web.zoom.us/j/123456789?pwd=xxx"
export ZOOM_CLIENT_ID="your-client-id"
export ZOOM_CLIENT_SECRET="your-client-secret"
./run-zoom-bot.sh
```

### What the container does at startup

The `entrypoint.sh` script:
1. Sets `LD_LIBRARY_PATH` for Zoom SDK shared libraries (if present)
2. Starts **Xvfb** (virtual framebuffer at 1920x1080) on display `:99`
3. Starts **PulseAudio** and creates a null sink (`zoom_sink`) for Zoom audio capture
4. Configures ALSA to route through PulseAudio
5. Ensures the browser-utils bundle exists
6. Runs `node dist/docker.js`

## Development

### Prerequisites

- Docker
- A running Vexa stack (Redis, optionally WhisperLive) on the `vexa_dev_vexa_default` Docker network

### Hot Dev Kit (Makefile)

The Makefile provides a fast development loop without needing local Node.js or dependencies:

```bash
cd vexa-bot/

# One-time setup: build Docker image + create local dist/ for hot-reload
make build

# Run a bot against a meeting URL (auto-detects platform from URL)
make test MEETING_URL='https://meet.google.com/abc-defg-hij'
make test MEETING_URL='https://teams.live.com/meet/123456?p=xxx'

# After editing TypeScript files, rebuild dist/ (~10s, no Docker image rebuild)
make rebuild

# Then restart the bot (Ctrl+C the running one, re-run make test)

# Send a graceful leave command via Redis
make publish-leave

# Send a custom Redis command
make publish DATA='{"action":"reconfigure","language":"es"}'
```

**Why this is fast:** `make rebuild` only recompiles TypeScript to JavaScript (~10 seconds). The bot container bind-mounts `dist/`, so it picks up changes without rebuilding the entire Docker image. Only run `make build` again when changing dependencies.

### Building core manually

```bash
cd core/
npm install
npm run build          # tsc + bundle browser utils
npm run build-browser  # rebuild browser utils bundle only
```

### Building the native addon (Zoom)

From the repo root:

```bash
npm install
npm run build:native   # runs node-gyp rebuild
```

Requires: `build-essential`, `cmake`, `python3`, `libssl-dev`, `qtbase5-dev` (Linux only).

## Platform Deep Dive

### Google Meet

**Browser:** Chromium via Playwright with [puppeteer-extra-plugin-stealth](https://github.com/nicedream/puppeteer-extra-plugin-stealth) to evade bot detection (webdriver flag, plugins, languages, etc.).

**Join flow:**
1. Navigate to the meeting URL
2. Wait for the name input field, fill in the bot name
3. Mute microphone and camera using aria-label selectors
4. Click "Ask to join"

**Audio capture:** Finds `<audio>` and `<video>` DOM elements with active `MediaStream` sources, combines them into a single stream via `AudioContext` + `MediaStreamAudioDestinationNode`, processes through a `ScriptProcessorNode`, resamples to 16kHz, and sends to WhisperLive.

**Speaker detection:** A `MutationObserver` watches `[data-participant-id]` elements for CSS class changes. Google Meet uses obfuscated class names to indicate speaking state. A 500ms polling fallback handles cases where mutations are missed.

**Admission:** Checks multiple indicators simultaneously (People button, Chat button, Leave button, toolbar, `data-participant-id` attributes, mic controls). Requires at least 2 indicators to confirm admission, preventing false positives.

**Removal:** Scans the DOM every 1.5 seconds for text like "Meeting ended", "Call ended", "You left the meeting", and alert roles.

---

### Microsoft Teams

**Browser:** MS Edge via Playwright (required by Teams). Uses `bypassCSP: true` and pre-injects browser utils via `context.addInitScript()`.

**Join flow:** Multi-step process:
1. Navigate to the meeting URL
2. Click "Continue on this browser"
3. Turn off camera, set display name
4. Select "Computer audio"
5. Click "Join now"

**Audio capture:** Before navigating, an `addInitScript` monkey-patches `RTCPeerConnection` to intercept `ontrack` events. When remote audio tracks arrive via WebRTC, hidden `<audio>` elements are created and appended to the DOM. Without this hook, Teams audio is not accessible through DOM elements. The rest of the pipeline is the same as Google Meet.

**Speaker detection:** Monitors `[data-tid="voice-level-stream-outline"]` elements for the `vdi-frame-occlusion` CSS class. Implements a full `ParticipantRegistry` and `SpeakerStateMachine` with 200ms debounce. Uses both `MutationObserver` and `requestAnimationFrame`-based polling.

**Admission:** Checks for the hangup button (`#hangup-button`, `data-tid="hangup-main-btn"`) as the primary admission indicator. Detects waiting room via "Someone will let you in shortly" text.

**Removal:** Scans for "you've been removed" text and "Rejoin"/"Dismiss" buttons every 1.5 seconds.

---

### Zoom

**Approach:** Completely different from the browser-based platforms. Zoom uses a **native C++ addon** (`zoom_wrapper.cpp`) that wraps the official Zoom Meeting SDK via N-API. No browser is involved.

**Native addon (`zoom_wrapper.cpp`):**
- Wraps the Zoom Meeting SDK C++ API as a Node.js native addon
- Integrates Qt's event loop with Node.js's libuv event loop (the SDK depends on Qt internally) by registering a `uv_idle_t` callback that pumps Qt events
- Uses `Napi::ThreadSafeFunction` to safely marshal SDK callbacks from background threads to the JS main thread
- Provides: `initialize`, `authenticate` (JWT), `joinMeeting`, `joinAudio`, `startRecording`, `stopRecording`, `getUserInfo`, `leaveMeeting`, `cleanup`

**Join flow:**
1. Initialize the Zoom SDK
2. Authenticate with a generated HMAC-SHA256 JWT (from `ZOOM_CLIENT_ID` / `ZOOM_CLIENT_SECRET`)
3. Call `meetingService->Join()` with meeting number, display name, password

**Audio capture:**
- **Primary:** SDK's `onMixedAudioRawDataReceived` callback provides PCM Int16 buffers at 32kHz mono. The addon copies the buffer and sends it to JS, which converts to Float32 and forwards to WhisperLive.
- **Fallback:** If the SDK returns `NO_PERMISSION` (raw audio data requires a special license), falls back to **PulseAudio capture** using `parecord --device=zoom_sink.monitor`. The container's entrypoint creates a null audio sink that the SDK outputs to.

**Speaker detection:** The SDK's `onActiveSpeakerChange` callback provides an array of active user IDs. The JS layer diffs against the previous set to emit `SPEAKER_START` / `SPEAKER_END` events, resolving user IDs to names via `getUserInfo()`.

**Admission:** Always returns `true` since the SDK handles admission during the join call itself.

**Removal:** Monitors SDK meeting status events. Triggers removal on `ended`, `failed`, or `removed` statuses.

## Shared Services

### Meeting Flow Controller (`platforms/shared/meetingFlow.ts`)

The core orchestrator that all platforms plug into:

1. **Join** — platform-specific navigation / SDK join
2. **Stop-signal guard** — bail if a Redis `leave` command was received during join
3. **Admission + Prepare (parallel)** — wait for admission while setting up browser functions
4. **Startup callback** — notify bot-manager the bot is `active`
5. **Recording + Removal race** — `Promise.race` between the recording loop and the removal monitor
6. **Graceful leave** — teardown with a structured reason

### WhisperLive Client (`services/whisperlive.ts`)

WebSocket client that streams audio and events to the transcription server:
- Sends initial config (uid, language, task, platform, token, meeting_id)
- `sendAudioData()` — raw Float32 audio frames
- `sendAudioChunkMetadata()` — timing metadata for each chunk
- `sendSpeakerEvent()` — `SPEAKER_START`/`SPEAKER_END` with participant name, ID, and relative timestamp
- `sendSessionControl()` — lifecycle events like `LEAVING_MEETING`
- Stubborn reconnection with exponential backoff (never gives up)

### Browser Utils Bundle (`utils/browser.ts` -> `browser-utils.global.js`)

A bundled IIFE injected into the browser page context (Google Meet and Teams only):
- `BrowserAudioService` — finds media elements, combines streams, processes and resamples audio
- `BrowserWhisperLiveService` — browser-side WebSocket to WhisperLive with stubborn reconnection
- Injected via `utils/injection.ts` using multiple strategies (script tag, Trusted Types policy, Blob URL fallback)

### Unified Callback (`services/unified-callback.ts`)

HTTP POST to the bot-manager with status updates. Retries up to 3 times with exponential backoff.

Statuses: `joining` -> `awaiting_admission` -> `active` -> `completed` or `failed`

Completion reasons: `stopped`, `awaiting_admission_timeout`, `awaiting_admission_rejected`, `left_alone`, `evicted`

### Redis Control

Subscribes to `bot_commands:meeting:<meeting_id>` for runtime commands:
- `{"action": "leave"}` — trigger graceful shutdown
- `{"action": "reconfigure", "language": "es", "task": "..."}` — change transcription settings without restarting

## Adding a New Platform

1. Create `platforms/<provider>/` with strategy files: `index.ts`, `join.ts`, `admission.ts`, `recording.ts`, `leave.ts`, `removal.ts`, `selectors.ts`
2. Implement the `PlatformStrategies` interface:

```ts
type PlatformStrategies = {
  join: (page, botConfig) => Promise<void>;
  waitForAdmission: (page, timeoutMs, botConfig) => Promise<AdmissionResult>;
  checkAdmissionSilent: (page) => Promise<boolean>;
  prepare: (page, botConfig) => Promise<void>;
  startRecording: (page, botConfig) => Promise<void>;
  startRemovalMonitor: (page, onRemoval?) => () => void;
  leave: (page, botConfig?, reason?) => Promise<boolean>;
};
```

3. Wire strategies in `<provider>/index.ts` and call `runMeetingFlow("<provider>", ...)`
4. Add platform-specific selectors and browser helpers as needed
5. Test with the hot-debug workflow: `make test MEETING_URL='...'`

## Exit Reasons

The bot always exits with a structured reason, derived from the platform name:

| Reason | When |
|--------|------|
| `*_ADMISSION_REJECTED` | Host denied entry |
| `*_ADMISSION_TIMEOUT` | Timed out waiting in lobby |
| `*_BOT_REMOVED_BY_ADMIN` | Removed from meeting by host |
| `*_BOT_LEFT_ALONE_TIMEOUT` | All participants left, timeout expired |
| `*_STARTUP_ALONE_TIMEOUT` | No one joined after bot entered |
| `*_NORMAL_COMPLETION` | Graceful leave via Redis command |

(Prefix is the platform name, e.g. `GOOGLE_MEET_BOT_REMOVED_BY_ADMIN`, `TEAMS_BOT_LEFT_ALONE_TIMEOUT`)
