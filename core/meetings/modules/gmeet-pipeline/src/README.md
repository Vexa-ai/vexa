# gmeet-pipeline/src

Front door [`index.ts`](index.ts). [`gmeet-pipeline.ts`](gmeet-pipeline.ts) is the
channel router + turn/glow binding; [`speaker-streams.ts`](speaker-streams.ts) is the
per-stream sliding-window buffer + LocalAgreement confirm;
[`hallucination-filter.ts`](hallucination-filter.ts) drops junk (phrase lists in
[`hallucinations/`](hallucinations/)); [`log.ts`](log.ts) is the injectable logger;
[`contracts/`](contracts/) is the TS view of the sealed `transcript.v1`.

`*.test.ts` are the offline goldens (`gate:node` runs them) — the hallucination filter
and the **replay conformance** gate.
