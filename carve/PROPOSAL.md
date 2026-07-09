# Carve contribution proposal

**Source:** `/home/dima/vexa-0.12@0.12` (79bca4b)  
**Target:** `https://github.com/Vexa-ai/vexa-core.git`  
**Candidates:** 986 files · **Removed:** 11 · **Flagged for review:** 15

> Approve by: sanitizing or sign-off on every FLAGGED file, accepting the REMOVED list,
> then running `carve/sync.sh --push` (or `carve/seed.sh --push` for the initial seed).

## ⚑ Flagged — needs human+AI decision (sanitize-and-keep OR add to EXCLUDE)
- `architecture.calm.json`
    L569:          "path": "clients/dashboard"
    L580:          "path": "clients/slim"
- `clients/terminal/README.md`
    L54:- ⬜ planned — login (Google + dev type-any-email, mirroring `clients/dashboard`) → drop the hardcoded subject
- `clients/terminal/src/app/api/auth/adminApi.ts`
    L3: *  Mirrors the dashboard's pattern (clients/dashboard/src/lib/vexa-admin-api.ts) WITHOUT importing it
- `clients/terminal/src/app/api/auth/[...nextauth]/authOptions.ts`
0.12:clients/terminal/src/app/api/auth/[...nextauth]/authOptions.ts:10: *  (the email debug login still works). Credentials come from vexa-secrets (see .env.local).
- `clients/terminal/src/app/api/auth/[...nextauth]/README.md`
0.12:clients/terminal/src/app/api/auth/[...nextauth]/README.md:7:parent `README.md` and the repo `.env.local`). Mirrors the production webapp's nextauth route.
- `clients/terminal/src/surfaces/meetingId.ts`
    L2: *  Id formats mirror the dashboard join-form (clients/dashboard/src/components/join/join-form.tsx):
- `core/gateway/contracts/api.v1/README.md`
    L27:| `clients/dashboard` | proxies `/bots`,`/meetings`,`/transcripts`,`/recordings` — the shapes here |
- `core/gateway/contracts/ws.v1/README.md`
    L38:| `clients/dashboard_new` | proxies `/ws`; renders `transcript` live + `meeting.status`/`chat_message` |
- `core/meetings/eval/COUNTING-FIXTURES.md`
    L18:stage 2 STT        transcription.vexa.ai       → 2-stt.jsonl             (real STT; the LOSS source)
- `core/meetings/eval/src/counting_fixture.py`
    L9:  stage 2  STT     — transcription.vexa.ai  → <store>/<scenario>/2-stt.jsonl  (verbose_json per turn)
    L16:transcription.vexa.ai. Usage:
    L30:STT = "https://transcription.vexa.ai/v1/audio/transcriptions"
    L79:        raise SystemExit("set TX_KEY (transcription.vexa.ai STT token) — see ~/vexa-test-rig/secrets.env")
- `core/meetings/modules/gmeet-pipeline/src/pipeline-realstt.live.test.ts`
    L3: * channel-routed pipeline + real STT (transcription.vexa.ai) and assert it emits glow-attributed
    L20:const URL = process.env.VEXA_TX_URL || "https://transcription.vexa.ai";
- `core/meetings/services/desktop/src/desktop-e2e.live.test.ts`
    L18:const URL = process.env.VEXA_TX_URL || "https://transcription.vexa.ai";
- `core/meetings/services/meeting-api/tests/test_bot_spawn.py`
    L54:    inv = build_invocation(**base, transcription_service_url="https://transcription.vexa.ai",
    L57:    assert inv["transcriptionServiceUrl"] == "https://transcription.vexa.ai"
- `deploy/compose/Makefile`
    L23:## Needs mock-bot:dev + vexa-dashboard:dev (docker build -f clients/dashboard/Dockerfile --build-arg
- `pnpm-workspace.yaml`
    L15:  - "!clients/dashboard" # vendored full Next.js app (own npm); refactor pending
    L16:  - "!clients/dashboard_new" # fresh modular dashboard — its own npm workspace (modules/*), not pnpm

## ➖ Removed by manifest EXCLUDE
- `core/agent/tests/test_cookbook_l2.py`
- `core/agent/tests/test_cookbook_l3.py`
- `core/meetings/eval/BASELINE.md`
- `core/meetings/eval/O6-MEET-LEG.md`
- `core/meetings/eval/src/counting_replay.py`
- `core/meetings/eval/src/read-redis-transcript.mjs`
- `core/meetings/services/bot/eval/README.md`
- `core/meetings/services/bot/eval/RUNBOOK.md`
- `deploy/compose/bin/dashboard-harness.sh`
- `deploy/compose/docker-compose.dashboard.yml`
- `deploy/compose/tests/dashboard_surface.py`

## 👥 Contributors in the carve
-     109 Dmitry Grankin <dmitry@vexa.ai>
-      58 Vexa Agent <agent@vexa.ai>

## 🔍 Risk scan (candidates only)
### Large blobs (>256KB)
- 604KB `core/meetings/eval/replay-fixture/session.captured-signal.jsonl`
- 305KB `core/meetings/modules/join/src/googlemeet/humanized/mocap-data.ts`
### Secret-shaped literals
_none_
### Dangling refs to removed/excluded paths
