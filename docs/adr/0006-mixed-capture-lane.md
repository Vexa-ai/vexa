# ADR 0006 — Two capture lanes: per-channel (gmeet) vs mixed+diarize (zoom/teams/youtube)

**Status:** accepted · 2026-06-19 · enforces **P5, P10**

## Context

Speaker-attributed transcription needs per-speaker audio. **Google Meet** exposes each remote
participant as its own `<audio>` element, so we can capture one clean PCM channel per speaker and bind
the name at the source (the "glow" hint) — the **gmeet lane**. **Zoom · Teams · YouTube** expose no
per-participant audio in the page: a tab capture yields a single *mixed* stream. The per-channel
approach is impossible there, so a different lane is required — or speaker attribution is lost on three
of four platforms.

## Decision

**Run two capture lanes behind the same `transcript.v1` output, chosen by platform — not one lane
forced to cover both shapes.**

- **gmeet lane** (`@vexa/gmeet-capture` → `@vexa/gmeet-pipeline`): per-participant `<audio>` → one PCM
  channel each, glow-name bound at capture → channel-routed transcription. Output carries
  `source: "glow-bound"`. This is the high-fidelity path; use it wherever per-participant audio exists.
- **mixed lane** (`@vexa/mixed-capture-core` + per-platform DOM watchers → `@vexa/mixed-pipeline`): one
  tab-captured **mixed** remote stream on **channel 999** (+ local mic on **1000**) → pyannote
  diarization → `cluster-name-binder` maps diarized clusters to names using **DOM active-speaker hints**
  (`@vexa/zoom-capture`, `@vexa/teams-capture`, youtube). Output is `source: "hint-derived"`; YouTube,
  having no participant metadata, stays unnamed by design.
- **Each platform's DOM is an ANTI-CORRUPTION ADAPTER (P5).** Zoom's active-speaker DOM, Teams' blue-square
  voice-level outline, gmeet's glow — each is translated to a neutral active-speaker/glow hint at the edge;
  the pipeline core never sees platform vocabulary.
- **Shared spine.** Both lanes share `@vexa/buffer` (LocalAgreement-N confirmation) and
  `@vexa/transcribe-whisper`, and both emit `transcript.v1`. The lane split is at capture+attribution only.

## Consequences

- Two lanes are more surface than one, justified by a real force (the platforms differ in what audio they
  expose) — P10: a module-level split, not a service split. The desktop host routes by detected platform.
- Mixed-lane attribution quality depends on DOM hints + diarization, so it is inherently lower-fidelity than
  gmeet's source-bound channels; the eval harness scores leakage/attribution per lane (≥ baseline gate).
- Active-speaker **flicker** can hijack attribution; mitigated by debounce in the platform watchers and the
  binder despeckle. The failure-mode injector (eval `noise`) regression-tests it.
- Adding a platform = a new DOM watcher adapter feeding the mixed lane; the pipeline is untouched.
