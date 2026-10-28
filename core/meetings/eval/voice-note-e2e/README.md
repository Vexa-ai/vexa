# voice-note-e2e — a voice note, end to end, through the socket Minutes uses

_meetings eval · on a Docker host (bbb), never the laptop · #1783._

`run.sh` starts a throwaway Valkey container, the branch's **meeting-api** (real app, real Redis
writer, the bot's own ACL user, an in-memory meeting store) and **gateway** (real app, a fresh Ed25519
identity key, the production `websockets` relay) as local processes, and a real transcription
service. `client.py` then does what Minutes does: `POST /bots {"platform":"voice_note"}` through the
gateway, streams a speech WAV as capture.v1 frames over `WS /voice-notes/{id}/stream` at real-time
pace, says Stop, waits for the bot (`LocalBotRuntime`: the branch's `src/index.ts` under `tsx`, one
process per note) to report its terminal state, and reads the transcript it wrote to
`transcription_segments`. It prints seconds sent, acknowledged and transcribed, the bot's terminal
line and the text.

The speech is synthetic (`speech.txt` through `espeak-ng`), so the expected words are known.

Prerequisites on the host: `uv`, Node 22 + `pnpm install --filter @vexa/bot...` and a build of the
bot's workspace dependencies in this checkout, `espeak-ng`, `ffmpeg`, Docker for Valkey.

## Through a real browser — `browser.mjs`

`client.py` drives the socket itself, so it never exercises the microphone, the AudioWorklet, the
terminal server's cookie-to-key relay or the UI. `browser.mjs` does: Playwright Chromium with a fake
microphone playing a speech WAV, signed in to a running Minutes with a one-time sign-in link, starts a
note from the empty chat's *Record a voice note* suggestion as a person does (or, with `START=agent`,
by asking the agent, whose `record_voice_note` tool opens the recorder), records for `RECORD_SEC`, presses Stop in the
meeting controls and reads the finished transcript. It reports frames and seconds sent, seconds
meeting-api acknowledged, transcript lines that arrived **while recording**, and the seconds
transcribed; it fails unless audio was acknowledged, a line arrived live and Stop completed the note.

```bash
# in the Playwright image (mcr.microsoft.com/playwright, same version as the `playwright` package)
SIGNIN_LINK_FILE=link.txt WAV=speech.wav RECORD_SEC=30 node browser.mjs
MOBILE=1 ...                 # a phone profile: the chat list is a drawer, opened as a person would
STALE_PARENT_COOKIE=1 ...    # an older sign-in cookie of the same name on the parent domain, sent first
START=agent ...              # ask the agent "Record a voice note for me." instead of pressing the suggestion
SHOTS=dir ...                # screenshots of the empty chat (suggestions and their icons) and of the recording
```

The sign-in link is a credential: it is read from the file and never printed.
