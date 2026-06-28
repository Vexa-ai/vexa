# Pipeline-spine release — working doc

> What we are doing now: releasing the atomic domain bricks for the meeting →
> transcript pipeline, with the contracts and fixtures between them. Live working
> doc for this effort; the binding spec is [MANIFEST.md](MANIFEST.md), the brick
> reference is [modules/README.md](modules/README.md). This file is the "now".

## Goal

Turn the meeting→transcript concerns into **six contract-defined bricks** —
join · capture · pipeline · speaker-attribution · recording · recorder — each
behind a versioned contract with **replayable fixtures** between them, so every
brick develops against fixtures with no live meeting.

## Decisions locked (this session)

The rule: **a brick is defined by its contract.** One contract out + one fixture
= one brick; platform, topology, and source-type are *internal strategies*.

1. **One `pipeline` brick.** mixed (zoom/msteams, diarizer) and multistream
   (gmeet, channels) share `capture.v1` in → `separated-transcript.v1` out, one
   oracle → one brick, strategy by `meta.topology`. (Earlier we split them two;
   the contract test pulls back to one.)
2. **Attribution is its own brick.** `speaker-attribution` consumes
   `separated-transcript.v1` (+ capture.v1 name hints) → `transcript.v1`. Both
   key-sources (channel id, cluster id) are internal strategies here.
3. **Opaque-key seam.** The pipeline emits segments keyed by an opaque, **unstable**
   id (gmeet channels reassign; diarizer clusters split/merge) — never a name.
   Name resolution lives only in `speaker-attribution`.
4. **`capture` and `recording` stay separate** (`capture.v1` vs `recording.v1` —
   different contracts), but **`capture-source` + `media-delivery` merge** into
   `recording` (both serve `recording.v1`). Duplicate audio taps are a *wiring*
   choice (tap once, fan out), not a brick split.

## The spine (6 bricks, contract-defined)

```
URL →[join]→ session →[capture]─capture.v1─►[pipeline]─separated-transcript.v1─►[speaker-attribution]─transcript.v1─► collector
                          │         (strategy: mixed ‖ multistream)   (strategy: cluster-binder ‖ mapper)
                          └─ tap ─►[recording]─recording.v1─► meeting-api finalizer → media file
                                          [recorder] tees any contract → fixtures
```

| # | Brick | Contract IN → OUT | Status |
|---|---|---|---|
| 1 | join | URL → session (`_host.ts`) | ✅ extracted |
| 2 | capture | page → `capture.v1` | ✅ extracted |
| 3 | pipeline | `capture.v1` → `separated-transcript.v1` | ✅ extracted — mixed (gate→diarizer→Whisper, `createMixedPipeline`) ‖ multistream; bot imports `@vexa/pipeline` |
| 4 | speaker-attribution | `separated-transcript.v1` + names → `transcript.v1` | ✅ extracted — caption-mapper ‖ cluster-name-binder (`attributeMixed`); `transcript.v1` schema defined |
| 5 | recording | tap → `recording.v1` | ✅ merged (acquire + deliver) |
| 6 | recorder | any contract → fixtures (P5) | ✅ extracted |

## Done this session

- 🆕 `contracts/separated-transcript/v1/` — schema + README + goldens pointer.
- Registries updated: `contracts/README.md`, MANIFEST §1a / §2 / capture.v1 note.
- `modules/README.md` — single brick reference (spine, chains, contracts, fixtures index).
- **Structure:** `packages/` → `modules/` (bricks); client SDKs stay in `packages/`.
- **Reshaped to the 6-brick contract model** (all builds + isolation green):
  - `speaker-streams` → `pipeline` (mixed + multistream are internal strategies).
  - merged `capture-source` + `media-delivery` → `recording` (acquire + deliver).
  - split `speaker-mapper.ts` out → new `speaker-attribution` brick.
  - earlier renames: meet-join→`join`, capture-kit→`capture`.

No bot-side pipeline/attribution code extracted yet. No fixtures recorded. No brick tagged.

## Fixtures — the autonomous-development path (current focus)

Goal: every downstream brick develops against fixtures, **no live meeting**. The
infra exists — recorder brick (P5 tee) + the env-configured fixture store
(`$VEXA_FIXTURE_CACHE`, default `~/.vexa/fixtures/capture/v1/<name>/`; local now,
S3 later) + the capture-brick tool `modules/capture/tools/fixture-capture` (the
dedicated extension) + `contracts/capture/v1/validate.mjs` (the conformance gate).

**The extension is a temporary fixture-capture tool** (`services/vexa-extension/`,
all 3 platforms). We use it to record real `capture.v1`, then wire the real
modules against the fixtures. We do NOT integrate it into the brick model.

Per-platform capture reality (validated against the live gate
`ingest-server.usesMixedDiarization()` = `zoom || teams`):
- **gmeet** — per-participant (native `<audio>` elements) → `topology: per-participant` (multistream).
- **zoom + msteams** — **mixed** → mixed track 999 → `OnnxLocalDiarizer` +
  `ClusterNameBinder`. `topology: mixed`. (The per-participant WebRTC-hook path is
  aspirational scaffolding, NOT live — both platforms diarize a single mixed track.)

So only **two** capture fixtures are needed pipeline-wise: one **mixed** (zoom —
also covers teams) and one **per-participant** (gmeet).

One enabling change: **tee the recorder into `ingest-server.ts`**
(`tee(pipelineSink, RawCaptureService)` → `uploadCaptureToS3`), behind a
`RAW_CAPTURE` flag. Then per platform: run one real meeting → fixture → pointer.

Fixture capture order: **zoom (mixed) first** — it also unlocks the two bot→brick
folds (mixed pipeline + cluster-binder) — then msteams, then gmeet.

## Next (in order)

- [ ] **Fix `zoom-speakers` selectors** (stale → names resolve to "Participant") so Zoom hints are real.
- [ ] **Capture `zoom-mixed` fixture** (one real meeting, via `modules/capture/tools/fixture-capture`) → `$VEXA_FIXTURE_CACHE`; validate with `contracts/capture/v1/validate.mjs`.

- [ ] **Fold the mixed strategy into `pipeline`** — extract `chunked-host` +
      `chunked-transcriber` + `diarization/` from the bot behind `meta.topology`. *(bulk of MVP2)*
- [ ] **Fold `cluster-name-binder` into `speaker-attribution`** — second key-source
      strategy (cluster id) alongside the caption mapper.
- [ ] **Add the nine-artifact skeletons** (contract/ · harness/{driver,oracle,lens} ·
      fixtures/ · docs/) to each brick.
- [ ] **Record fixtures/goldens** from `production-replay` FULL mode →
      promotes `pipeline` + `speaker-attribution` `make replay` gates.
- [ ] **Tag + pin** each green brick (`<brick>-vX.Y.Z`) into `release.yaml`.
- [ ] Trim: legacy local-`finalize()` residue in `recording/src/recording.ts`.
