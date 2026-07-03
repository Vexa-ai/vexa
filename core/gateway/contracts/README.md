# gateway/contracts — the public-surface wire contracts

Contracts owned by the **gateway** domain (the world-facing edge), discovered + sealed by
the same machinery as every other `<domain>/contracts/<name>.v<N>` (gate:schema runs
`validate.mjs`; gate:contract-version freezes the `*.schema.json` bytes via
`contracts.seal.json`).

| Contract | Between | Status |
|---|---|---|
| [`api.v1`](api.v1/) | api-gateway / mcp / meeting-api → the world | **frozen ≡ vexa `main` api-gateway 1.5.0** (OpenAPI 3.1) |
| [`ws.v1`](ws.v1/) | api-gateway `/ws` → the world (live transcripts/status/chat) | **frozen ≡ vexa `main` `/ws`** (pinned by main's G5 WS gate test) |

The rule (MANIFEST §2): a back-compatible change re-seals (`pnpm seal:contracts`); a
breaking change opens `api.v2`, leaving `api.v1` until no consumer pins it.
