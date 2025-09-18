### Platforms PRD: Platform‑agnostic Meeting Bot Design (Teams as Canonical)

#### Context and Goals
- **Goal**: Provide a platform‑agnostic design for meeting bots under `platforms/` so that each provider (Teams, Google Meet, etc.) shares the same lifecycle, contracts, and behaviors while differing only in DOM selectors and UI specifics.
- **Canonical reference**: `msteams/teams.ts` implements the platform‑agnostic design fully; new platforms should conform to this behavior unless explicitly stated.
- **Scope**: Bot join/admission, audio capture and streaming to WhisperLive, speaker events, participant monitoring, removal/exit handling, and graceful teardown.
- **Non‑goals**: Authentication/OAuth, scheduling/orchestration outside this process, storage schema, model selection.

#### High‑level Architecture
- **Node‑side orchestrator (Playwright Page context owner)**
  - Navigates to `botConfig.meetingUrl`, performs pre‑join steps, and coordinates admission wait + recording preparation concurrently.
  - Calls centralized callbacks: `callJoiningCallback`, `callAwaitingAdmissionCallback`, `callStartupCallback`, `callLeaveCallback` from `../../utils`.
  - Starts/stops recording by injecting `browser-utils.global.js` and evaluating browser‑side logic.
  - Performs Node‑side safety loops (e.g., periodic removal checks in Teams) and always finalizes via the provided `gracefulLeaveFunction`.

- **Browser‑side helpers (injected bundle)**
  - Provided by `core/src/browser-utils.global.js` (bundled as `window.VexaBrowserUtils`).
  - Key classes used inside the page:
    - `BrowserAudioService`: discovers `<audio>/<video>` media, creates a combined stream, downmixes to target sample rate, and feeds a processor.
    - `BrowserWhisperLiveService`: manages WebSocket connection to WhisperLive, gates audio until server ready, supports failover modes (simple/stubborn).
  - Exposes a standard `window.performLeaveAction()` function to trigger platform‑specific leave UX from Node.

- **Services / Utils (Node)**
  - `WhisperLiveService` (`../../services/whisperlive`): server‑side session init and URL resolution for the browser client.
  - `AudioService` (`../../services/audio`): Node‑side configuration references (browser capture is done with BrowserAudioService).
  - `WebSocketManager` (`../../utils/websocket`): reconnection / backoff helper leveraged by BrowserWhisperLiveService implementations.
  - `utils` (`../../utils`): logging and bot lifecycle callbacks.
  - `types` (`../../types`): `BotConfig` and related configuration types.

#### Cross‑platform Lifecycle (canonical from Teams)
1) Navigate and pre‑join
   - Go to `botConfig.meetingUrl`, optionally set display name, toggle camera/microphone.
   - Send `callJoiningCallback(botConfig)`.

2) Prepare for recording (in parallel with admission wait)
   - Expose `window.logBot` and `window.performLeaveAction`.
   - Load `browser-utils.global.js` to provide `BrowserAudioService` and `BrowserWhisperLiveService`.
   - Prepare selectors/config for platform logic if needed.

3) Admission wait (platform‑agnostic contract)
   - Check for immediate admission via reliable UI indicators (e.g., a Leave button/control present and enabled) plus negative checks (not in lobby/pre‑join).
   - If in waiting room/lobby: send `callAwaitingAdmissionCallback(botConfig)`, then poll until timeout.
   - When lobby indicators disappear, first check for explicit rejection; if not rejected, confirm admission indicators, otherwise conservatively assume admitted if waiting room is gone and no rejection is found.
   - Distinguish outcomes: admitted | rejected_by_admin | admission_timeout. On timeout, attempt stateless leave before exiting.

4) Startup notification
   - On admission, send `callStartupCallback(botConfig)`.

5) Recording pipeline (browser)
   - Find active media elements; create combined audio stream and initialize the audio processor.
   - Connect to WhisperLive via `BrowserWhisperLiveService`, gate audio sending until `SERVER_READY`, and optionally emit chunk metadata.
   - Implement speaker detection and emit `SPEAKER_START` / `SPEAKER_END` events through the WhisperLive client, including participant name/id and session‑relative timestamps.

6) Meeting monitoring (browser)
   - Track participants (count and names) and expose helpers: `window.get...ActiveParticipantsCount()` and `window.get...ActiveParticipants()`.
   - Apply alone‑timeout policies (short post‑speaker windows; longer startup windows) to end recording when appropriate.

7) Removal / abnormal termination
   - In‑page heuristics: watch body text/buttons/indicators for removal.
   - Node‑side periodic removal check to promptly terminate with explicit reason (e.g., `removed_by_admin`).

8) Leave and teardown (always)
   - `leave<Platform>(page, botConfig, reason)` sends `callLeaveCallback(botConfig, reason)` first, then invokes `window.performLeaveAction()`.
   - After normal completion, invoke `gracefulLeaveFunction(page, 0, "normal_completion")`.

