### Fixed

- **Mixed lane (Zoom · Teams · Jitsi): the transcript no longer loses whole sentences between
  segmenter cuts.** The cut source closes a turn on `speaker→silence`, and its complementary
  re-open does not always fire — on a 267.7 s reference mix, 19.5 s of the 167.5 s of speech sat in
  a span no turn covered, was ringed, and was never sent to STT. A turn now begins where the last
  one ended, so a boundary the model never emits delays words instead of deleting them. Measured
  on that fixture with real STT: WER 34.0% → 11.5%, CER 27.0% → 8.1%, words produced 92% → 95% of
  the golden.
- **Mixed lane: a re-segmenting confirmation no longer leaves an orphan half-sentence in the
  store.** Whisper answers with one sentence where the previous pass gave two; the ids it no
  longer writes are now retired with an empty-text draft row instead of standing beside their own
  confirmation forever.

### Added

- `core/meetings/services/bot/src/flat-quality.test.ts` (`npm run quality:flat`) — a flat WAV plus
  a golden text file in, a scorecard out (WER · CER · segments per golden turn · segment duration ·
  STT submit-span distribution), on the real pyannote cut source, real STT, and a real-time feed.
