# Zoom LFX hot-debug environment

A faithful, hot-reloadable local loop for debugging the two failure surfaces of a
Zoom meeting bot: **joining** (the LFX portal needs a human click-through) and
**listening** (audio → real GPU whisperlive → transcription segments).

Driver: [`zoom-hot-debug.sh`](./zoom-hot-debug.sh). Default target is the LFX meeting
`zoom-lfx.platform.linuxfoundation.org/meeting/92446951537`.

## Why a special loop

- The bot's Zoom code **navigates the LFX portal URL as-is** and relies on a human to
  VNC in and click through the LF landing page (T&C / guest-name). After that, the
  standard Zoom pre-join selectors take over. So "the bot won't join" is expected to
  need one human click via noVNC — that is by design, not a bug.
- Recent Chromium **ignores `--remote-debugging-address=0.0.0.0`** and binds CDP to
  `127.0.0.1:9222`. So CDP can't be reached from the host; `hot-debug.js` is run
  **inside** the container via `docker exec`. (CDP is opt-in via `BOT_DEBUG_CDP=true`,
  added to `getBrowserArgs` in `core/src/constans.ts` — inert for prod bots.)

## Wiring (all on docker network `vexa-network`)

| Piece          | Where                                                        |
|----------------|-------------------------------------------------------------|
| bot image      | `vexaai/vexa-bot:dev`, `core/dist` mounted for hot-reload    |
| redis          | `vexa-hot-redis` (auto-started; command + segment bus)       |
| transcription  | `http://transcription-lb/...` (live `large-v3-turbo`, CUDA)  |
| noVNC          | `http://localhost:16080/vnc.html` (watch + click the portal) |
| CDP            | in-container `127.0.0.1:9222`, driven by `docker exec`        |

## Run sheet

```bash
cd services/vexa-bot

./zoom-hot-debug.sh selfcheck   # validate the whole rig (dummy URL, never the LFX room)
./zoom-hot-debug.sh run         # join — bot enters the meeting (explicit)
#   → open http://localhost:16080/vnc.html and click through the LFX landing page
./zoom-hot-debug.sh logs        # watch join progress + errors
./zoom-hot-debug.sh inspect     # CDP: chat input / speaker / leave button DOM state
./zoom-hot-debug.sh speaker     # current active speaker
./zoom-hot-debug.sh segments    # LIVE transcription output → proof it's listening
./zoom-hot-debug.sh shot        # screenshot the browser → /tmp/bot-debug-screenshot.jpg
./zoom-hot-debug.sh leave       # graceful leave
./zoom-hot-debug.sh stop        # force kill
```

## Hot-reload a fix

Edit TypeScript under `core/src`, then:

```bash
./zoom-hot-debug.sh restart     # rebuilds core/dist (tsc) and relaunches in seconds
```

> Note: `tsc` currently emits a duplicate-`playwright-core` type error (a stray
> `~/node_modules/playwright-core` shadows the project copy). It is **type-noise only** —
> JS still emits and the bot runs. `restart` tolerates it (`tsc || true`). Worth cleaning
> up separately so the build exits 0.

## Overrides (env)

`ZOOM_MEETING_URL`, `BOT_NAME`, `NOVNC_PORT`, `CONTAINER_NAME`, `DOCKER_NETWORK`,
`BOT_IMAGE`, `MEETING_DB_ID`.
