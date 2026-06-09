# Vexa in-tab extension — "same bot, no admission"

Transcribe the Google Meet **you are already in**. Instead of dispatching a
headless bot that joins as a separate participant and waits in the lobby to be
**admitted**, this Chrome extension captures audio inside your own,
already-admitted meeting tab. There is no bot and no admission phase — you are
the participant.

## Why this is the same bot

The Vexa bot is two halves joined by one seam:

- **Capture half** (browser): per-speaker Web Audio streams off each
  participant's `<audio>`/`<video>` element → resampled 16 kHz PCM.
- **Pipeline half** (Node): `SpeakerStreamManager` → `TranscriptionClient` →
  `SegmentPublisher` (Redis) → dashboard.

The bot bridges them in-process via `page.exposeFunction('__vexaPerSpeakerAudioData')`
because Playwright owns both halves. Here the user's **real tab** holds the
capture half, so that bridge becomes a **WebSocket**:

```
Google Meet tab (you, already admitted)
  └─ inpage.ts (MAIN world)  ── the bot's exact per-speaker capture loop
       └─ content.ts (isolated) ── relay
            └─ background.ts (service worker) ── WebSocket ──┐
                                                              ▼
        ingest-server (vexa-bot/core, run as a WS server)  ── SAME pipeline,
        no Playwright / join / admission / virtual camera
                                                              ▼
                Redis  →  api-gateway /ws  →  existing dashboard (live, free)
```

- Capture loop reused from `vexa-bot/core/src/index.ts` (the per-speaker
  `page.evaluate` block) — see `src/inpage.ts`.
- Pipeline reused verbatim — see `vexa-bot/core/src/ingest-server.ts`.
- Networking lives in the **background service worker** (extension host
  permissions), so Google Meet's page CSP does not block the WebSocket.

## Build

```bash
cd services/vexa-extension
npm install
npm run build      # → dist/  (load this as an unpacked extension)
```

Then in Chrome: `chrome://extensions` → enable **Developer mode** →
**Load unpacked** → select `services/vexa-extension/dist`.

## Backend

The extension streams to the **ingest-server**, which runs the bot's pipeline
behind a WebSocket. Bring it up with the rest of the stack:

```bash
cd deploy/compose
# transcription-service + redis + meeting-api + api-gateway + dashboard + ingest-server
TRANSCRIPTION_SERVICE_URL=http://<your-transcription-service>:8083 \
docker compose up -d redis postgres meeting-api api-gateway dashboard ingest-server
```

`ingest-server` is host-published on **8092** (compose internal 8090; 8090 on the
host is taken by runtime-api).

## Use

1. Open the popup, enter your **Vexa API key** and the ingest URL
   (`ws://localhost:8092/ingest` by default).
2. Join a Google Meet as yourself (you get admitted — no lobby for a bot).
3. Hit **Start**. The popup shows `capturing — meeting <id>, N stream(s)`.
4. Open that meeting in the Vexa **dashboard** — the live per-speaker transcript
   appears with no extra UI, because the ingest-server publishes to the same
   `tc:meeting:{id}:mutable` channel the dashboard already reads.

## Status / scope

MVP: **Google Meet only**, per-element speaker separation with `Speaker N`
labels. Rich speaker-name attribution (the bot's `speaker-identity` vote/lock
logic) and Teams/Zoom are follow-ups. Everything downstream of capture is the
production pipeline, unchanged.
