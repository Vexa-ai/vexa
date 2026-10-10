# golden — transcript.v1 vectors

Conforming examples (the spec, P8). Filename = `<Shape>.<case>.json`; the prefix before the first dot is
the `$def` validated against `../transcript.schema.json` by `../validate.mjs` (run by `gate:schema`).
`token` fields use placeholders — never a real secret (P14).

`SignedEntryVector.meeting-42.json` is a signing vector with a fixture token (not a minted
MeetingToken): `validate.mjs` re-signs it in Node, and the bot's `transcript-redis.test.ts` and
meeting-api's `test_segment_entry_auth.py` pin the same signature. `StreamEntry.signed.json` is the
entry that vector produces. The `Feed*` goldens and `FeedEntry.*` are the per-meeting feed: a
`FeedEntry`'s `payload` must parse to a `FeedTranscription`, a `FeedRetract`, a `FeedSessionStart` or a
`SessionEnd`.
