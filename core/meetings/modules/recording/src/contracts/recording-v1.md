# recording.v1 — chunked media → master file

A meeting's combined media (audio WebM, or PCM WAV) is captured as a sequence of
**chunks** and assembled into a single **master** file when the meeting ends.
This contract is the source of truth; the rest of this page is the **validation
index** — the one place that says where every piece is proven.

## The contract (wire shape)

One frame per chunk:

| field      | meaning                                                        |
|------------|----------------------------------------------------------------|
| `seq`      | monotonically increasing per session (assembly order)          |
| `is_final` | last chunk; the empty final chunk is the COMPLETED signal       |
| `format`   | the chunk's container — `webm` (MediaRecorder) or `wav` (PCM)   |
| `bytes`    | the raw chunk bytes                                             |

Two transports carry the same frame, one per deployment:

- **bot / prod** — HTTP multipart per chunk → `meeting-api` (token-gated).
- **desktop** — a `REC1`-magic binary frame over the ingest WS → `vexa-desktop`
  (`encodeRecordingChunk` / `decodeRecordingChunk` in `@vexa/capture-codec`).

## Architecture — one pure core, thin per-deployment adapters

```
 capture ─chunks─►  ┌─ bot:     HTTP ─► meeting-api ─► MinIO ─┐
                    │                                          ├─► MASTER CODEC ─► serve
                    └─ desktop: WS   ─► vexa-desktop ─► disk ──┘   (pure, deterministic)
```

The **master codec** is the only non-trivial logic, and it is duplicated *on
purpose* — the all-Node desktop has no Python meeting-api, so the same bytes-in →
bytes-out build exists twice:

| pure core (build the master) | TypeScript                                  | Python                                         |
|------------------------------|---------------------------------------------|------------------------------------------------|
| dispatch by format           | `buildRecordingMaster` (recording-codec.ts) | `_build_recording_master` (recording_codec.py) |
| webm = byte-concat Clusters  | `buildWebmMaster`                           | `_build_webm_master`                           |
| wav = RIFF header-merge      | `buildWavMaster`                            | `_build_wav_master`                            |

Everything else (store, transport, DB/JSONB, lifecycle) is a thin adapter that
differs per deployment. The two cores are kept honest by the **golden vectors**
in `golden/` — minimal inputs with independently-computed-correct master bytes
(sha256-pinned). Both builders must reproduce them byte-for-byte.

## Validation — one oracle, many conformers

Validation lives **at the level of the code it proves**, and each level tests
only what it *adds* over the level below. It is distributed by design; this table
is the map.

| level | home | command | proves (the delta) |
|-------|------|---------|--------------------|
| **contract** | `golden/generate.mjs` | `node modules/recording/src/contracts/golden/generate.mjs --check` | the committed vectors *are* the spec (tamper-evident) |
| **module** · master | `modules/recording/src/golden.test.ts` | `npm test` (in `modules/recording`) | TS `buildRecordingMaster` ≡ vectors |
| **module** · wire | `modules/shared/capture-codec/src/recording-chunk.test.ts` | `npm test` (in `modules/shared/capture-codec`) | `REC1` frame encode/decode round-trips; null on audio |
| **service** · master | `services/meeting-api/tests/test_recording_golden.py` | `pytest services/meeting-api/tests/` | Python `_build_recording_master` ≡ **same** vectors |
| **service** · record | `services/meeting-api/tests/test_recording_jsonb.py` | `pytest services/meeting-api/tests/` | `apply_chunk_to_recording`: cumulative bytes, U.7 master-preserve, R2 sticky |
| **deployment** · desktop | `services/vexa-desktop/src/recording-e2e.test.ts` | `npm test` (in `services/vexa-desktop`) | full chain offline: wire → store → assemble → serve + build-on-read |

The TS golden (module) and the Python golden (service) read the *same* `golden/`
vectors — that is what makes the deliberate two-language duplication safe instead
of a drift hazard. Break either builder and **its own** gate goes red.

### Enforced in CI

- `.github/workflows/gates.yml` — `gate:unit` runs the contract integrity guard +
  every brick's `npm test`; `gate:recording-e2e` builds the recording bricks and
  runs the desktop e2e.
- `.github/workflows/test-meeting-api.yml` — `pytest` runs both service tests.

## Definition of done

> Recording is green when `pytest services/meeting-api/tests/` **and** `npm test`
> in `modules/recording`, `modules/shared/capture-codec`, and
> `services/vexa-desktop` all pass — and CI runs all of them on every PR.

## Changing the master format

The vectors are the spec. To change how a master is built:

1. Edit the **oracle** (`generate.mjs`) to express the new expected bytes.
2. Regenerate: `node modules/recording/src/contracts/golden/generate.mjs` (rewrites the JSON).
3. Update **both** builders (TS + Python) until their golden gates pass.
4. Review the new `master_sha256` values in the diff — they are the proof the
   change is intentional. `--check` will fail in CI until the committed vectors
   match the oracle again.
