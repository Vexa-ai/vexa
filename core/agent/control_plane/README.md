# agent · control_plane

The agent control plane: the FastAPI app (`api.py`) and orchestration that dispatches work to workers and tracks meeting lifecycle. Owns request handling, the one-time routine migration into flows, transcription watching, and event relay — distinct from the `worker/` that runs a single agent workload.

## Modules

Every module in this folder, by concern. `routers/` holds the routes, one module per owner (see
[`routers/README.md`](routers/README.md)).

**The HTTP app**
- `api.py` — `create_app`: builds what the routes are built out of (resolvers, stores, peer clients)
  and includes the routers.
- `api_shared.py` — what the routes share: the session index, live meetings, the unit inbox, SSE
  framing, the chat's grounding and context bundle.
- `unit_faults.py` — why a chat's queue is not moving (P18): the typed runtime fault a failed spawn
  leaves on the unit (`unit:{id}:fault`, agent-api its only writer). The pending list marks queued
  rows `blocked` by it and the chat's SSE relay answers an attach with it as an `error` event; it
  stops blocking the moment the worker takes anything. A refused dispatch answers 502/503 with
  `{detail, fault}` on every door, never a 500.
- `bodies.py` — every named request body, as a pydantic model (the OpenAPI schema names).
- `ceiling.py` — where a caller may act and whether a person is in the loop: the delegated
  dispatch's workspace ceiling (`require_in_ceiling`, `write_slug`, `reads_within`), the one
  person-in-the-loop rule (`is_delegated`, `is_unwatched`, `require_person`, `REFUSAL`), and the
  one logged refusal.
- `route_policy.py` — which verbs need a person in the loop: the `verbs` rows of
  `core/agent/routes.v1.json` marked `"person": true` (routes.v1). `PERSON_GATE`, an app-level
  dependency, applies `require_person` to the route a request matched. A row naming a route the app
  does not serve refuses the boot, and so does a destructive or membership route with no flag (any
  DELETE, and any other write whose path has a `MEMBERSHIP_SEGMENTS` segment, less the internal-only
  `NOT_A_WORKER_DOOR`). It also holds D3, default-deny for shared tokens. Every row and verb
  declares `"shared": "allow"|"refuse"`. `SHARED_GATE` refuses a shared or workspace-principal
  caller, with `SHARED_REFUSAL`, on every route not declared `allow`. `assert_shared_declared` makes
  the boot (and `tests/test_shared_routes.py`) fail on a served route with no declaration.
- `workspace_principal.py` — D2: asks identity (`POST /internal/principals/workspace`) for the
  workspace's numeric principal a room's turn runs as (`VEXA_ROOM_WS_PRINCIPAL`, on by default;
  needs `VEXA_ADMIN_API_URL`); cached, fails closed — no id, no toolbelt.
- `version.py` — what is serving, one unauthenticated fact.
- `admin_panel.py` — read-only infrastructure and meeting-pipeline introspection for the hidden admin
  panel.
- `config_preflight.py` — the boot-time `config.v1` validator (ADR-0026); `config_test.py` — the
  Settings → Models "Test" probes.

**Trust at the door**
- `identity_token.py` — gateway-identity.v1: verifies the identity the gateway signed onto a request
  (vendored byte for byte; fact `identity-token`).
- `dispatch_sink.py` — who may hand agent-api a dispatch to run.
- `broker_assertion.py` — credential-broker.v1's one Python signer and verifier;
  `broker_client.py` — the one HTTP client to the credential broker.

**Peers.** agent-api calls meeting-api, admin-api and flows-api from several modules, each as the
caller or with the internal secret, never with a credential of its own:
- `peer_lookups.py` — meeting access and transcript (meeting-api) and email → subject (admin-api), for
  the live feed's ownership gate and the post-meeting room; fails closed.
