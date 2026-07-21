### Fixed
- **Mixed lane (Zoom/Teams/Jitsi): every sentence was stored twice.** The forming tail was
  published under `turn:N:p<i>` — indexed off the unconfirmed slice, so it also renumbered as the
  turn advanced — while its confirmation went out under `turn:N:<seq>`. Since the store upserts on
  `(meeting_id, segment_id)`, those are two identities for the same words: the draft row was never
  replaced and the reader saw each sentence doubled (a real Jitsi meeting stored 6 rows for 3
  spoken sentences). The forming tail now carries the ids it will confirm under, so a draft is
  repainted in place — one row per sentence, matching what the gmeet lane already did.
