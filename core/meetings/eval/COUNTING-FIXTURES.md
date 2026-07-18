# Counting fixtures — the deterministic, offline, autonomous transcript-pipeline gate

_Sibling of this dir's live harness (`README.md`, which drives real bots through `/speak`). This one needs
**no live meeting and no human**: it builds a deterministic **1..N counting** fixture and pushes it through
the real transcript pipeline, attributing any loss to the exact stage. Counting is the perfect oracle — known
content at every position, so a missing number = a drop, a wrong speaker = a mislabel, out-of-order = a
segmentation fault._

## Why counting
A meeting where speakers simply **count 1, 2, 3, …** switching at known boundaries is fully deterministic AND
speaker-attributed. That makes every stage falsifiable with a trivial oracle, and lets the whole thing run
autonomously in CI or ad-hoc, any time.

## The pipeline + where each fixture is valid

```
stage 1 audio      Deepgram TTS                → 1-audio/turNNN.wav      (real audio; valid input)
stage 2 STT        transcription.vexa.ai       → 2-stt.jsonl             (real STT; the LOSS source)
stage 3 segments   transcript.v1 (+ speaker)   → 3-segments.jsonl        (real text + GROUND-TRUTH speaker)
   ── the fixture is valid from here; speaker labels are the ORACLE, not live diarization ──
stage 4 collector  meeting-api consume          (transcription_segments → …)
stage 5 native     tc:meeting:{native} (stream) → the DETERMINISTIC gate stops here
stage 6 copilot    unit:agent-meet-{native}:out  notes/cards (LLM; proven but model-timing-bound, not gated)
```

**Validity:** stages 1–3 are real (TTS / STT / segments). They drive stages 4–6 faithfully. They do **not**
exercise the live bot's audio-capture + diarization (stage-3 speaker labels are assigned = the oracle). That
live-capture leg needs `/speak` (see README), currently network-gated.

## The store (gitignored, ready-to-go, outside the repo)
`~/vexa-test-rig/fixtures/<platform>/count-<scenario>-1to<N>/`
`1-audio/*.wav · 2-stt.jsonl · 3-segments.jsonl · truth.jsonl · manifest.json`
Outside the repo by design (audio is large / may carry voices); `~/vexa-test-rig/secrets.env` holds `DG_KEY`.
The three scripts below are tracked code (`core/meetings/eval/src/`).

## Fixture inventory — what we have today (2026-06-28)

Two complementary shapes cover different stages. **Counting** = stage-1→3 (audio/STT/segments) + the 1..N
oracle, drives STT + downstream; **`captured-signal.v1`** = stage-1 raw per-speaker PCM, drives the real
capture/diarization/segmentation.

### Counting fixtures — gitignored store `~/vexa-test-rig/fixtures/google_meet/` (stages 1–3 + truth + manifest)
| fixture | turns | STT recall (stage 2) | note |
|---|---|---|---|
| `count-silence-1to20` | 4 | 1.0 | clean turns, baseline |
| `count-overlap-1to20` | 4 | 1.0 | boundary overlap |
| `count-continuation-1to20` | 4 | 1.0 | same speaker across a gap |
| `count-solo-1to20` | 1 | 1.0 | no-switch control |
| `count-dynamic-1to20` | 10 | **0.7** | rapid 1–2-number switches — STT drops (real finding) |
| `count-silence-1to500` | 100 | **0.902** | scale; downstream still LOSSLESS |

Each dir: `1-audio/*.wav · 2-stt.jsonl · 3-segments.jsonl · truth.jsonl · manifest.json`. Gitignored (outside
the repo); regenerate any time with `counting_fixture.py`.

