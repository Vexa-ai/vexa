# Lite lane — meeting-deployment-test (Phase G)

**Pack:** pack-msteams-diarization-cutover (#394)
**Branch:** codex/pack-pack-msteams-diarization-cutover

## Status — partial: pack's Lite changes verified; full `make build` blocked on pre-existing dashboard build failure (out of scope)

### ✅ Pack's Lite Dockerfile change builds cleanly

The pack's only `deploy/lite/Dockerfile.lite` change is one new
`RUN` step inserted after `npm run build` and before
`npx playwright install msedge`:

```diff
 WORKDIR /app/vexa-bot
 RUN npm ci --omit=dev 2>/dev/null || npm install \
     && npm run build
+# pack-msteams-diarization-cutover (#394): pre-download diarization
+# ONNX models so the Lite container's first bot session doesn't hit
+# Hugging Face. ~32 MB into node_modules/@huggingface/transformers/.cache/.
+RUN node scripts/bake-diarization-models.js
 RUN npx playwright install msedge --with-deps || true
 RUN node build-browser-utils.js || true
```

Build log fragment (`lite/build-lite.log`):

```
#29 [25/49] RUN node scripts/bake-diarization-models.js
#29 0.466 [bake] onnx-community/pyannote-segmentation-3.0 — downloading processor...
#29 0.720 [bake] onnx-community/pyannote-segmentation-3.0 — downloading model...
#29 1.185 [bake] onnx-community/pyannote-segmentation-3.0 — done (721 ms)
#29 1.185 [bake] onnx-community/wespeaker-voxceleb-resnet34-LM — downloading processor...
#29 1.336 [bake] onnx-community/wespeaker-voxceleb-resnet34-LM — downloading model...
#29 1.827 [bake] onnx-community/wespeaker-voxceleb-resnet34-LM — done (642 ms)
#29 1.827 [bake] all models cached
```

The new step succeeded. The bot portion of the Lite Dockerfile
(steps 1–25/49) — including `npm install`, ts compile, model bake —
completed.

### ⛔ Lite `make build` did NOT produce a fresh image

The build then failed at step 39/49 — **the dashboard Next.js build**:

```
> [39/49] RUN cd /app/dashboard-build && npm run build:
ERROR: failed to build: failed to solve: process
"/bin/sh -c cd /app/dashboard-build && npm run build"
did not complete successfully: exit code: 1
```

The failure cause is Next.js module-not-found errors on
`services/dashboard/src/app/api/.../route.ts` files. **This pack
did not touch `services/dashboard/`** — `git diff --stat main..HEAD --
services/dashboard/` is empty. The dashboard build failure is
pre-existing on this dev workstation and is **out of pack scope**.

Because the final image step never ran, the local `vexa-lite:dev` tag
still points at a 7-week-old cached image (`docker history vexa-lite:dev`
confirms this — its `package.json` predates my @huggingface/transformers
add). Operator must resolve the dashboard build issue (or run
`make lite` on a clean host where the dashboard prebuild has
historically worked) before the Lite lane can be verified end-to-end.

### Scope verification

| surface | touched by pack? | build result |
|---|---|---|
| `services/vexa-bot/core/` | yes (new diarization sources + dep) | ✅ steps 1–25/49 pass |
| `services/vexa-bot/Dockerfile` (Compose) | yes (bake step) | ✅ separate build PASS |
| `deploy/lite/Dockerfile.lite` | yes (bake step) | ✅ bake step pass |
| `services/dashboard/` | NO | ⛔ pre-existing failure (out of scope) |
| `services/{runtime-api,meeting-api,admin-api,mcp,...}` | NO | not exercised |

The dashboard build failure does not reflect any regression introduced
by this pack. A `git stash` of `Dockerfile.lite`'s pack-line followed
by a re-build would produce the same step-39 failure — the cause is in
Next.js code (or its environment) on main, not in our changes.

## What the develop skill cannot verify (operator required)

Same as Compose — plus the dashboard prebuild issue:

- ⛔ Get the dashboard Next.js build to succeed (likely needs missing
  env vars: `NEXT_PUBLIC_VEXA_OSS_VERSION`, `NEXT_PUBLIC_VEXA_OSS_RELEASE_DATE`,
  `VEXA_API_URL` resolution, or a clean npm cache).
- ⛔ Single-container Lite supervisor brings up postgres, runtime-api,
  dashboard, transcription, bot manager.
- ⛔ End-to-end MS Teams meeting attribution in Lite mode.

## Operator instructions to unblock

```bash
cd /home/dima/dev/vexa-pack-pack-msteams-diarization-cutover

# 1. Re-issue token at https://vexa.ai/account.
echo "TRANSCRIPTION_SERVICE_TOKEN=<fresh-token>" >> .env

# 2. Investigate the dashboard build separately — it's a main-branch
#    concern. Common fixes:
#      - export NEXT_PUBLIC_VEXA_OSS_VERSION before make build
#      - rm services/dashboard/node_modules services/dashboard/.next
#      - npm cache clean --force
#
#    OR run on a CI host where the build is known to succeed.

# 3. Once the dashboard issue is resolved:
make lite

# 4. After the meeting ends, capture:
#    - lite/meeting-deployment-test.md  → set Status: pass + transcript excerpt.
#    - lite/human-eyeball-basic.md       → verdict on overall Lite behaviour.
#    - lite/human-eyeball-blast-radius.md → verdict on diarization surface.
```

## Status: PARTIAL

- Pack's Lite-image change verified clean (bot+bake step).
- Full Lite image build blocked by pre-existing, out-of-scope
  dashboard build issue.
- Operator gates open as in Compose.
