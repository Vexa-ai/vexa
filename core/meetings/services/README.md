# meetings/services — the hosts

Backend **hosts** that compose the meetings bricks (`modules/`) into deployables. A service
imports bricks by their published `@vexa/*` packages + contracts — never another brick's
internals, never another domain (`gate:isolation`, `gate:graph`). Services are `"private"`
(not published libraries), so they're exempt from `gate:exports`.

| Service | What | Composition |
|---|---|---|
| [desktop](desktop/) | the all-in-one host (gmeet subset) | ingest WS → `gmeet-pipeline` → real STT → gateway `/transcripts` · one process |
| _bot_ | containerized capture + join + pipeline | _(3.3+)_ spawned by the runtime kernel |
| _meeting-api_ | cloud control plane | _(3.4)_ `POST /bots` → runtime kernel |

The same bricks compose three ways — desktop (one process), bot (container), cloud (split
services). That's "microservices, each internally a modular monolith."
