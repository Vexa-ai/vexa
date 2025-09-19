## Vexa Bot — Platform‑agnostic Meeting Bot

This document describes the system design and implementation of the platform‑agnostic meeting bot used to join and transcribe calls across providers (Microsoft Teams, Google Meet, etc.). It reflects the current architecture after the refactor to unify orchestration and reduce duplication.

### Goals
- Provide a shared, robust meeting lifecycle across platforms
- Keep platform files concise by extracting low‑level logic into modular functions
- Centralize cross‑platform flow and error handling
- Make adding new platforms straightforward (selectors + a few strategy functions)

## High‑level Architecture

- Node‑side flow controller: `runMeetingFlow()` in `platforms/shared/meetingFlow.ts`
  - Coordinates join → prepare → admission wait → startup → recording → monitoring → leave
  - Handles stop‑signal guard, admission outcomes, reason propagation, and graceful leave
  - Races recording against a platform’s removal monitor
- Platform strategy modules (per provider): `join.ts`, `admission.ts`, `recording.ts`, `leave.ts`, `removal.ts`, `selectors.ts`
  - Each platform exports a handler entrypoint (`googlemeet/index.ts`, `msteams/index.ts`) that wires strategies into `runMeetingFlow`
- Browser‑side helpers (injected bundle): `browser-utils.global.js` (built into `dist/`)
  - `BrowserAudioService`: captures and mixes media stream(s)
  - `BrowserWhisperLiveService`: WebSocket to WhisperLive with stubborn reconnection
  - Exposes `window.performLeaveAction()` for platform‑specific leave UX

## Repository Structure (core)

```text
core/
  dist/                         Built bundle for browser helpers
  src/
    index.ts                    Bot entry (imports platform handlers)
    platforms/
      shared/
        meetingFlow.ts          Shared cross‑platform flow
      googlemeet/
        index.ts                Google Meet handler (strategies wiring)
        join.ts
        admission.ts
        recording.ts
        leave.ts
        removal.ts
        selectors.ts
      msteams/
        index.ts                Teams handler (canonical behaviors)
        join.ts
        admission.ts
        recording.ts
        leave.ts
        removal.ts
        selectors.ts
      hot-debug.sh              Unified hot‑reload debug runner (google|teams)
```

## Cross‑platform Flow (runMeetingFlow)

1) Join: platform `join()` navigates and performs pre‑join steps (name, mic/cam)
2) Stop‑signal guard: exit early if a global stop was requested
3) Prepare + Admission (in parallel):
   - `prepare()` exposes `window.performLeaveAction()` and loads browser utils
   - `waitForAdmission()` resolves admitted or returns structured timeouts/rejection
4) Startup: `callStartupCallback()` once admitted
5) Recording + Removal Race:
   - `startRecording()` begins streaming audio and events to WhisperLive
   - `startRemovalMonitor()` signals if the bot is removed; flow races and exits correctly
6) Leave/Teardown: always call provided `gracefulLeaveFunction()` with reason

### Exit Reasons
- admission_rejected_by_admin
- admission_timeout
- removed_by_admin
- left_alone_timeout
- startup_alone_timeout
- normal_completion

Reason tokens used by recording/monitors are derived automatically from the platform name (e.g., `TEAMS_BOT_REMOVED_BY_ADMIN`).

## Platform Strategy API

Each platform’s entrypoint provides the strategies to `runMeetingFlow`:

```ts
type PlatformStrategies = {
  join: (page, botConfig) => Promise<void>;
  waitForAdmission: (page, timeoutMs, botConfig) => Promise<boolean | { admitted: boolean; rejected?: boolean; reason?: string }>;
  prepare: (page, botConfig) => Promise<void>;
  startRecording: (page, botConfig) => Promise<void>;
  startRemovalMonitor: (page, onRemoval?: () => void | Promise<void>) => () => void; // returns stop function
  leave: (page | null, botConfig?, reason?) => Promise<boolean>;
};
```

Google Meet and Teams export their handlers as thin wires to the shared flow:

```ts
// Example (googlemeet/index.ts)
await runMeetingFlow("google_meet", botConfig, page, gracefulLeave, {
  join: async (page, cfg) => joinGoogleMeeting(page, cfg.meetingUrl!, cfg.botName, cfg),
  waitForAdmission: waitForGoogleMeetingAdmission,
  prepare: prepareForRecording,
  startRecording: startGoogleRecording,
  startRemovalMonitor: startGoogleRemovalMonitor,
  leave: leaveGoogleMeet,
});
```