#### Cross‑platform Contracts (what every platform module must provide)
- `prepareForRecording(page, botConfig?)`
  - Exposes `window.logBot` and a platform‑specific `window.performLeaveAction()` that:
    - Locates visible/clickable primary/secondary leave buttons.
    - Scrolls into view and clicks with short delays; returns boolean success.
  - Optionally exposes selectors/config fetchers for browser code.

- `waitFor<Platform>MeetingAdmission(page, timeout, botConfig)`
  - Implements the canonical admission algorithm: immediate check, lobby detection with `callAwaitingAdmissionCallback`, disappearance handling with rejection first, admission confirmation, disambiguation of outcomes.
  - Returns: `true` when admitted; or throws/returns structured outcome for `rejected_by_admin` and `admission_timeout`.

- `start<Platform>Recording(page, botConfig)`
  - Loads `browser-utils.global.js` and initializes `BrowserAudioService` and `BrowserWhisperLiveService`.
  - Gates audio on `SERVER_READY`; may send audio chunk metadata for diagnostics.
  - Emits speaker events via `sendSpeakerEvent(type, name, id, tRelMs, botConfig)`.
  - Sets up meeting monitoring and cleans up on unload/visibility change.

- `leave<Platform>(page, botConfig, reason)`
  - Sends `callLeaveCallback(botConfig, reason)` prior to invoking `window.performLeaveAction()`.
  - Returns boolean result of the browser leave action.

#### Canonical Behaviors (Teams) that are platform‑agnostic by design
- Admission detection uses positive (meeting controls present) and negative checks (not in lobby, no pre‑join UI).
- Rejection is checked immediately after lobby disappears and again as a final guard.
- Distinct exit reasons are propagated: `admission_rejected_by_admin`, `admission_timeout`, `removed_by_admin`, `left_alone_timeout`, `startup_alone_timeout`, `normal_completion`, etc.
- `hasStopSignalReceived()` allows fast exit pre‑admission.
- Dual removal detection: in‑page heuristics and Node‑side periodic checks.
- Leave path always triggers a leave callback with a reason prior to UI clicks.
- Post‑recording normal completion always calls `gracefulLeaveFunction`.

#### Dependencies (codebase mapping)
- Platform modules
  - `core/src/platforms/msteams/teams.ts` — canonical implementation of the platform‑agnostic design.
  - `core/src/platforms/googlemeet/google.ts` — should converge to Teams’ canonical behaviors.
  - `core/src/platforms/*/selectors.ts` — provider‑specific selectors for join/admission/leave/speaker indicators.

- Shared services and utils
  - `core/src/services/whisperlive.ts` — Node WhisperLive session initialization and lifecycle.
  - `core/src/services/audio.ts` — Audio configuration helpers (Node context).
  - `core/src/utils/websocket.ts` — WebSocket management utilities.
  - `core/src/utils/index` (imported via `../../utils`) — logging and lifecycle callbacks: `callJoiningCallback`, `callAwaitingAdmissionCallback`, `callStartupCallback`, `callLeaveCallback`, etc.
  - `core/src/types.ts` — `BotConfig` structure (e.g., `meetingUrl`, `botName`, `redisUrl`, `automaticLeave.waitingRoomTimeout`).

- Browser bundle
  - `core/src/browser-utils.global.js` — exposes `window.VexaBrowserUtils` with `BrowserAudioService` and `BrowserWhisperLiveService` used by all platforms.

- Environment/config
  - `WHISPER_LIVE_URL` — explicit server URL for WhisperLive.
  - `WL_MAX_CLIENTS` — capacity hint for server‑side WhisperLive service.

#### Current Status and Known Deviations
- `msteams/teams.ts` implements the full contract above and is the source of truth for new platforms.
- `googlemeet/google.ts` deviations to address for parity:
  - Add negative checks and explicit rejection checks within admission wait.
  - Add dual removal detection (in‑page + Node‑side periodic) with explicit reasons.
  - Add stop‑signal guard pre‑admission.
  - Send leave callback with reason and accept `reason` in `leaveGoogleMeet`.
  - Gate audio sending on `SERVER_READY` and (optionally) send chunk metadata.
  - Implement speaker detection and structured speaker events.
  - Expose ARIA‑based participant helpers; unify alone‑timeout semantics.
  - Always call `gracefulLeaveFunction` after normal recording completion.

#### Acceptance Criteria
- A new platform module that:
  - Exposes the four functions matching the contracts above.
  - Passes the admission flow including rejection/timeout differentiation.
  - Streams audio to WhisperLive only after `SERVER_READY`.
  - Emits speaker events and participant counts.
  - Handles removal and alone timeouts with explicit reasons.
  - Invokes `gracefulLeaveFunction` on all completion paths.

#### Notes
- Selectors and DOM details are platform‑specific; behaviors are not. Start from Teams, swap selectors and minimal glue, and keep the canonical behaviors intact.


