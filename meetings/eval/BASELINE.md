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
| **gmeet** (per-participant) | `google_meet/dps-nwbw-jzz` — shared presentation on `ch0` + local mic | `segments=13 segN=0 midcut=1 dup=0 short=5 misattr=0` | **0** ✅ | **8%** | 0 — all bound |

## Capture liveness (the FEED — per `capture`)
- **mixed:** `ch999` minted, healthy.
- **gmeet:** per-participant **`ch0` HEALTHY** (122f · 31.2s · avgRMS 0.13 · <floor 2%) + mic `ch1000` → `✓ CAPTURE HEALTHY`. *(This is the first time the gmeet per-participant lane was exercised end-to-end — a shared YouTube presentation provided the remote-channel audio.)*

## ⚠ What this baseline does NOT yet validate — ATTRIBUTION
`misattr=0` here is **weak evidence**: it only means no segment's content self-identified a speaker that
*contradicted* its label. With **no NAMED speakers** in either capture (the shared presentation → `Speaker`;
the local mic *should* be `You` — that mislabel is now fixed), **nothing exercised positive attribution**.
So this baseline validates **capture + transcription + segmentation**, NOT "the right name on the right audio."

**Definitive gmeet attribution requires the speaker-bots eval** — `meetings/eval`: `launch` named synthetic
bots → `drive` a known speech timeline (the ground truth) → `analyze` attribution against it; `noise` for the
active-speaker flicker-hijack test. It needs test accounts/secrets + a human to admit the bots (a `🧑` step),
and is a **planned objective** (RELEASE-PLAN). Until it runs, **gmeet ATTRIBUTION is UNVALIDATED.**

## Thresholds (the bar)
- **`misattr = 0`** — HARD. A wrong speaker label fails the lane. Both lanes meet it.
- **`dup = 0`.** **`seg_N`:** gmeet must stay bound (`0`); mixed `seg_N` is by-design (single stream, no participant identities).
- **oversegmentation (`midcut`):** mixed ≤ ~20%, gmeet ≤ ~10% (snapshot — tuning may improve mixed).

## Known follow-ons (logged, NOT regressions)
- **mixed-lane warm-up ~25s** to first confirm (pyannote + LocalAgreement) — a UX latency to optimise, not a correctness miss.
- **mixed-lane oversegmentation 20%** (`midcut=26`) — the pyannote despeckle characteristic; a quality-tuning objective.