- `person_resolution.py` — one Person, many addresses (PRD 0.13.4 D35): which CRM Person each
  account resolves to by its verified address (crm-api `POST /internal/persons`, internal secret,
  `CRM_API_URL`). The @ picker and the People strip list a Person once ("2 accounts"), a chat or
  workspace grant reaches only the accounts the person confirmed as theirs (never an unconfirmed
  link), invites and nudges go to `primary_address`, and after
  the person's own merge card `union_memberships` opens each workspace to every login. Profiles
  (a claimed record's person-owned fields) are read through it under the directory rule
  (`GET /api/people/{person}/profile`) and for a published public page (`GET /api/profiles/public/{handle}`,
  no account). Off without a CRM; fails toward fewer grants.
- `meeting_mint.py` (meeting-api annotate), `schedule_digest.py` (meeting-api meetings list),
  `global_layer.py` (admin-api is-admin), `publish.py` (flows-api events), the membership index
  `api.py` builds (admin-api), `routers/clock.py` and `routers/admin.py` (admin-api), and
  `admin_panel.py` (health probes).

**Dispatch and workloads**
- `dispatch.py` — a unit.v1 DISPATCH becomes a runtime.v1 agent container.
- `events.py` — event.v1 ingress → unit.v1 DISPATCH.
- `routines.py` (the routine compiler), `workspace_routines.py` (workspace-authored routines onto the
  runtime scheduler), `routine_resign.py` (re-arms routines armed before dispatches were signed).
- `workload_redis.py` — the Redis user an agent worker connects as.
- `model_endpoint.py` — whether a subject's model config points elsewhere, and the operator gate on
  where.
- `model_proxy.py` — the model-provider key proxy (H5/W1): `confine` takes a dispatched worker's model
  route out of its env (sealed in Redis, a per-turn token in its place), and the proxy app (a process
  of its own, port 8102) forwards a worker's model and toolbelt requests with the real credential.
- `model_providers/` — the operator's model catalog (`VEXA_MODEL_CATALOG`, `models.v1`) and the
  provider port each model resolves through, one adapter per provider kind (ADR-0043). The dispatch
  (`dispatch.apply_model_route`, `route_env`) is the one place a resolved route becomes a worker's
  environment; see [`model_providers/README.md`](model_providers/README.md).

**Meetings**
- `bridge.py` — the meeting WebSocket → agent bridge.
- `transcription_watcher.py` — the in-process watch over the live transcript.
- `meeting_mint.py` (a meeting's page exists from the moment the meeting does), `meeting_note.py`
  (where its report lives on a desk), `meeting_room.py` (the attendees' read-only mounts after it),
  `meeting_highlight.py` (what it has named, and which names have a page), `meeting_terms.py` (the
  annotation layer over its transcript), `meeting_steering.py` (per-state preambles for meeting chat
  turns), `schedule_digest.py` (the schedule as a prompt block), `meeting_link.py` (a link to a
  meeting's notes, answered as the meeting: its title and date, for a reader who may see it).
- `meeting_bundle.py` (+ `routers/meeting_bundle.py`) — a meeting's workspace tree and page in and out of a
  meeting-bundle.v1: the parts archive for an owner's export, and the restore after an import. The
  codec is `shared/meeting_bundle_codec.py`, vendored verbatim from the contract.

**Workspaces**
- `workspace_store_service.py` — the s3 workspace backend at agent-api's edge (WS-S3): boot self-test and hydrate, the durable-before-answer middleware, each unit's recorded mounts.
- `workspace_reader.py`, `workspace_ids.py` (id → where it is now), `workspace_purpose.py`,
  `system_mounts.py` (the two system tiers of the mount stack), `global_layer.py` and
  `global_seed.py` (the company layer in `_global`, and its seed), `link_resolver.py`.
- Membership: `workspace_membership.py` (below), `membership_acts.py` (adding a member is a
  conversation).
- Git: `workspace_attach.py`, `workspace_import.py`, `workspace_publish.py`, `workspace_git_sync.py`,
  `repo_ref.py`, `deploy_keys.py`, `workspace_credentials.py`, `git_credentials.py`,
  `git_secret_store.py` (Git credentials, kept in the credential broker), and `legacy_git_store.py`
  (read-only: moves what older builds kept on this volume into the broker).

**Chat, pages and onboarding**
- `scaffolds.py` (what the agent knows and the UI shows at the moment a person arrives),
  `preset_library.py` (the ask library), `chat_intents.py` (a button on a page → its preset),
  `front_page.py`, `flow_pages_watch.py`, `claims.py` (the claim book), `onboarding_research.py`
  (the account-scoped research cursor), `connection_setup_schema.py` (a custom-service setup
  proposal's shape).

**Conversations — the session store (ADR-0048).** Chats are kept here, apart from every workspace;
workspaces hold knowledge only, and `_system` holds who the person is (`identity.md`) and settings,
never a chat.
- `session_store.py` — the store: metadata in agent-api's own database (`agent_session*` tables, its
  own login role), each transcript version one object in its own bucket (`session_blobs.py`). A new
  version names its base and is accepted only while that base is the head.
- `session_sync.py`, `session_turn.py` — a turn's bundle in and out; with the store on the worker
  keeps no conversation under a mount (`VEXA_SESSION_STORE=1`: the carried bundle, or a private folder
  under its HOME).
- `session_access.py` (who may read a chat and what a shared turn may mount — the leak rule),
  `session_fork.py` (sharing a chat starts a new shared one), `session_schema.py` (the DDL, run by the
  migrations path, never by agent-api).
- `session_migrate.py` — the operator's one-shot that moves old-layout chats out of `_system` and the
  desks into the store: copy, read back, `--clean --retire` moves every old file into
  `<workspaces>/.sessions-retired/<run>/` (no mount names it), `--restore` puts them back.
- The agent reads other chats only with `chats_search` and `chat_read` (`routers/chat_read.py`).

**Rooms: who is in them and how people get in (0.13.4 S4).** A chat has one person; a room is shared.
- `room_membership.py` — D36: the one authority on who is in a room, installed in front of
  `SessionStore.role_of`. An open room's members are its workspace's (derived); a closed room's are the
  identity registry's `room:<id>` rows. The participant rows are a projection (read marks, the rail,
  who opened it). `ringable` is who a room call rings.
- `pair_rooms.py` — FD4: the room of two behind "Message", backed by a hidden pair workspace
  (deterministic id, both owners, a third person refused, never in a list or a mount).
- `room_switches.py` — "Vexa in this room" and "Transcribe calls" (7hh): either person turns off and
  both turn on in a room of two; owners in a bigger room; a workspace DENY line locks one.
- `room_ring.py` — a ring: a live frame plus a `call_started` notice that expires.
- `rooms_overview.py` — a workspace's rooms as its reader may see them (`GET /api/rooms/list`, tool
  `rooms_list`): people by name, a live call, recurring meeting links. A shared turn sees only its own
  workspace's open rooms, with no meetings.
- `shared_chat_migration.py` — D10: existing shared chats become a room of two or a closed room, once.

## A worker's delegation token ends with its unit

`delegation_revocation.py`. Each dispatch's delegation token (`shared/delegation.py`) is recorded
against its unit before the spawn (`vexa:delegation:unit:<unit id>`, jti → exp) and held live
(`vexa:delegation:live:<jti>`, expiring with the token — identity admits a token only while that key
exists), and a token that cannot be recorded is withheld. The reaper thread compares the recorded units with the runtime's live
workloads every 30 s; a unit the runtime no longer runs — completed, idled out, stopped, failed, or
never started — has its tokens written to `vexa:delegation:revoked:<jti>` with their remaining
lifetime and their live keys deleted, which identity's `/internal/validate` refuses. A unit id is reused across warm windows, so
the dispatch that starts a unit's next container also revokes the previous container's token when the
runtime reports it ended. Tokens younger than 120 s are never revoked (their spawn may still be on
its way), and a sweep that cannot read the runtime revokes nothing. The token's lifetime,
`VEXA_MCP_DELEGATION_TTL_SEC`, defaults to the chat warm window plus one turn (1800 s).

`delegation_refresh.py`. The same sweep keeps a LIVE unit's token fresh: once half of its life has
passed, agent-api mints a new one for the same person, regime, ceiling and target (new `jti`),
from its own record of the current token (`vexa:delegation:current:<unit id>`), records it for
revocation and publishes it at `unit:<id>:delegation`, which the worker's Redis user may read and not
write (`workload_redis.py`). The worker reads that key before every turn, write-back and job and
rewrites its MCP attachment (`worker/engine.py` `DelegationRefresh`), so a unit kept warm keeps its
tools; it holds the token outside its environment, so no harness subprocess inherits it. The replaced
token is not revoked while its unit runs: a turn that started with it keeps it until its own `exp`
(900 s after the refresh at the default). A turn that runs longer than that has its Vexa tool calls
refused, and ends with a typed fault (`source: "vexa-tools"`, `kind: "access_expired"`;
`worker/tool_access.py`) rather than quietly without its tools. A unit the runtime no longer runs is
never refreshed, and every token it holds, refreshed or replaced, is revoked on the first sweep after
it ends.

## Workspace membership + invites + roles (Lane M — the access layer for shared workspaces)

> **The full workspace + collaboration model (tiers, personal, sharing, live collaboration, deferred)
> is documented in [`docs/docs/core/workspaces.mdx`](../../../docs/docs/core/workspaces.mdx).** This section is the Lane M
> membership/invite mechanism.

`workspace_membership.py` is the access layer for shared workspaces. **Three roles:** `owner >
contributor > viewer`, said to people as owner, contributor and reader. An owner writes and adds or
removes members; a contributor writes; a reader reads. An invite may be minted for any of the three
(`INVITABLE_ROLES = ROLE_WORDS`). `POST /api/workspace/invite` (one address, the agent's
`workspace_invite`) is owner-only; `POST /api/workspace/invites` (a link) needs owner or contributor;
removing members and changing roles is owner-only. **An invite never outranks its sender**, an
**owner is made only by the owner-gated act** (`membership_acts.invite`, `owner_act=True`), and
`max_uses ≤ 1000`, expiry ≤ 365 days on every mint door (PRD 0.13.4 D5). Redeem re-checks the sender's
role at that moment, so a demoted sender's links grant no more than they now hold, and accepting
never queues a profile — the person's write target is unchanged (D26).

**Invites by email (PRD 0.13.4 S2).** `workspace_invite` may name a `room` of the workspace (the
inviter must be in it); the mail says "<room> in <workspace>" and the accept joins the room. When the
deployment says it delivers mail, a restricted one-address invite gets `signin_until`
(`mark_invite_signin`, 7 days) and its link is not handed back to the inviter: that link alone signs
its address in, once, through the terminal's join POST and `POST /internal/invites/signin-claim`
(`claim_invite_signin`, internal tier, under the workspace lock). A preview never spends it. An
invite notification stores `invite:<ws>/<id>`, never the token (M2; legacy rows are rewritten at
start by `Notifier.scrub_invite_tokens`). With Redis, `_ws_lock` also takes `RedisWorkspaceLock`
(`vexa:ws-lock:<ws>`, M6), so a second replica cannot spend a once-only link twice.

**Two stores, written together (the policy store is authoritative, the index is derived):**
- **Authoritative** — the WORKSPACE REGISTRY in identity's database (`workspaces`, `memberships`,
  `invites`; admin-api the one writer, MIGRATION-0009, PRD 0.13.4 D5), reached through the
  `PolicyStore` port (`shared.adapters.AdminApiPolicyStore`, `/internal/registry/*`). Without
  `VEXA_ADMIN_API_URL` the file backend answers (`<store-root>/.policy/<ws>/` + `.invites/`), outside
  every mount. `read_policy` / `write_policy` / `read_invites` are the only readers and writers.
  Legacy records (interim files, else the tree's `policy/*.json`) are adopted once — at start
  (`migrate_policy_store`) and on first read; `VEXA_POLICY_LEGACY_READ=0` stops it. A store that does
  not answer refuses, `503 policy_unreadable` (FD3). The workspace keeps a platform-written MIRROR at
  `policy/members.json` (audit trail, the worker's reader count) that nothing reads for access;
  evaluator rules stay in git at `policy/rules.json`, platform-only. Invites keep only the sha256 **hash** of each token, with
  `{id, role, mode, allowed_emails, expires_at, max_uses, uses, revoked, …}`, in the same store;
  see the write-guard section below for why they left the tree first.
- **Index** — `users.data.memberships[]` (`[{workspace_id, role, added_at}]`) for "workspaces shared
  with me". agent-api has no DB, so this is reached through the injected `MembershipIndex` port
  (real adapter → identity admin-api `/internal/users/{id}/memberships`; an in-memory fake in tests,
  and the composition-root default when `VEXA_ADMIN_API_URL` is unset — the git file stays authoritative).

**API surface** (`/api/workspace/*`, gateway-fronted, subject = `X-User-Id`):
- `POST /invites` (owner/contributor) → mint a scoped invite; token returned ONCE. Body carries
  `role`, `expires_in_sec`, `max_uses`, and the ACCESS MODE: `mode: open|restricted` +
  `allowed_emails[]` (AMENDMENT 5). `open` = anyone-with-link (authenticated) redeems; `restricted`
  = only an authenticated user whose VERIFIED email (`X-User-Email`, gateway-injected from the
  resolved key) is in `allowed_emails`.
  **ADDRESSES BIND** (Vexa-ai/vexa#1635): `mode` is unstated by default and `allowed_emails` decides
  it — naming addresses used to store them and then ignore them unless `mode="restricted"` came too,
  which turned a mint for one person into a link anyone holding it could redeem. Asking for both at
  once is refused (400) rather than resolved in a direction the caller cannot see.
  The response also carries `invite_url` — the whole link, composed HERE on the deployment's declared
  public app URL (`VEXA_UI_URL`) plus `join_path` (`/join`), because only the deployment knows where
  the person's terminal is. A client that composed it from its OWN host is what sent the founder to
  `rig.dev.vexa.ai/join?i=…` and a *"not found"*. Unset ⇒ `invite_url` is null and
  `invite_url_refused` names the key.
- `GET /invites/preview?token=` — NO SUBJECT, deliberately: the consent card renders before sign-in,
  gated by the token itself. Answers the workspace's name + id, the role, `shared_by`, validity and
  reason, and — for a bound invite — `restricted_to`, the address the terminal's `/join` page
  prefills and locks. Read-only (no registry sync, no use consumed); 404 for a token matching
  nothing, so it never enumerates workspaces.
- `POST /invites/accept` (any logged-in user; POST-AUTH redeem, no anonymous/guest) → validate
  (hash lookup, not expired/revoked, uses<max_uses, AND mode==open OR verified email listed) → grant
  membership (both stores) → increment uses. Idempotent per user (double-accept = one membership).
  The token carries no workspace id — it is resolved by hash scan over shareable workspaces.
- `DELETE /invites/{id}` (owner/contributor) revoke · `GET /invites`, `GET /members`
  (owner/contributor) · `DELETE /members/{subject}` (owner) · `POST /members/{subject}/role` (owner,
  the "change read/write permissions" DoD item) · `GET /shared` = the "shared with me" listing.

**Role enforcement** — `require_role(root, workspace_id, subject, min_role)` (owner > contributor >
viewer) is the ONE gate every shared route uses. Under single-rank: **mint/revoke/list invites +
list members = any member** (`require_role("contributor")`); **unshare / remove member / change role
= creator only** (`require_role("owner")`). The system tiers + reserved/own-private slugs are never
shareable (`RESERVED_SLUGS` = `sys` / `_system` / `system` / `_global` / `global` / `seed` /
`seed-prev`; `ensure_workspace_shareable` refuses them and the private baseline).
**DEFERRED DECISION** (owner's call): whether to also offer an owner-restricted invite mode — see
`docs/docs/core/workspaces.mdx`.

**`is_member(root, workspace_id, subject) -> role|None`** is the seam Lane A calls for mount-resolution
and transcript-subscribe-by-membership. This lane provides membership DATA + APIs only — it does NOT
touch the mount set / dispatch.

### policy/ is PLATFORM-WRITE-ONLY (the write-guard mechanism, Q3)

Since 0.13.4 (D5) three more walls stand in front of the guard below: `policy` is in
`workspace_paths.RESERVED_DIRS`, so every caller route refuses it (the page editor answers 403); a pull
whose remote changes `policy/` is refused (`workspace_git_sync._refuse_remote_policy`); and the tree is
only a mirror — authority is the policy store. A policy CHANGE (visibility, connections) goes through
`policy_decide.py` (D7 stub, `POST /internal/policy/decide` → `{decision, rule@sha}`, deny beats allow,
fail closed) — applied only for the workspace's owner in a browser session. **Agents change policy
with permission** (`policy_proposals.py`): `POST /api/workspace/policy/proposals` records a `policy`
proposal (the exact change + reason) in the session proposal store, owned by the workspace's owner;
`…/{id}/approve` (owner, browser session, never delegated) applies it through the evaluator and the
registry with `Approved-by` / `On-behalf-of` trailers; `…/decline` changes nothing. S5's signed card
wraps this same proposal.

`policy/` (members.json) is written ONLY by the control plane
(`workspace_membership.policy_commit` — stages + commits just `policy/` with the platform identity as
committer, never sweeping the agent's tree). An **agent turn must never modify `policy/`**. Enforcement
lives in the worker's turn-commit path (`llm/ports.run_harness_turn`): `_revert_policy_writes(work)`
runs right before `git add -A` — it restores any changed `policy/` path and deletes any untracked
`policy/` add, emitting `{"type":"policy-reverted","paths":[…]}`. So a turn's legitimate (non-policy)
writes still commit while a policy tamper is reverted before it can land. (Chosen default per plan Q3:
post-turn validation + revert.)

**It restores to an ANCHOR, not to the pre-turn HEAD (Vexa-ai/vexa#1645).** The anchor is the sha
captured before the turn, advanced over the platform's own policy commits made *while the turn ran*
(`llm/ports._policy_anchor` — committer is the platform, subject opens `policy: `, nothing outside
`policy/` touched). Restoring to the pre-turn sha instead deleted every membership and invite the
platform wrote during a turn: the founder minted an invite, the turn's write-back removed it one
second later, and the join page said the link was not valid. The guard also no longer purges and
re-checks-out the whole subtree — it touches only the paths that actually differ, so a turn that never
went near `policy/` writes nothing there at all.

### Invites do NOT live in `policy/` — they live outside every workspace mount

`<store-root>/.invites/<workspace_id>.json`, resolved through the single door
`workspace_membership.invites_path`. The roster is workspace knowledge (auditable, portable, survives a
DB loss — Q6) and stays in the tree; an invite is capability material (a token hash, an expiry, a use
count), it is never read in the workspace, and it must not travel when a workspace is published to
GitHub or attached to somebody else's repo. The runtime binds one subpath per mounted workspace and
never the store root (`runtime_kernel.mounts.workspace_binds`), so no worker container can see this
path. `mint` / `preview` / `accept` / `list` / `revoke` all resolve it through the same function, and
`find_invite` is the one token→workspace resolver both `preview` and `accept` call.
