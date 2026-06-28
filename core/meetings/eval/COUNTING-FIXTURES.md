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

## Related
- Fail-loud relay health (the 90-min-incident fix): `docs/adr/0010-fail-loud-and-attributable.md` (P18),
  `GET /api/meeting/relay-health`, and `core/agent/tests/test_transcription_watcher.py` (401 regression gates).
- The synthetic gmeet counting oracle (PCM, no STT): `core/meetings/modules/gmeet-pipeline/src/count-channelswitch.test.ts`.
