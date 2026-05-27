# MVP0 — how to run the RnD harness

## TL;DR

```bash
cd services/vexa-bot/rnd/diarization
./scripts/dev.sh                                       # placeholder transcripts
TRANSCRIPTION_URL=http://localhost:8083 ./scripts/dev.sh   # real Whisper output
```

Then in your browser:

1. Open <http://localhost:43500/dashboard> (leave it visible).
2. Open <http://localhost:43500/> in another tab.
3. Click **Share tab and start** → pick the YouTube tab → **tick "Share tab audio"** → confirm.
4. Watch `/dashboard` populate with `speaker_0` / `speaker_1` chips and transcript lines.

Stop: **Stop** button on the capture page, or the browser's "Stop sharing" pill.

## Environment variables

| Var                 | Default                                            | Purpose                                                                |
| ------------------- | -------------------------------------------------- | ---------------------------------------------------------------------- |
| `PORT`              | `43500` (pack `compose_dashboard` slot 125)        | HTTP + WS port.                                                        |
| `TRANSCRIPTION_URL` | unset                                              | If set, harness POSTs to this Whisper-compatible endpoint. Otherwise placeholder. |
| `NUM_SPEAKERS`      | `2`                                                | How many round-robin labels the stub diarizer rotates through.         |

## Local smoke verification (no browser required)

```bash
PORT=43500 npx tsx src/server.ts > /tmp/harness.log 2>&1 &
sleep 2
curl -s -o /dev/null -w "/ -> HTTP %{http_code}\n"           http://localhost:43500/
curl -s -o /dev/null -w "/dashboard -> HTTP %{http_code}\n"  http://localhost:43500/dashboard
curl -s -o /dev/null -w "capture.js -> HTTP %{http_code}\n"  http://localhost:43500/static/capture.js
curl -s -o /dev/null -w "dashboard.js -> HTTP %{http_code}\n" http://localhost:43500/static/dashboard.js
```

Expected: four `HTTP 200` lines.

Then optional WS smoke:

```bash
node -e "
const WebSocket = require('ws');
const w = new WebSocket('ws://localhost:43500/transcript');
w.on('open', () => console.log('ws OPEN'));
w.on('message', (d) => console.log('ws RECV:', d.toString()));
setTimeout(() => { w.close(); process.exit(0); }, 1500);
"
```

Expected on connect: `diarizer-info` event with `name = "vad-round-robin (MVP0 stub, RMS-energy VAD)"`, then `transcription-status` event with `reachable: true|false`.

## Hot-reload behavior

`scripts/dev.sh` runs `tsx watch src/server.ts`. On any `src/**/*.ts` save the
whole Node process restarts. Acceptable at MVP0 because no model weights are
loaded in-process (the RMS-energy VAD is pure JS, ~zero startup cost). MVP1's
pyannote sidecar will live in a **separate** Python child process so its
model weights survive harness reloads — that pattern is the documented
target for the harness, not implemented at MVP0.

## Browser requirements

- Chromium-based browser (Chrome, Edge, Brave, Arc) — these expose tab audio
  via `getDisplayMedia({ audio: true })` with the "Share tab audio" checkbox.
- Firefox can share the screen but tab-audio capture support is limited;
  prefer Chromium for the MVP0 demo.
- HTTPS is **not** required because everything is on `localhost`.
