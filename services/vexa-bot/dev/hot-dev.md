# Hot-dev for vexa-bot — iterate without rebuilds

Pack: pack-msteams-diarization-cutover (#394)
Scope: dev/test tooling only. Production paths are unaffected unless
`BOT_DEV_MODE=1` is explicitly set in the environment that loads
`services/runtime-api/profiles.yaml`.

## Why

The diarization-cutover work in this pack iterates on TypeScript inside
`services/vexa-bot/core/src/`. Each iteration normally costs:

- `docker build -f services/vexa-bot/Dockerfile services/vexa-bot/` ≈ 8–15 min
  (Playwright base layer, `npm install`, ts compile, model bake,
  Edge install). Even with BuildKit cache this dominates the
  edit→test loop against `/vexa-meeting-deployment-test`.

Hot-dev collapses this to **save .ts → next bot spawn picks it up**
(the next Teams meeting bot starts in seconds). No rebuilds.

## How it works

Two hooks land in this pack:

1. `services/vexa-bot/core/entrypoint.sh` — when the bot container's
   `BOT_DEV_MODE=1`, the entrypoint runs `npx tsx src/docker.ts`
   instead of `node dist/docker.js`. `tsx` is already in the bot's
   `node_modules` (devDep, installed by the Compose Dockerfile's
   `npm install`).
2. `services/runtime-api/profiles.yaml` — the `meeting` and
   `browser-session` profiles each have an optional bind-mount
   driven by `${VEXA_BOT_DEV_SRC_MOUNT:-}` and an opt-in
   `BOT_DEV_MODE: "${BOT_DEV_MODE:-}"` env passthrough. When the
   placeholders are empty the runtime-api filters them out
   (`services/runtime-api/runtime_api/api.py`).

Together: when you set the two env vars below, every bot container
spawned by `runtime-api` (including those triggered by
`/vexa-meeting-deployment-test`) will run from your host's bot source.

## Enable the loop

```bash
# 1. Make sure the bot image has been built at least once on this
#    branch so tsx + @huggingface/transformers are in node_modules.
cd /home/dima/dev/vexa-pack-pack-msteams-diarization-cutover/deploy/compose
make build-bot-image

# 2. Point the bot container at your host source.
REPO=/home/dima/dev/vexa-pack-pack-msteams-diarization-cutover
export BOT_DEV_MODE=1
export VEXA_BOT_DEV_SRC_MOUNT="${REPO}/services/vexa-bot/core/src:/app/vexa-bot/core/src:ro"

# 3. (Re)start runtime-api with the new env so it picks up the
#    profile expansions. In a Compose lane:
cd "$REPO/deploy/compose"
docker compose up -d --force-recreate runtime-api

# 4. Trigger a meeting via /vexa-meeting-deployment-test, dashboard,
#    or the meeting-api directly. The bot container will run via
#    tsx against your mounted source.
```

## Verify hot-dev is on

The bot container logs the hot-dev banner at startup:

```
[entrypoint] BOT_DEV_MODE=1 — meeting bot via tsx src/docker.ts
```

If you instead see `[entrypoint] Starting browser session node process...`
or `node dist/docker.js`, hot-dev didn't kick in — runtime-api wasn't
restarted with the env vars, or the profile cache hasn't reloaded.
Send `runtime-api` a `SIGHUP` (`docker kill --signal=HUP <runtime-api>`)
to force profile reload, or recreate the container.

## Edit→test cycle

```bash
# Edit anywhere under services/vexa-bot/core/src/.
$EDITOR services/vexa-bot/core/src/index.ts
$EDITOR services/vexa-bot/core/src/services/diarization/onnx-local-diarizer.ts

# Trigger another meeting bot. The next container picks up your edits.
# No rebuilds, no restarts.
```

Browser-side bundle code (`services/vexa-bot/core/src/browser-utils/*`)
is built by `node build-browser-utils.js` at image-build time and
bundled into `dist/browser-utils.js`. If you edit browser-side code
hot-dev does NOT pick it up — you'd need to re-run
`build-browser-utils.js` and rebuild the image. The diarization +
attribution code in this pack is all bot-side (`src/index.ts`,
`src/services/...`), so it's covered.

## What's NOT hot-mounted

| path | reason |
|---|---|
| `core/dist/` | tsx compiles `src/` on demand; dist not used in dev mode |
| `core/node_modules/` | host has Zoom-native build artifacts that break in Linux containers |
| `core/node_modules/@huggingface/transformers/.cache/` | pyannote + wespeaker ONNX models stay in the image (the Phase D bake) |
| `core/assets/` | bot avatar logo, rarely edited |
| `core/scripts/` | build-time only (bake-diarization-models.js) |
| `core/build-browser-utils.js` | browser-side bundler, rebuild required |

## Disable hot-dev

```bash
unset BOT_DEV_MODE VEXA_BOT_DEV_SRC_MOUNT
docker compose up -d --force-recreate runtime-api
```

Or simply leave the env vars unset — defaults route through
`node dist/docker.js` as before.

## Quick direct-run path

For iteration outside the full Compose stack, see
`services/vexa-bot/dev/hot-bot.sh` — spawns one bot container with
mounts + env, given a BotConfig JSON. Bypasses meeting-api /
runtime-api entirely.

```bash
./services/vexa-bot/dev/hot-bot.sh teams "https://teams.microsoft.com/l/meetup-join/..." 'Dev Bot'
```
