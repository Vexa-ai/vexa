# speaker-attribution — opaque keys → participant names

Resolves the opaque speaker key on each segment to a real participant name —
the only brick that knows names, so both pipelines stay identity-free.

- IN: `separated-transcript.v1` (segments keyed by channel id / cluster id)
  + `capture.v1` name events (the hints).
- OUT: `transcript.v1` (segments with resolved names).

Key-source-agnostic by design (internal strategy, not separate bricks):
- `src/speaker-mapper.ts` — caption-boundary mapping (`mapWordsToSpeakers`,
  `captionsToSpeakerBoundaries`). Aligns words to platform-caption turns.
- *(next)* the bot's `cluster-name-binder.ts` — diarizer-cluster → name (window
  match + cluster vote). Folds in here: same `transcript.v1` out.

Harness: unit oracle `npm test` (mapper goldens). Golden diff vs recorded
`transcript.v1` at MVP2.

Gates: `npm run check:isolation` · `npm run build` · `npm test`.
