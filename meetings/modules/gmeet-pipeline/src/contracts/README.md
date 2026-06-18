# gmeet-pipeline/src/contracts

[`transcript-v1.ts`](transcript-v1.ts) — the pipeline's **typed view** of the sealed
`transcript.v1` contract. SSOT is the JSON Schema at
`meetings/contracts/transcript.v1/transcript.schema.json`; the replay golden validates
the pipeline's actual output against that schema, so this view cannot silently drift.
