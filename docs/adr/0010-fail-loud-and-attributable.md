# ADR 0010 — Fail loud and attributable (P18)

**Status:** accepted · 2026-06-19 · introduces **P18**, completes **P5**

## Context

Validating the live desktop + extension path, transcription silently stopped producing
segments. It took deep forensic work — decoding capture tapes, running the eval `benchmark`
— to find the cause: the STT service returned **HTTP 402 "Insufficient balance. Available:
0.00 minutes."** The failure was invisible because:

- the whisper adapter threw a **bare `Error`** (status attached ad-hoc), and
- both pipelines **swallowed** the throw — `gmeet-pipeline` in a bare `catch {}`, `mixed-pipeline`
  in a log-only `catch` — and emitted nothing.

So "out of balance," "no speech," and "capture failed" were **indistinguishable** — all just
"no transcript." The constitution governs static structure (P1–P17) but had **no principle for
failure visibility**: nothing forced the 402 onto an observable, attributed surface. And it is
systemic — every adapter (redis transcript, HTTP lifecycle, recording upload, join) has the same
hole, so we'd rediscover this pain at each one.

## Decision

Add **P18 — Fail loud and attributable.** At a dependency's adapter, a failure is:

1. **Translated into a typed fault** — `source` (which dependency) + `kind`
   (`payment_required` / `unauthorized` / `rate_limited` / `unavailable` / `timeout` / `bad_request`)
   + `retryable`. (`TranscriptionError` in `@vexa/transcribe-whisper`.) This is the *failure half*
   of the P5 anti-corruption translation, previously missing — an adapter that translates only the
   happy path is incomplete.
2. **Surfaced, never swallowed.** A core/pipeline reports the fault through an `onError` **seam**
   (P16) and may still degrade gracefully — but the consumer (the composition root) routes it to an
   **observable channel**: the desktop's `log`, a `/telemetry` entry, and a `/ws` **`health`** frame;
   for the bot worker, a `lifecycle.v1` `needs_help`/`failed` with a reason.
3. **Gated.** **Failure-injection** tests force the fault (a 402) and assert it is surfaced +
   attributed and not retried forever — so P18 can't silently regress
   (`whisper/src/errors.test.ts`, `gmeet-pipeline/src/fault-surfacing.test.ts`, under `gate:node`).
   Per P9, an un-gated rule rots.

Faults are **throttled** at the surface (per meeting · source · kind) so a repeating failure informs
without flooding the channel.

## Consequences

- The STT path now turns "insufficient balance" into a **visible health event**, not a phantom
  "0 segments." The silent-dependency-failure class is closed at the STT boundary and the **pattern
  is reusable**: each new adapter defines its own typed fault and wires the same `onError` seam; the
  bot maps faults to `lifecycle.v1`.
- Trade-off: a small per-adapter cost (a typed error + an `onError` forward) and one failure-injection
  test each — cheap now, while the adapter count is small (the whole point of fixing the class early).
- **No shared error base type** — the boundary owns its error vocabulary (P2/P5); the cross-cutting
  contract is the *convention* (a typed `source`+`kind`, surfaced via `onError`), enforced by review +
  the per-adapter gate, not a junk-drawer base class.