## WhisperLive Integration
- Uses `BrowserWhisperLiveService` in the browser with NEVER‑GIVE‑UP reconnection (“stubborn mode”)
- Sends initial config with platform, connectionId, meeting URL/ID
- Gates audio until server signals ready; supports diagnostics logs

## Removal Monitoring
- In‑page heuristics (selectors/signals) in `recording.ts`
- Node‑side periodic checks in `removal.ts` per platform
- Shared flow races recording against a removal promise; errors propagate into reasons

## Redis Control & Callbacks
- Subscribes to `bot_commands:<connectionId>` (e.g., `bot_commands:google-hot-debug`)
- Supports `{"action":"leave"}` to trigger graceful shutdown
- Lifecycle callbacks: `callJoiningCallback`, `callAwaitingAdmissionCallback`, `callStartupCallback`, `callLeaveCallback`

## Configuration (BotConfig)
- Key fields: `platform`, `meetingUrl`, `botName`, `connectionId`, `redisUrl`, `automaticLeave` timeouts
- Environment: `WHISPER_LIVE_URL`, `WL_MAX_CLIENTS`

## Development & Debugging

Hot‑reload debug runner (unified):

```bash
cd vexa/services/vexa-bot/core/src/platforms
./hot-debug.sh google https://meet.google.com/abc-def-ghi
./hot-debug.sh teams  https://teams.live.com/meet/123456789
```

Notes:
- The script rebuilds `core/dist` automatically and runs the container with bind‑mounted `dist/`
- Screenshots saved to `/home/dima/dev/bot-storage/screenshots/run-<timestamp>`
- Sends a Redis leave command automatically to test graceful shutdown

Build only:

```bash
cd vexa/services/vexa-bot/core
npm run build
```

## Adding a New Platform (Checklist)
1) Create `platforms/<provider>/` with: `index.ts`, `join.ts`, `admission.ts`, `recording.ts`, `leave.ts`, `removal.ts`, `selectors.ts`
2) Implement strategy functions to match Teams’ canonical behavior
3) Wire strategies in `<provider>/index.ts` and call `runMeetingFlow("<provider>", ...)`
4) Add selectors and browser helpers as needed
5) Test with `hot-debug.sh <provider>`

## Design Principles Recap
- Platform‑agnostic flow owned by `shared/meetingFlow.ts`
- Platform files are concise, high‑level, and strategy‑based
- Selectors and DOM specifics are platform‑only
- All exits are reasoned and graceful; removal is handled deterministically
- Strong logging and reconnection for reliable operations

# Vexa Bot 

## Meet Bot CLI Tool  (Development, Testing)

## Install dependencies
Install Dependencies
### For Core
1.Navigate to the core directory and run:
```bash
npm install
```
2. Build the core:
```bash
npm run build
```

### For CLI
3. Navigate to the cli directory and run
```bash
npm install
```
4. Create a config file in JSON format (e.g., configs/meet-bot.json) like this:
```json
{
  "platform": "google_meet",
  "meetingUrl": "https://meet.google.com/xxxx",
  "botName": "TestBot",
  "automaticLeave": {
    "waitingRoomTimeout": 300000,
    "noOneJoinedTimeout": 300000,
    "everyoneLeftTimeout": 300000
  }
}
```
4. Run the CLI with:
```bash
npm run cli <config path>
```
example 
```bash
npm run cli configs/meet-bot.json
```
**Note: This is a temporary setup and I will improve it later.**

## How to Run the Bot with Docker for Production

#### 1. Build the Docker Image

Before running the bot, you need to build the Docker image. Navigate to the `core` directory  (where the Dockerfile is located) and run:
```bash
docker build -t vexa-bot .
```
This command will create a Docker image named vexa-bot.
#### 2. Run the Bot Container

Once the image is built, you can start the bot using Docker. Pass the bot configuration as an environment variable:
```bash
docker run -e BOT_CONFIG='{"platform": "google_meet", "meetingUrl": "https://meet.google.com/xcb-tssj-qjc", "botName": "Vexa", "token": "123", "connectionId": "", "automaticLeave": {"waitingRoomTimeout": 300000, "noOneJoinedTimeout": 300000, "everyoneLeftTimeout": 300000}}' vexa-bot
```
##### Notes:

- Ensure the BOT_CONFIG JSON is properly formatted and wrapped in single quotes (') to avoid issues.

- The bot will launch inside the Docker container and join the specified meeting.

- You can replace the values in BOT_CONFIG to customize the bot's behavior.
