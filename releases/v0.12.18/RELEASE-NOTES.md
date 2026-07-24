# v0.12.18 — dependable joins and truthful operator failures

v0.12.18 tightens the paths that decide whether a meeting bot really joined,
is still alive, and failed for a reason an operator can act on. It also makes
the MCP front door stream correctly, fixes Lite startup, and removes several
false-success states from bot creation and deployment.

## Meeting reliability

- Google Meet lobby bots are no longer reaped at five minutes while still
  legitimately waiting for admission (#862).
- Meet host denial wins over ambient reCAPTCHA and terminates with a typed,
  non-retryable reason (#840).
- The browser locale is deterministic (`en-US` by default), with its resolved
  language recorded for diagnosis (#856). The English lobby path was walked
  live; arbitrary non-English joining is not claimed.
- Teams no longer treats generic alert UI as evidence that the bot was
  removed; the staged bot stayed active for 207 seconds under the live removal
  monitor (#600).
- Mixed-lane turns without a resolved participant name now use the stable
  `Speaker` label instead of leaking the internal `seg_N` cluster identifier
  (#890). Human name binding remains a later attribution-quality step.
- Teams authentication redirects and non-admitted bot deaths now preserve
  their real causes instead of degrading into admission timeouts or
  `reason: None` (#915, #926). These external failure shapes are
  machine-proven; neither recurred during the release witness.
- Stop during pre-admission now reaches the phase-aware withdraw path and
  cleans up the workload (#889). Meet's host-side knock can still persist;
  that distinct visible withdrawal gap remains
  [#839](https://github.com/Vexa-ai/vexa/issues/839).

## Truthful APIs and operator behavior

- A bot workload that never starts no longer yields false `201`; `POST /bots`
  returns `502` with the cause and marks the meeting failed immediately (#718).
- Invalid native meeting IDs containing URL/query characters fail at intake
  with `422`; callers should use `meeting_url` and `passcode` (#892).
- Meeting API remains available for durable reads during Redis loss and
  reports Redis degradation on `/health`; Redis-dependent Stop fails narrowly
  and retryably (#809).
- Redis keyspace reconciliation is no longer on the ten-second collector hot
  loop, removing the known `/health` saturation amplifier (#893).
- The webhook introspection capture is bounded, removing its unbounded
  in-process retention source (#803).

## MCP, webhooks, and identity

- MCP streamable HTTP is available through the authenticated gateway `/mcp`
  front door. Sessioned `GET /mcp` opens and keeps its SSE stream instead of
  being buffered into a timeout (#795, #921).
- Admin token mint honors JSON `scopes`, keeps query compatibility, and refuses
  unsupported body fields loudly (#922).
- Core now records recent webhook delivery outcomes in a per-user,
  secret-safe ledger exposed at `GET /user/webhook/deliveries` (#841 core
  half). The dashboard does not display that ledger yet; the consumer remains
  open in v0.12.20.

## Deployment

- Helm migrations use the same pinned image tag as the deployed release
  (#900).
- Admin API retries transient database DNS/connect races with bounded backoff
  (#901).
- Vexa Lite pins all service environments to Python 3.12, restoring reliable
  admin API startup and first-run key provisioning (#927).
- The no-STT contract is documented truthfully: transcription is required by
  default and missing configuration fails loudly; capture-only is an explicit
  `transcribe_enabled=false` choice (#532).

## Upgrading

From any v0.12.x release, pin `IMAGE_TAG=v0.12.18`. The guarded `:v012`
pointer moves only after the published image set, value gate, and release
witness are green. There is no database schema migration in this batch.

Behavior refinements to note:

- token JSON bodies reject unknown fields with `422`;
- native meeting IDs carrying URL characters reject with `422`;
- bot workload start failure now returns `502`, not `201`;
- `/mcp` and `/user/webhook/deliveries` are new gateway surfaces.

## Artifact identity

The stable tags are same-byte aliases of the release candidate that passed
published-image validation and the hosted stage/production witness. They were
not rebuilt after the witness.

| image | `:v0.12.18` digest |
|---|---|
| `vexaai/v012-admin-api` | `sha256:49a8bd29268250cb37976c3170fde41c960f00d55f31ef4a42e17062b968b114` |
| `vexaai/v012-runtime` | `sha256:653fcd1a8edbc925a174a55fe34863d414c56c8ef367a94b449f89a5a3a5c4e1` |
| `vexaai/v012-agent-worker` | `sha256:d4c637ca1ca9c05ff6de852778299da665374ed9d59115088aa030d90212bf15` |
| `vexaai/v012-agent-api` | `sha256:af4f2bf2008ed2c910904e82155e06d3b0e747d04d7c7ccb9f14208b17c49bae` |
| `vexaai/v012-meeting-api` | `sha256:d1db225472d54f0b087e1b7a342c465b01b7e7f042372a730d3b32e0c6836f4a` |
| `vexaai/v012-gateway` | `sha256:4b9bd9d6d33f4f2ec8df0b9530b02942482c0399ec71f39722fb06f48aae55df` |
| `vexaai/v012-mcp` | `sha256:72158d83293ce48c1cfd5c9bd8e2762e72a2da63d962fdbdbf7fe9b09daa8755` |
| `vexaai/v012-terminal` | `sha256:a668ad43e225364422b225939fad057bff33db8b5bb3d6c341e1b625c5bde008` |
| `vexaai/vexa-bot` | `sha256:9442a44558fd48950208cbef40673cc7a0b2feb41f380964fc74a0e25bf18fae` |
| `vexaai/vexa-lite` | `sha256:2d5c11d0fd4a194ce1085ac8624ed84116dd13c29b608cc8dac1b295979b3fb9` |

Hosted production directly held the admin API, runtime, meeting API, gateway,
and bot bytes. Agent worker, agent API, MCP, Terminal, and Lite are proven by
the published-candidate validation run; they are not claimed as hosted
production workloads.

## Known boundaries

- Host-visible graceful lobby withdrawal remains #839.
- Dashboard webhook Delivery History remains #841.
- Teams auth-redirect and Zoom non-admission reason fixes have exact-head
  machine evidence but no live recurrence in the witness window.
- Jitsi hosted meeting detail remains #937.
- Real audio→words is witnessed live; the standalone machine wav→words oracle
  remains deferred.
- Hosted transcription currently has a functional Cloudflare fallback, while
  primary health remains false and direct BBB was not exercised.

## Credits

Contributors: Joseph Yaksich, Felix-Ayush, and Dmitry Grankin.

Reporter credit: Valerie Phoenix (#927).

Community PR validator: Dmitry Grankin.

Release witness: Dmitry Grankin, corroborated by dashboard, bot, meeting API,
Redis, Postgres, and Kubernetes evidence.

## Delivery guarantees

| # | Guarantee | Evidence |
|---|---|---|
| 1 | Users pull the bytes that were proved | exact candidate map; stable alias digest equality |
| 2 | Compose, Lite, and Helm install paths work to their declared scope | [published-candidate validation](https://github.com/Vexa-ai/vexa/actions/runs/30036135103) |
| 3 | Real audio produces speaker-attributed words | hosted stage and production human witness; standalone wav→words remains unclaimed |
| 4 | Fresh empty VM works | not claimed; no fresh-VM leg was run |
| 5 | Existing behavior remains covered | v0.10 compatibility, bot-spawn, Compose, Lite, Helm, and arm64 validation |
| 6 | Each image contains what its name promises | image-identity validation |
| 7 | A human witnessed the assembled value | hosted stage witness plus production meetings 24740/24741 |
| 8 | Every batch change has acceptance evidence | 21-PR value map and exact-head checks |
| 9 | Notes state coverage boundaries | this document |
| 10 | Moving pointers change only after validation | release value/witness gates plus the `release-promote` environment |
