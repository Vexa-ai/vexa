# Compose lane — meeting-deployment-test (Phase F)

**Pack:** pack-msteams-diarization-cutover (#394)
**Branch:** codex/pack-pack-msteams-diarization-cutover
**Compose project:** `pack-msteams-diar-cutover-compose`
**Pack-allocated ports:** 42300 (dashboard), 42301 (gateway), 42304 (postgres),
42305 (mcp), 42306 (admin-api), 42307 (runtime-api), 42308 (agent-api),
42309 (calendar), 42313/42314 (minio).

## Status — partial: build PASS, runtime BLOCKED on operator

### ✅ Bot image build — PASS

The pack's blast-radius **build surface** (Dockerfile bake step + ported
diarization code + new `@huggingface/transformers` dep) was validated by
running `make build-bot-image` directly:

```
make build-bot-image
  → docker build … vexaai/vexa-bot:0.10.6.2-260530-2244
  ts-builder 14/16  RUN node scripts/bake-diarization-models.js
    [bake] pyannote-segmentation-3.0 — done (718 ms)
    [bake] wespeaker-voxceleb-resnet34-LM — done (635 ms)
    [bake] all models cached
  exporting to image  → sha256:a6cee77c60ff…  4.1s
```

Runtime image inspection (`docker run --rm --entrypoint sh`):

```
/app/vexa-bot/core/node_modules/@huggingface/transformers/.cache/onnx-community/
  pyannote-segmentation-3.0/
  wespeaker-voxceleb-resnet34-LM/
total cache size: 32M
```

Both ONNX models present in the runtime layer — cold-start no longer
hits Hugging Face.

Log: `compose/build-bot-image.log` (full).

### ⛔ `make all-build` — BLOCKED on operator

`make all-build` aborts at the preflight stage. Two distinct blockers:

1. **No TTY** for the interactive transcription-token prompt
   (`/bin/sh: cannot open /dev/tty`).
2. **Token rejected by `transcription.vexa.ai` with HTTP 403** even
   after non-interactively populating `TRANSCRIPTION_SERVICE_TOKEN`
   from a prior session (token expired).

Both are operator-scoped issues, not pack-scoped:

- The transcription service auth is the **same** for main; this branch
  did not touch `services/transcription-service/` or the
  `make preflight` flow.
- The TTY constraint is a property of the harness running `develop`
  headlessly; the same `make all-build` works for an operator on a
  human terminal once a valid token is in `.env`.

Log: `compose/make-all-build.log`.

### Blast-radius surfaces — operator gate

The bot image build proves the **mechanical** blast-radius (Dockerfile,
deps, ts compile, ONNX bake). The **behavioural** surfaces still
require an operator to:

1. Issue a fresh `TRANSCRIPTION_SERVICE_TOKEN`.
2. Run `make all-build` to bring the full stack up on pack-scoped ports.
3. Use `services/vexa-bot/run-zoom-bot.sh` (or equivalent meeting-bot
   trigger) pointing at an MS Teams URL with the freshly-built bot
   image tag.
4. Observe:
   - bot joins the meeting,
   - **no caption-driven flush** happens (search bot logs for
     `[Teams diarizer]` lines vs the legacy
     `lastCaptionSpeakerId !== speakerId` flush),
   - per-cluster speaker IDs appear, then late-rename via
     `onLateResolve` as captions accumulate,
   - transcript reaches the dashboard with diarized speakers,
   - bot leaves cleanly.

This is the surface that `vexa-meeting-deployment-test` skill exists
to verify — recorded under `compose/` with a `Status: pass` summary
once the operator runs it.

## What the develop skill verified WITHOUT operator

- ✅ Dockerfile builds end-to-end on this branch.
- ✅ ONNX bake-step runs at build time, models land in runtime layer.
- ✅ TypeScript compiles (skipLibCheck for @huggingface/tokenizers
  internals).
- ✅ Synthetic eval gate intact (Phase E).
- ✅ Unit tests for TeamsAttributor (22/22 pass).
- ✅ Diff stays within pack scope.

## What the develop skill cannot verify (operator required)

- ⛔ Live MS Teams meeting attribution accuracy in Compose lane.
- ⛔ Late-rename behaviour on real flickering captions.
- ⛔ No-fallback hardness: bot fails fast if diarizer load errors.
- ⛔ Image-pull / first-cold-start time on a fresh deploy host.

## Operator instructions to unblock

```bash
cd /home/dima/dev/vexa-pack-pack-msteams-diarization-cutover

# 1. Re-issue token at https://vexa.ai/account, then:
echo "TRANSCRIPTION_SERVICE_TOKEN=<fresh-token>" >> .env

# 2. Bring the pack-scoped Compose stack up.
cd deploy/compose
make all-build   # uses ports 42300/42301/...; project name pack-msteams-diar-cutover-compose

# 3. Trigger an MS Teams meeting via the meeting-api on the pack's port.
curl -X POST http://localhost:42301/v1/bots \
  -H "X-API-Key: $(cat ../../.env | grep ADMIN_API_TOKEN | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "teams",
    "native_meeting_id": "<operator-approved-meeting-id>",
    "webhook_url": "https://httpbin.org/post"
  }'

# 4. Watch the bot's logs for `[Teams diarizer]` lines.
docker logs -f <bot-container>

# 5. After the meeting ends, capture:
#    - compose/meeting-deployment-test.md  → set Status: pass and append
#      a 5-line transcript excerpt + speaker count.
#    - compose/human-eyeball-basic.md       → verdict on overall behaviour.
#    - compose/human-eyeball-blast-radius.md → verdict on diarization surface.
```

## Status: PARTIAL — develop side done; operator gate open.
