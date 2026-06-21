# 0.12 L4 eval baseline (P19 · `gate:eval-baseline` · ADR-0011)

The recorded L4 ground-truth scores per **user-facing lane** — the bar future L4 runs must not regress.
Re-derive deterministically by replaying the source tape through `meetings/eval` (`replay` → `analyze`);
no live meeting needed. The `analyze` `SCORE` line is the gate.

_Captured 2026-06-20 · all-in-one desktop @ internal STT (transcription.vexa.ai) · extension `0.620.805.5`._
_Method: live human capture (the ground-truth oracle) → tape recorded → scored by the `analyze` instrument (the validation economy: human earns it, instrument pins it)._

## Scores

| Lane | Source (live) | `SCORE` | misattr | overseg (midcut) | unbound `seg_N` |
|---|---|---|---|---|---|
| **mixed** (youtube · zoom · teams) | `youtube/x2VHFgyawPE` — a live AI-talk, ~130 turns | `segments=130 segN=130 midcut=26 dup=0 short=9 misattr=0` | **0** ✅ | **20%** (pyannote characteristic) | 130 — *unnamed by design* (single mixed stream) |
| **mixed — LIVE v0.12 BOT (zoom)** ⭐ | `zoom/89237402037` — **real public meeting**, carved bot auto-admitted, WebRTC-hook→mixed lane (Learning #25); 2026-06-20 | `segments=39 segN=39 midcut=7 dup=0 short=17 misattr=0` | **0** ✅ | **18%** (≤ baseline) | 39 — unnamed (no platform hints) |
| **mixed — LIVE v0.12 BOT (teams)** ⭐ | `teams/392148053670959` — carved bot joined+admitted, WebRTC-hook→mixed lane, transcribed real audio (*"if you can hear… voice agent is"*); 2026-06-21 | live confirmed segments (`seg_N`) | **0** ✅ | within baseline | unnamed (by design) |
| **gmeet — LIVE v0.12 BOT (meet)** ⭐ | `google_meet/rvf-kywf-pxb` — carved bot, **real human speech** attributed **`"Dmitriy Grankin"`** verbatim (*"I'm speaking, I'm saying things out loud"*, *"I'm talking now"*); host-name binding live (Learning #17 fix confirmed); 2026-06-21 | live confirmed segments, all bound to the speaker | **0** ✅ | low | **0 — all bound to the named speaker** ✅ |
| **gmeet** (per-participant) | `google_meet/dps-nwbw-jzz` — shared presentation on `ch0` + local mic | `segments=13 segN=0 midcut=1 dup=0 short=5 misattr=0` | **0** ✅ | **8%** | 0 — all bound |

## Capture liveness (the FEED — per `capture`)
- **mixed:** `ch999` minted, healthy.
- **gmeet:** per-participant **`ch0` HEALTHY** (122f · 31.2s · avgRMS 0.13 · <floor 2%) + mic `ch1000` → `✓ CAPTURE HEALTHY`. *(This is the first time the gmeet per-participant lane was exercised end-to-end — a shared YouTube presentation provided the remote-channel audio.)*

## gmeet ATTRIBUTION — definitive measurement (speaker-bots eval, 2026-06-20)
The shared-presentation `misattr=0` above was **vacuous** (no NAMED speakers → attribution never exercised).
The speaker-bots eval fixed that: 2 named synthetic bots (`spk-Anna`, `spk-Zoya`) admitted to a live Meet,
`drive`n through **6 non-overlapping self-identifying turns**, captured by the desktop, scored by `judge.py`
against the driven ground truth (`truth.jsonl`).

**Result (raw):** `COMPLETENESS 6/6=100%` · remote-channel attribution **22/26 = 85%** · named-rate 45%.

| Channel | True owner | Labels received | Verdict |
|---|---|---|---|
| `ch-0` | Anna | `spk-Anna`×15, **`Dmitriy Grankin`×3**, `spk-Zoya`×1, `Speaker`×7 | contaminated by the **host glow-leak** |
| `ch-1` | Zoya | `spk-Zoya`×7 | **clean — 7/7** |
| `ch-1000` | host mic | `Speaker`×144 | unnamed (mic→"You" fix not in the tested 07:55 build) |

**Defect found + fixed:** the host's tile glow leaked its name onto Anna's *remote* channel (content-proven:
`@106.7 "This is Anna." → labeled "Dmitriy Grankin"`). Root cause: self-exclusion was only the per-scan
`data-self-name` marker, which Meet drops transiently. **Fix (L2-validated):** `gmeet-channel-binder` now pins a
**sticky `selfName`** it refuses to bind to any channel, fed by `gmeet-speakers.onSelf` + the inpage wiring
(`gmeet-channel-binder.test.ts`, 10 checks, the leak reproduced). **Owed: an L4 re-test on the rebuilt
extension** (manifest ≥ `0.620.921.54`) to confirm the leak is gone live — until then gmeet attribution is
**fixed-but-not-yet-L4-reconfirmed**.

**Instrument caveat (Learning #18):** `judge.py`'s headline "precision 64% / wrong=9" **over-counts** — ~5 of the 9
are correctly-labeled segments shifted into the adjacent truth window by variable `/speak` latency (fixed
`L=3.4s` can't track it). The content-anchored misbind count is **4**, all the one host-leak mechanism.

**Note:** the `noise` flicker-hijack run and an overlap-stress run remain (RELEASE-PLAN follow-ons).

## Thresholds (the bar)
- **`misattr = 0`** — HARD. A wrong speaker label fails the lane. Both lanes meet it.
- **`dup = 0`.** **`seg_N`:** gmeet must stay bound (`0`); mixed `seg_N` is by-design (single stream, no participant identities).
- **oversegmentation (`midcut`):** mixed ≤ ~20%, gmeet ≤ ~10% (snapshot — tuning may improve mixed).

## Known follow-ons (logged, NOT regressions)
- **mixed-lane warm-up ~25s** to first confirm (pyannote + LocalAgreement) — a UX latency to optimise, not a correctness miss.
- **mixed-lane oversegmentation 20%** (`midcut=26`) — the pyannote despeckle characteristic; a quality-tuning objective.
