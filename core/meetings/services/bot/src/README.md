# @vexa/bot — src

The bot worker's source. Hexagonal: the orchestrator core depends only on ports + contract
types; transports are adapters wired at the composition root.

**Status: 2b adapters wired (L1/L2/L3 green); the browser-resident legs are L4-pending → O6 (VM).**

| File | Role |
|---|---|
| `index.ts` | **composition root** — validates config, launches the browser, wires the REAL adapters, runs the orchestrator, exits. Speak acts are tee'd to a voice handler and `reconfigure` acts to the transcription-language control (orchestrator core untouched). The container entrypoint (`main`). |
| `config.ts` | `invocation.v1` boot config — parse + ajv-validate `VEXA_BOT_CONFIG`, fail-fast (P14). Exports the typed `Invocation`. |
| `transcription-language.ts` | the live transcription-language setting (`createLanguageControl`), the `reconfigure` act handler, and the language-aware STT call (auto · forced · restricted). Pure. |
| `ports.ts` | the port interfaces the core depends on: `JoinDriver · Pipeline · TranscriptSink · LifecycleSink · ActsSource · RecordingSink`. Pure (no transport types). |
| `test-doubles.ts` | shared L2 port doubles (`noopAloneness`, `noopActs`, `noopPipeline`, …) — one export site for every orchestrator construction in tests. |
| `orchestrator.ts` | the `lifecycle.v1` state machine (`createOrchestrator`) — joining → awaiting_admission → active → (completed \| failed). Depends only on ports. |
| `contracts.ts` | TS mirrors of the published `lifecycle.v1 · acts.v1 · transcript.v1` schemas + the executable `canTransition` machine. |
| `join-driver.ts` | **JoinDriver** — wraps `@vexa/join` `joinMeeting`/leave/removal (guest + authenticated); maps `JoinState`→`BotStatus`. |
| `pipeline.ts` | **Pipeline** — `google_meet`→`@vexa/gmeet-pipeline` (per-channel, glow-named) · `zoom`/`teams`→`@vexa/mixed-pipeline`; STT via `@vexa/transcribe-whisper`; lane sink → bot `TranscriptSink.publish`. Exposes `feedAudio`. |
| `recording.ts` | **RecordingSink** — `@vexa/recording` assembler (`buildRecordingMaster` on `is_final`/`close`) → upload (`RecordingService`). |
| `capture-bridge.ts` | **L4-pending (O6)** — browser launch (+ S3 auth profile), page-side capture inject + PCM pump → `pipeline.feedAudio`, and the speak controller. Browser-resident; not unit-provable — validated on the VM. |
| `*.test.ts` | L1/L2/L3 — config (ajv goldens) · orchestrator (lifecycle.v1 sequence, fake ports) · lifecycle-http/transcript-redis/acts-redis (transports) · **pipeline (L3: capture→lane→stt→publish, overlap no cross-mislabel)** · **recording (L3: webm/wav/seq)**. |

Tests run via `tsx` (no build step): `npx tsx src/<file>.test.ts`; all chained in `npm test`.

## Transcription language

The setting is `{language, allowedLanguages}` (`transcription-language.v1`); a code is `^[a-z]{2,3}$`.

| Mode | Setting | Every STT call |
|---|---|---|
| auto | no language, empty list | carries no language |
| forced | language, empty list | pinned to `language` |
| restricted | non-empty list | one entry: pinned to it. Several: unpinned; a detection outside the list (or none) is re-run on the same audio pinned to the fallback — `language` when set, else the first entry. Each fallback is counted and logged. |

`invocation.v1` seeds it; an off-contract pair (non-code, language outside a non-empty list) fails
boot with `validation_error`. An `acts.v1` `reconfigure` replaces each field it carries — a present
`language` sets it (`null` clears), a present `allowedLanguages` sets it (`[]` clears), an absent key
keeps that field. The next STT call uses the new setting; calls in flight finish with the old one. A
malformed act is refused with a `console.error` naming why and changes nothing. `task` is not
supported (transcribe only): a non-`transcribe` task is reported and ignored. The mixed (Jitsi) lane
reads the pinned language per turn for its segment stamp and language-probability gate.

Log lines: `[bot] transcription language: <summary> (invocation)` at boot and
`[bot] transcription language: <old> → <new> (reconfigure)` per applied change, where a summary is
`auto` · `forced de` · `restricted [de,en] fallback de`. With the capture-signal recorder on, each
setting is also an observation (`source: 'transcription-language'`, `{language, allowedLanguages, cause}`).
