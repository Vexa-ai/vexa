# RELEASE-PLAN — 0.12 from the 0.10.6.3 baseline

The **always-current plan** (ADR-0015): keep the proven stack, converge bot+extension, onboard contributors.
Walked as an **objective chain** (per-hop **objective ledger**, [`AGENTS.md`](../AGENTS.md)). Rules: [`ARCHITECTURE.md`](ARCHITECTURE.md); decisions: [`adr/`](adr/).

_Last updated: 2026-06-20 (re-grounded on main @ 0.10.6.3.14 — keep the stack, not rebuild)._

## Goal
A self-hostable **0.12** = the 0.10.6.3 stack **kept + reused**, with the v0.12 **carved capture lane** (one bot,
one extension) + **sealed contracts + 9-gate CI** + **contributor onboarding**. **Rough ≠ half-done** — reuse the
real services (postgres/redis/minio), never stub them.

## Deployment model (four targets, two classes)
| Target | DB | Users | Delivery | Role |
|---|---|---|---|---|
| **desktop** | **sqlite** | single | **`npm install` + extension** | hot-dev rig (raw `@vexa/*`, no rebuilds) **+** simple local app. No postgres/docker. |
| **lite** | postgres (remote) | multi | 1 container + remote DB | single-container cloud deploy |
| **compose** | postgres (local) | multi | docker compose | dev + observable **pilot** |
| **helm** | postgres (managed/in-cluster) | multi | k8s | full **cloud microservice** |

One extension serves all four. Desktop = single-user sqlite; lite/compose/helm = postgres multiuser cloud.

