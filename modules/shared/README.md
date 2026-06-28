# modules/shared

The cross-cutting stages both topology lanes (gmeet, mixed) drive — nothing
platform-specific lives here. Each is its own isolated brick with its own README.

| module | role | contract |
|---|---|---|
| [`whisper`](./whisper) — `@vexa/transcribe-whisper` | the stt.v1 egress: PCM window → Whisper segments | `stt.v1` |
| [`buffer`](./buffer) — `@vexa/transcribe-buffer` | the LocalAgreement-3 confirm primitive: which leading words are stable enough to confirm | — (pure fn) |
| [`capture-codec`](./capture-codec) — `@vexa/capture-codec` | frame + event byte serialization, shared by both lane capture contracts | the wire codec |

Flow: a lane pipeline cuts an audio window → `whisper` transcribes it (stt.v1) →
`buffer.localAgreement()` decides how many leading words are stable enough to
confirm → the lane's `transcript.v1` sink. `buffer` is a pure primitive (no I/O);
the lane driver owns the loop and injects `whisper`, so the two stay independently
swappable.

## Function naming conventions (project-wide)

Every module's public surface follows these:

- **construct**: `create<Name>(opts) → <Name>`; stateful pure logic = a `class`.
- **push data in**: `feed<Noun>(…)` — `feedAudio`, `feedFrame`, `feedHint`.
- **emit out**: one `on<X>` callback per output; a single `sink` object only when outputs
  are coupled (e.g. `sink:{ publish, publishPending, clearPending, rename }`).
- **serialize**: `encode<X>` / `decode<X>`.
- **stt**: `transcribe(pcm, prompt) → segments`.
- **read state**: `get<X>()`.
- **teardown**: `dispose(): Promise<void>` if it flushes; `destroy(): void` for sync teardown.