### In-repo committed fixtures (small, tracked — for offline gates)
| fixture | shape | drives | gate |
|---|---|---|---|
| `eval/counting-fixture/` | **stage-1 counting audio** — 4 WAVs (1..20, speakers A/B every 5, `silence`) + `truth.jsonl` + `manifest.json`, ~284K | the **L3.5 wav→words leg** (#560): local CPU whisper (`deploy/transcription/docker-compose.cpu.yml`) → collector → API-served words, per-stage attribution + STT-down negative control | `make -C core/meetings/eval counting-ci` (`deploy/compose/tests/counting_stt_test.py`; CI: `pr-value-stt` + `validate-counting-stt`) |
| `eval/replay-fixture/session.captured-signal.jsonl` | **captured-signal.v1** — 36 frames raw per-speaker PCM, Alice→Bob→Alice gmeet | the **real gmeet pipeline** (capture→diarization→segmentation) | `gate:replay` (`services/bot/src/replay.test.ts`) |
| `eval/replay-fixture/transcript-misattr.json` | transcript.v1 w/ a PLANTED mis-attribution | the O-TEL-3 auto-flagger | `analyze.mjs --flag-issues` |
| `core/agent/eval/replay/gamestop-allin.jsonl` | stage-3 segments (transcript.v1, real meeting slice) | the copilot offline (notes/cards) | cookbook L3 / `replay_transcript.py` |

### Not present yet
- **`EVAL_CACHE` (`~/vexa-test-rig/cache`)** — the live-rig TTS clip pool — empty; regen with `FORCE_REGEN=1` (needs DG key).
- **No zoom/teams fixtures**, and **no `captured-signal.v1` for counting** yet (the reuse that would give counting real capture/diarization coverage — see "Closing the capture gap" below).

## Scenarios (the speaker-switch knob — same 1..N oracle, different stress)
`silence` (clean turns, gap) · `overlap` (boundary overlap) · `dynamic` (rapid 1–2-number switches) ·
`continuation` (same speaker across a brief gap) · `solo` (one speaker, control).

## Run it autonomously (any time, no human, no live meeting)
```bash
set -a; . ~/vexa-test-rig/secrets.env; set +a        # DG_KEY (+ optional TX_KEY for STT)
cd core/meetings/eval

# 1) GENERATE a fixture — real TTS → real STT → store (stages 1–3 + truth + manifest + STT oracle)
python3 src/counting_fixture.py --scenario silence --n 500 --speakers A,B,V,C --cadence 5
#   → manifest.json oracle: {missing, dupes, in_order, stt_recall}

# 2) DOWNSTREAM GATE (deterministic, fast) — replay segments → collector → tc:meeting:{native},
#    attribute loss by stage (STT vs downstream). Needs the local vexa-v012 stack up.
python3 src/counting_matrix.py                       # the 1to20 scenario matrix
python3 src/counting_matrix.py ~/vexa-test-rig/fixtures/google_meet/count-silence-1to500   # at scale
#   → per fixture: STT recall(stage-2)  ·  downstream LOSSLESS|DROP(stage-4-5)  ·  PASS/FAIL

# 3) FULL VERTICAL (to the LLM copilot) — segments → … → notes/cards on …:out, assert 1..N reached.
#    Slower / model-timing-bound; use a fresh native and ~12s copilot warm-up (the script does this).
python3 src/counting_replay.py --fixture ~/vexa-test-rig/fixtures/google_meet/count-silence-1to20
```

## Current state (2026-06-28)
- **gmeet downstream (stages 4–5) = LOSSLESS** across all 5 scenarios @1-20 AND silence @1-500 (100 segments):
  `reached == stage-3 input`, speaker attribution preserved. All number loss is **STT (stage 2)** (e.g. dynamic
  r=0.85, 1-500 r=0.902) — the collector→`tc:meeting:{native}` relay (P23: producer stamps `native_meeting_id`)
  carries everything it's given.
- **Agent-on-a-transcript = proven:** `counting_replay` 20/20 — copilot consumes `tc:meeting:{native}` and emits
  notes + cards with correct speaker attribution.
- **Scope:** Google Meet only. Stages 4–6 are platform-agnostic (carry a `platform` string); the per-platform
  capture/diarization modules (`gmeet-/teams-/zoom-capture`) are NOT exercised by the offline path.
- **Not yet:** the live bot audio-capture leg (needs `/speak` on a reachable deployment), and zoom/teams.

## Closing the capture gap (planned reuse of `captured-signal.v1`)
The counting fixtures skip the real capture/diarization layer (their speaker labels are the oracle assignment,
not live diarization). To cover it offline, emit the counting audio in **`captured-signal.v1`** shape — WAV →
Float32 PCM frames assigned to speaker **channels + glows** per each scenario's switch pattern — and replay
through the real `@vexa/gmeet-pipeline` (the `gate:replay` path, optionally with real STT). One fixture would
then cover **capture/diarization + STT + the 1..N oracle**. Not built yet; reuses the committed format + harness.

## Related
- Fail-loud relay health (the 90-min-incident fix): `docs/adr/0010-fail-loud-and-attributable.md` (P18),
  `GET /api/meeting/relay-health`, and `core/agent/tests/test_transcription_watcher.py` (401 regression gates).
- The synthetic gmeet counting oracle (PCM, no STT): `core/meetings/modules/gmeet-pipeline/src/count-channelswitch.test.ts`.
