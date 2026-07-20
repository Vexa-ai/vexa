# api.v1 — the public surface, frozen to vexa **main**

The REST + WS + MCP API the world builds against (eval harness, dashboard, SDKs, any
client). **`api.schema.json` is the OpenAPI 3.1 document emitted by vexa `main`'s
`services/api-gateway` — captured verbatim** (`info.title = "Vexa API Gateway"`,
`version = 1.5.0`) and **sealed** in `contracts.seal.json`, so every v0.12 service
(meeting-api, dashboard, bot) builds against the **real production surface**, never an
invented shape. This is the contract-first anchor: pin the API to main *before* the
services, or they drift (the rough meeting-api's hand-rolled 4-op shape is the cautionary
tale — it must be reconciled to the shapes below).

## What's pinned
- **Identity:** OpenAPI `3.1.0` · title `Vexa API Gateway` · version `1.5.0`.
- **Core paths** (asserted by `validate.mjs`): `GET/POST /bots`, `GET /bots/status`,
  `DELETE /bots/{platform}/{native_meeting_id}`, `PUT .../config`, `POST .../speak`,
  `GET /transcripts/{platform}/{native_meeting_id}`, `GET /recordings`,
  `GET /recordings/{recording_id}`, `GET /meetings`.
- **Shapes** (goldens conform to the frozen `#/components/schemas/*`): `MeetingResponse`,
  `MeetingListResponse`, `TranscriptionResponse`, `TranscriptionSegment`,
  `BotStatusResponse`. Canonical `MeetingStatus` enum =
  `[requested, joining, awaiting_admission, active, needs_human_help, stopping, completed, failed]`.

## The seam (what conforms to this)
| Consumer | How |
|---|---|
| `meetings/services/meeting-api` | its routes + response models MUST match these paths/shapes (rough cut owes reconciliation) |
| `clients/terminal` | proxies `/bots`,`/meetings`,`/transcripts`,`/recordings` — the shapes here |
| `meetings/eval` | polls `GET /bots` (`meetings[].status`), reads `GET /transcripts/{p}/{n}` |

## Auth error contract
The gateway authenticates `X-API-Key` against admin-api and **fails closed**. The exact statuses:

| Condition | Status | Body `detail` |
|---|---|---|
| Missing `X-API-Key` header | `401` | `Missing API key` |
| Invalid / unknown / revoked key | `401` | `Invalid API key` |
| Valid key, scope not permitted for the route | `403` | `Insufficient scope for this endpoint` |
| Expired key | `401` | `Invalid API key` |
| Admin route, missing/invalid `X-Admin-API-Key` | `403` | `Invalid or missing admin token.` |

Clients should treat **401 = re-authenticate** (key bad/absent/expired) and **403 = authorized but not
permitted** (wrong scope, or admin route). The stack-test conformance suite asserts these.

## Re-verify / re-capture
The frozen doc was captured from the deployed main (`api.cloud.vexa.ai/openapi.json`,
version-matched to `git show main:services/api-gateway/main.py` = 1.5.0). To re-verify drift:

```bash
curl -s https://api.cloud.vexa.ai/openapi.json | diff - gateway/contracts/api.v1/api.schema.json
node gateway/contracts/api.v1/validate.mjs
```

A **deliberate** main API bump → re-capture `api.schema.json` + `pnpm seal:contracts` on a
`lane:contract` human-reviewed PR (a breaking change opens `api.v2`, leaving v1 until no
consumer pins it). The frozen bytes are the spec; never edit them to match an implementation.

One deliberate spec-bug correction has been applied to the captured bytes (#62 → #531): the
1.5.0 capture referenced an undefined `APIKeyHeader` security scheme on all 61 secured
operations — invalid OpenAPI, so Swagger UI could not attach the key and generated clients
never sent it. The per-operation `security` references were repointed to the document's own
defined schemes (`ApiKeyAuth` for client ops, `AdminApiKeyAuth` for the `/admin/{path}` ops —
the headers the runtime already honors); no other bytes changed, and the seal was re-issued on
a `lane:contract` review per the policy above. This was not an implementation-appeasing edit:
the document was internally invalid, and `validate.mjs` now pins referential integrity
(referenced ⊆ defined) so a re-capture can never re-import the bug.

## What "sealed" enforces — and what it does not (#591)

The seal in `contracts.seal.json` freezes the **bytes** of `api.schema.json` — it guarantees the
*document* cannot silently change. It does **not**, by itself, guarantee the shipped services still
*serve* the routes the document declares: 0.12 renamed/dropped six sealed endpoints and the
one-directional conformance (impl ⊆ contract) stayed green. `gate:contract-conformance` now closes
that gap with the **reverse** check (contract ⊆ impl) plus a **golden-shape** check:

- **Implementation presence (contract ⊆ impl).** For every `(path, method)` this document declares,
  the union of the shipped gateway edge + meeting-api must register it — or the route is recorded in
  the audited **[`KNOWN_GAPS.json`](KNOWN_GAPS.json)** ledger (see below). A sealed route renamed or
  dropped and *not* audited turns CI RED, listed by name.
- **Response shapes (golden-driven).** The frozen golden examples (`golden/*.example.json`) are driven
  against the **real** responses, so a renamed field (e.g. `BotStatusResponse.running_bots` →
  `running`) fails — not just a removed path.

**`KNOWN_GAPS.json` is the audited exception path.** It records, with a reason + issue link for each
row, the sealed routes the 0.12 core genuinely cannot serve yet (`known_gaps`: e.g. `POST .../chat`
send, `POST /meetings/{id}/transcribe`, the bot-command avatar/screen/speak routes) and the prefixes
owned by *other* services (`owned_elsewhere`: `/admin` → admin-api, `/api` → agent-api, `/mcp` → the
mcp service, …). The gate prints every entry loudly (`SEALED-BUT-WAIVED` / `OWNED-ELSEWHERE`) on each
run. Adding a row is a **deliberate, diff-visible change in this sealed dir** — and because the file is
not a `*.schema.json`, it does **not** move the api.v1 seal hash, so the seal stays frozen while the
gap lives in the ledger. Closing a gap means implementing the route (delete its row) or re-versioning
the contract in a `lane:contract` PR.
