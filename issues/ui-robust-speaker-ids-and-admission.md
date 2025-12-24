# UI-robust speaker IDs + admission/report (Meet + Teams)

## Problem summary
- Current bot behavior (join → awaiting admission → admitted/removed) is driven largely by **DOM selectors** (text/aria/CSS) and is therefore **fragile** under A/B tests, localization, and UI refactors.
- Speaker detection is also largely **DOM-driven** (tile classes/voice-level elements), so it breaks for the same reasons.
- Product requirement: **speaker IDs** (ideally stable; ideally mappable to real participants).

## Key idea: shift “ground truth” away from UI
Replace UI/DOM heuristics with **transport/app-state signals** that change far less often:
- **WebRTC (RTCPeerConnection + getStats())** for admission state + speaking events (works in-browser for both Meet + Teams).
- **Platform calling APIs** where available (Teams) for true participant identity mapping.

## Speaker IDs: define what “ID” means
There are tiers with different feasibility:

- **Tier A — platform-truth participant IDs (best)**  
  Example: Teams/AAD identity; Meet internal participant identity.  
  Requires **platform signaling/app state**, not DOM.

- **Tier B — transport-truth stream IDs (recommended baseline)**  
  Example: WebRTC **SSRC / receiverId / mid** derived from `pc.getStats()`.  
  **UI-proof** and stable within a session, but doesn’t guarantee a human identity without extra mapping.

- **Tier C — voice-truth diarization IDs (fallback)**  
  Example: `spk_0`, `spk_1`. UI-proof, but “who” requires attribution.

## Make admission/join robust (stop depending on DOM)
Implement a browser-side call-state collector (installed via `addInitScript` **before** platform scripts run):

- Track all `RTCPeerConnection` instances (wrap constructor; store in `window.__vexaPeerConnections`).
- Poll `pc.getStats()` to derive:
  - **Admitted**: connected ICE/DTLS state **and** inbound audio stats progressing (e.g., `inbound-rtp` audio `bytesReceived` increasing and/or `totalAudioEnergy` increasing).
  - **Waiting/lobby**: no connected PC and no inbound audio progression.
  - **Ended/removed**: connection transitions to `failed/disconnected/closed` OR inbound audio stalls for N seconds after it previously flowed.

Then, in Node:
- Replace `waitFor*MeetingAdmission()` selector loops with polling `page.evaluate(() => window.__vexaCallState())`.
- Keep DOM-based checks only as a last-resort fallback / diagnostics.

## Make speaker events robust (stop depending on DOM tiles)
Derive `SPEAKER_START/END` from WebRTC stats:
- For each inbound audio stream (`inbound-rtp` audio):
  - Use **SSRC (or receiverId+ssrc)** as `participant_id_meet` (Tier B).
  - Determine “speaking” using:
    - `totalAudioEnergy` delta over time (preferred), or
    - `audioLevel` where available.
- Emit speaker events keyed by that transport ID.

This removes reliance on class names, “active speaker” UI elements, or participant tile DOM structure.

## Identity mapping (Tier B → Tier A): platform-specific

### Teams (best path to Tier A)
If you must have real participant identity mapping reliably:
- Move Teams ingestion to a **calling bot** path (Graph Cloud Communications / ACS Teams interop).
  - Admission/lobby/removed are first-class events.
  - Roster includes participant identifiers.
  - Audio streams can be associated with participants without UI scraping.

### Google Meet (no public “calling bot”)
To reach Tier A for Meet, you must accept tradeoffs:
- **Signaling interception (recommended if Tier A is required)**: patch `WebSocket`/`fetch`/`XMLHttpRequest` early and extract participant↔stream mapping from app payloads.  
  This is still reverse-engineering, but materially more stable than DOM selectors/strings.
- Otherwise: use Tier B IDs (SSRC-based) + optional attribution layer (best-effort).

## Operational simplification
Leaving the meeting:
- Prefer **closing the page/context** (UI-invariant) over clicking “Leave” buttons.
- Treat “polite leave UI click” as best-effort only.

## Rollout plan (low-risk, high ROI)
- **Phase 1**: implement WebRTC-based `callState` and `speakerStats` collectors; switch admission + speaking to those signals; keep selector code as fallback.
- **Phase 2 (Teams)**: if Tier A required, implement the Teams calling-bot ingestion path and make Playwright a fallback only.
- **Phase 3 (Meet)**: if Tier A required, add signaling interception for participant↔stream mapping; otherwise stick with Tier B IDs.

## Open decision (blocks final architecture)
Do we require:
- **Tier A**: participant IDs that map to real users (names/AAD IDs), or
- **Tier B**: stable per-session speaker IDs (SSRC/receiverId/mid) sufficient for downstream correlation?


