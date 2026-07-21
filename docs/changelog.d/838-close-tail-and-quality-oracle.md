### Fixed
- **gmeet: a turn that ends while STT is behind no longer publishes a stale draft as the whole turn.**
  At end-of-stream (bot leaving, meeting ending, channel rotation) the lane emitted the last Whisper
  draft and reset — discarding audio newer than that draft, while stamping the emitted segment with
  the buffer's full extent. A turn whose draft covered its first seconds went out as a full-length,
  plausible-looking segment with most of its words missing, and nothing downstream reported a gap.
  The untranscribed audio is now submitted (or deferred to the in-flight response) first; the draft
  remains the fallback for an empty or hallucinated final response, so a turn is still never lost.

### Added
- **A transcript QUALITY oracle** — `core/meetings/eval/src/speech_fixture.py` builds a
  `captured-signal.v1` session out of real speech whose text is known, and
  `core/meetings/services/bot/src/quality.test.ts` replays it through the real gmeet lane against
  real STT and scores recall / precision / fragmentation / attribution against that truth. Every
  prior replay harness ran on a mock transcribe returning canned text, so none of them could see a
  transcript that was structurally perfect and substantively wrong. Not in the default chain and
  gates nothing by default: it needs a live STT endpoint and is not deterministic.