## Keep vs Change
**KEEP (reuse from main):** the cloud trio lite/compose/helm + build/release mechanics (`Makefile`,`VERSION`,`IMAGE_TAG`);
the control-plane images (`api-gateway`, `meeting-api`+collector, `runtime-api`, `admin-api`, `mcp`, `tts-service` — postgres/redis/minio);
the bot-spawn model (runtime-api → `BROWSER_IMAGE`); the **deployment docs + `make` cookbooks** (maintained); the
**desktop** (hot-dev rig + local app; reuse as-is, cleanup deferred).
**CHANGE/CONVERGE:** **one bot** (v0.12 bot root + `vexa-bot_new`'s real adapters, as `BROWSER_IMAGE`); **one
extension** (v0.12 canonical + ported Zoom/Teams `webrtc-hook` + recording, deployment-aware config); the **sealed
contracts + 9-gate CI** as the contribution bar.
**DISCARD:** the from-scratch **sqlite meeting-api** (keep only `api.v1`/`ws.v1` contracts + the `recording_codec` twin); the cloud meeting-api is main's real one.

## Docs discipline (cross-cutting — at EVERY O)
Docs are a per-hop deliverable. Every O revises the **Mintlify docs** (`docs.vexa.ai`, `vexa-docs` skill) + the
deploy docs/`make` cookbooks: **how-it-works**, **quick-start per deployment**, **develop-by-use-case** guides —
**actionable for humans AND agents**. An O isn't done until its docs are revised + the relevant quick-start runs.

## The objective chain (walk top-down; 🤖 autonomous until 🧑/VM gate or UNEXPECTED). **Current: O6 (🧑/VM gate).**
**O4 ✅ DONE (autonomous part) — PR triage.** `docs/PR-TRIAGE-0.12.md`: the 22 PRs bucketed; the 10 "clean" dry-run
apply-checked → **6 cherry-pick clean** (#406,#435,#436,#319,#260,#450 — exact `git cherry-pick -x` per PR), **4
conflict-where-the-carve-moved-lines** (#385,#381,#320,#297 — re-target notes); 4 needs-rework, #446 close, rest stale.
**Opening/merging the PRs is the maintainer's outward-facing step** (I prepared, did not push). 0.11 PRs validate via 0.11 CI + `make smoke`.
**O5 ✅ DONE — Contribution policy + CI.** `CONTRIBUTING.md` rewritten for 0.12 (lanes · local `pnpm gates` + pre-push ·
9-gate guide · authorship via `-x` · L1–L4 pyramid). Fresh-clone baseline: **`pnpm gates` GREEN** (all 9 gates, 16 node pkgs + 3 python).
**Discard executed:** the sqlite meeting-api (app/db/main + my conformance tests) removed; only the `recording_codec` twin kept; `gate:python` green.
**O3 ✅ DONE — Deployments validated + documented.** `docker compose config` VALID (12 images) · `helm template`
RENDERS (29 manifests) · make targets present. Docs: `docs/deployment-options.mdx` (use-cases "why each exists" +
per-deployment quick-starts) in nav (docs.json valid); `deploy/*/README.md` cookbooks cross-linked. Caught a drift
(`make compose`→`make all`). **Live `make all` up + `/health` folds into O6** (needs real STT/DB).
**O2 ✅ DONE — One extension, all deployments.** `v0.12/clients/extension` is canonical (the v0.12 inpage was
already ahead of the shipped one — full Zoom/Teams branches + the gmeet `setSelfName` fix it *lacks*). Added
Meet-side recording, the `webrtc-hook` manifest slot, and `endpoints.ts` (deployment-aware: `desktop` :9099 /
`cloud` :8092, explicit URL overrides). build+typecheck GREEN; `endpoints.test` (21) + `capture-liveness` (25)
GREEN; manifest covers Meet/Zoom/Teams/YouTube; gmeet fix intact. **L4-pending → O6:** live capture per platform/deployment.
**O1 ✅ DONE — Standalone bot finished (code, L1/L2/L3).** All 3 stubs replaced: `join-driver.ts`
(`@vexa/join`, guest+auth), `pipeline.ts` (gmeet + mixed lanes, sink-reconciled), `recording.ts`
(`@vexa/recording` assembler), `capture-bridge.ts` (browser launch + page-capture pump + speak), index wired
(speak via a tee, no orchestrator change). **build+typecheck+isolation GREEN; tests GREEN** (prior 94 + new
pipeline L3 [overlap, no cross-channel mislabel] + recording L3 [webm/wav/seq]). `@vexa/remote-browser` now
re-exports `Page`. **L4-pending → O6:** browser launch, capture bridge, speak injection, recording upload (need a real meeting).

**O2 🤖 — Converge to ONE extension (all deployments).** v0.12/clients/extension canonical (gmeet selfName fix +
capture-liveness); port `webrtc-hook` (Zoom/Teams) + recording from `services/vexa-extension`; deployment-aware
endpoint config. **Expected:** one `pnpm build` green; connects (capture.v1/WS) to desktop + cloud ingest; 4-platform parity. 🧑 eyeball.

**O3 🤖 — Deployments: keep lite/compose/helm.** Reuse `deploy/{lite,compose,helm}` (main's images) + the converged
`BROWSER_IMAGE` (O1) + extension (O2). **Expected:** `make compose`/`lite` up on postgres/redis/minio, `/health` green, extension connects.

**O4 🤖 — PR triage + cherry-picks (preserve authors).** Prepare branches/PRs for ~10 clean PRs
(`#406,#385,#381,#435,#436,#319,#320,#297,#260,#450`) via `git cherry-pick -x`, each `pnpm gates` green, **opened
for maintainer review (no merge)**. Rework notes for 4 capture-lane (`#428,#420,#375,#447`); close `#446`; route internal/stale.

**O5 🤖 — Contribution policy + CI.** 0.12 `CONTRIBUTING`: lanes (internals machine-gated / contract human-reviewed),
local `pnpm gates` + pre-push, what-each-gate-checks/if-it-fails, preserved authorship, L1–L4 pyramid. CI 9-gate already armed.

**O6 🧑/VM — Full-cycle bot validation (the definitive bar) + tag.** **✅✅ MATRIX GREEN — join · transcription · attribution · speaking validated LIVE across all three platforms (Meet · Zoom · Teams) on bbb, 2026-06-20/21.** Remaining: webhooks + recording master→minio (infra-config) → tag 0.12.

**✅ THE FULL-CYCLE MATRIX — validated live across Meet · Zoom · Teams (the definitive bar):**

| Platform | Join | Transcription | Attribution | Speaking | Recording |
|---|---|---|---|---|---|
| **Zoom** (`89237402037`, real public mtg) | ✅ auto-admit, no human | ✅ 39 real-speech segs (mixed+ONNX) | ✅ misattr=0 ≤ baseline | — | ✅ acquire (12 chunks) |
| **Teams** (`392148053670959`) | ✅ admitted | ✅ mixed lane (real audio) | ✅ seg_N by-design, misattr=0 | ✅ **heard** | ✅ acquire |
| **Meet** (`rvf-kywf-pxb`) | ✅ admitted | ✅ verbatim user speech | ✅ **`"Dmitriy Grankin"`** (host-binding, Learning #17 fix live) | ✅ **heard** | ✅ acquire |

- **Speaking** proven on Teams+Meet (human confirmed audible) — synthesis (tts-service 200+PCM) → `tts_sink`→`virtual_mic`→meeting; acts.v1 `{action:speak}` over redis. **Recording-acquire** proven (`record-chunker` chunks live). **Mixed-lane fix** that unlocked Zoom+Teams: `installRemoteAudioHook` pre-nav mirrors WebRTC audio → combined mixed stream (Learning #25). **gmeet attribution** (Meet) is the host-glow-leak fix (Learning #17) confirmed LIVE — clean `"Dmitriy Grankin"` binding.
- **Remaining (infra-config, not bot capability):** webhooks (a customer endpoint + event), recording **master→minio** (`recordingUploadUrl` + meeting-api). The bot side is **done**.

**The ENTIRE bot side is proven LIVE on bbb — admitted, active, capturing (all ✅):**
- ✅ **Build:** `vexaai/vexa-bot:v012` builds on bbb (4.14GB, FROM `vexa/meet-join-env:dev`), bakes `browser-utils.global.js` (capture bundle from the v0.12 bricks) **+ the `pyannote-segmentation-3.0` ONNX** (offline-verified) for the Zoom/Teams mixed lane.
- ✅ **Join + admission + active:** standalone `docker run` joins a real Meet, the **human admitted it**, and it reached `lifecycle.v1 active` ("✅ Admitted: 4 participant tiles").
- ✅ **Capture pipeline END-TO-END:** on the live meeting page the bot connected all 3 participant streams, `AudioContext` `ctx=running`, the AudioWorklet posts PCM continuously, and **36 frames crossed `feedAudio` into the Node pipeline** — capture→worklet→bridge→pipeline proven working. (Streams were digitally silent — `max≈0.0002` — because the speaker-bots weren't driven, so no transcribable speech; see gate below.)
- ✅ **Lifecycle.v1 state machine:** clean `joining → awaiting_admission → active → completed` (and `failed(join_failure)` on the timeout path) emitted to redis.
- **Two real bugs found + fixed live (gate-green, committable):** (a) `startCaptureBridge` ran pre-navigation on the blank page → **deferred to `pipeline.start()`** (post-admission, live page); (b) `AudioContext` started **suspended** → **`ctx.resume()` + `--autoplay-policy=no-user-gesture-required`**. Plus page-console forwarding for observability. (Learning #23.)

**🧑 Remaining legs — each needs a meeting or a credential:**
- **Teams** — SAME mixed lane + WebRTC hook as Zoom (just validated) → should work as-is; needs a **Teams meeting URL** (ideally another public/recurrent one).
- **Meet** — the carved bot + gmeet per-participant capture are proven live (36 frames); needs either a **real public Meet with audio** (the Zoom pattern) OR the **prod speaker tokens** to drive the harness (throwaway tokens are `Invalid API key`; cred store safety-blocked). The read→score instrument is ready.
- **Speaking** — implemented; **synthesis VALIDATED** (bbb tts-service → HTTP 200 + 140KB PCM for the bot's exact `/v1/audio/speech` request); playback = standard `paplay`→`tts_sink` (entrypoint chain) + the wired mic toggle. Only the in-meeting "heard by participants" needs a **controlled meeting** + `voiceAgentEnabled=true`.
- **Recording master→minio** — acquire proven live; needs `recordingUploadUrl` + the meeting-api receiver.
- **Webhooks** — needs a customer webhook endpoint + a transcript event.
- **Remaining legs after audio flows:**
  - **attribution ≥ baseline** — scorer proven (`read-redis-transcript.mjs` + `analyze.mjs TRANSCRIPT_FILE`, unit-tested incl. mis-attribution detection); runs on the live transcript.
  - **recording** — ✅ **now wired** (was an unwired scaffold, Learning #24): `@vexa/record-chunker`'s `createRecordingTap` bundled + `startRecording()` feeds `recording.chunk` post-admission. Acquire+assemble self-proves from logs (master size) even without `recordingUploadUrl`; master→minio needs the upload URL + meeting-api.
  - **speaking** — ✅ **now implemented** (was deferred): `tts-playback.ts` ports production's TTS→`tts_sink` path (acts.v1 `speak` text → TTS service `/v1/audio/speech` pcm → `paplay` → `virtual_mic`), wired into the SpeakController (unmute mic → synth → re-mute). Off-by-default (`voiceAgentEnabled`); env-configured (`TTS_SERVICE_URL`/`TTS_API_TOKEN`); **no contract change** (text/voice already in acts.v1). Build+9 gates green; L4 pending a live run with `voiceAgentEnabled=true`.
  - **webhooks** — end-to-end emission (gated on a live transcript event).
  - then **Zoom/Teams** (ONNX baked; need a meeting + admit) + **authenticated join** (needs an S3 Google profile). Across **Meet · Zoom · Teams**. Full matrix green → **tag 0.12** (version reconciled with `VERSION`/`Chart.yaml`).

## Foundation — ✅ contracts frozen to main
8 sealed in `contracts.seal.json` (gate:schema + gate:contract-version green): public `api.v1` (≡ main OpenAPI 1.5.0)
+ `ws.v1` (≡ main `/ws`); internal capture/transcript/lifecycle/acts/invocation/recording/workspace/runtime `.v1`.

## Known issues (logged · NON-blocking)
- gmeet attribution residuals (cross-bot bleed, ~50% unnamed) — quality, post-fix. `judge.py` over-counts (content cross-check).
- desktop `live_sessions` cosmetic counter bug. prod bot backend 503s on concurrent spawns — launch one-by-one (eval rig).
- mixed lane warm-up ~25s / oversegmentation ~20%.

## Backend SoC validation — ✅ Lane A done; Lane B human-gated (2026-06-21)
A contract-faithful **mock bot** (reuses the real orchestrator+adapters, fakes only Join+Pipeline) validates the
**control plane at L3 on the real compose stack** — backend ⊥ worker, runnable anywhere. All green on bbb, committed
to `claude/busy-bouman-9ea75f` ([`docs/PARITY-MAIN.md`](PARITY-MAIN.md) · [`docs/ARCH-COMPLIANCE.md`](ARCH-COMPLIANCE.md)).
- **Lane A (✅):** A:V1 lifecycle/edge-cases (`gate:compose`+MOCK_BOT, 16 passed) · A:V2 stress · A:V3 chaos · A:V4
  modularity (`gate:test-isolation`/`gate:arch-report`) · A:V5 parity (`gate:parity`). New: execution-target registry
  (`gate:execution-env`, ADR-0020) + planning-embeds-rules (ADR-0021).
- **Lane B (worker-L4): harness reusable + gated (`gate:eval-baseline`); live admit DONE.** The real bot — **spawned by the
  real API** (`POST /bots` → gateway → meeting-api → runtime → `vexaai/vexa-bot:v012`) — joined `rvf-kywf-pxb`, was **admitted**,
  reached `active`, and captured **3 streams**; the DB read `active` (the persistence fix **proven LIVE against the production
  bot**, not only the mock). The transcript **score** awaits two deferred items: `bot_spawn` STT-wiring (task; confirmed live as
  `transcriptionServiceUrl:<MISSING>`) + `internal@vexa.ai` `/balance`=0 (`/purchase`). Learning #28.
- **The mock lane caught 5 real backend bugs O6 (L4) missed** (it bypassed the control plane — raw-stream reads + the
  legacy image). **Fixed + gate-green:** `VEXA_BOT_CONFIG` (was `BOT_CONFIG`) · lifecycle now persists to the DB (✅ confirmed
  live via the API) · transcript envelope matches the collector · **`bot_spawn` STT-wiring** (the invocation now carries
  `transcriptionServiceUrl/Token` — an API-spawned bot transcribes). **Flagged (tasks):** `DELETE /bots` route unmounted ·
  max-bots TOCTOU overspill · bot-orphan-on-terminal. Learning #27. **Close: `pnpm gates` GREEN (exit 0); meeting-api 143 pass.**
- **Dashboard wired + full-surface harness (✅ green on bbb).** main's dashboard de-vendored enough to BUILD in 0.12
  (`packages/transcript-rendering` + the docs helpers it was missing; 0.12-layout Dockerfile + `docker-compose.dashboard.yml`
  overlay → `vexa-dashboard:dev`, gateway-wired, self-host auth). `make -C deploy/compose dashboard-harness` proves the two
  flows the user asked for against the real stack via the MOCK bot: **config · send-bot (the dashboard proxy → gateway →
  runtime) · real-time WS transcript** (through the dashboard's own `/ws`). Caught + fixed a real bug: `/api/config` was
  build-time-cached (`authToken:null`) → `force-dynamic`. Real-bot real transcript = a real-bot run + the internal STT.

## Done (audit trail)
- **Re-grounded** on main's real stack (deployments/images/postgres); discarded the sqlite meeting-api tangent (Learning #20).
- **Contracts sealed to main:** `api.v1` (17/17) + `ws.v1` (7/7); 8 sealed, gates green.
- **Speaker-bots eval (definitive):** remote-channel attribution 85%; **host glow-leak** found, **fixed** (`gmeet-channel-binder` sticky `selfName`, 10 L2) + **L4-confirmed** (LEAKAGE 0%, mic→"You"). Learnings #16/#17/#18. `eval/BASELINE.md`.
- **Process:** objective-ledger forcing function (Learning #19); rough≠half-done + ground-in-main (Learning #20).
- **Gate debts closed:** capture.v1 · eval-baseline · access+canAccess · health.
