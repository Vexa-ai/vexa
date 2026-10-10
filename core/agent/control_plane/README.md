# agent · control_plane

The agent control plane: the FastAPI app (`api.py`) and orchestration that dispatches work to workers and reconciles routine/meeting lifecycle. Owns request handling, routine bookkeeping, transcription watching, and event relay — distinct from the `worker/` that runs a single agent workload.

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
  dependency, applies `require_person` to the route a request matched; a row naming a route the
  app does not serve refuses the boot.
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
- `model_providers/` — the operator's model catalog (`VEXA_MODEL_CATALOG`, `models.v1`) and the
  provider port each model resolves through, one adapter per provider kind (ADR-0042). The dispatch
  (`dispatch.apply_model_route`, `route_env`) is the one place a resolved route becomes a worker's
  environment; see [`model_providers/README.md`](model_providers/README.md).

**Meetings**
- `bridge.py` — the meeting WebSocket → agent bridge.
- `transcription_watcher.py` — the in-process watch over the live transcript.
- `meeting_mint.py` (a meeting's page exists from the moment the meeting does), `meeting_note.py`
  (where its report lives on a desk), `meeting_room.py` (the attendees' read-only mounts after it),
  `meeting_highlight.py` (what it has named, and which names have a page), `meeting_terms.py` (the
  annotation layer over its transcript), `meeting_steering.py` (per-state preambles for meeting chat
  turns), `schedule_digest.py` (the schedule as a prompt block).

**Workspaces**
- `workspace_reader.py`, `workspace_ids.py` (id → where it is now), `workspace_purpose.py`,
  `system_mounts.py` (the two system tiers of the mount stack), `global_layer.py` and
  `global_seed.py` (the company layer in `_global`, and its seed), `link_resolver.py`.
- Membership: `workspace_membership.py` (below), `membership_acts.py` (adding a member is a
  conversation).
- Git: `workspace_attach.py`, `workspace_import.py`, `workspace_publish.py`, `workspace_git_sync.py`,
  `repo_ref.py`, `deploy_keys.py`, `workspace_credentials.py`, `git_credentials.py`,
  `git_secret_store.py`, and `secret_store.py` (the one encrypted-at-rest store).

**Chat, pages and onboarding**
- `scaffolds.py` (what the agent knows and the UI shows at the moment a person arrives),
  `preset_library.py` (the ask library), `chat_intents.py` (a button on a page → its preset),
  `front_page.py`, `flow_pages_watch.py`, `claims.py` (the claim book), `onboarding_research.py`
  (the account-scoped research cursor), `connection_setup_schema.py` (a custom-service setup
  proposal's shape).

## A worker's delegation token ends with its unit

`delegation_revocation.py`. Each dispatch's delegation token (`shared/delegation.py`) is recorded
against its unit before the spawn (`vexa:delegation:unit:<unit id>`, jti → exp), and a token that
cannot be recorded is withheld. The reaper thread compares the recorded units with the runtime's live
workloads every 30 s; a unit the runtime no longer runs — completed, idled out, stopped, failed, or
never started — has its tokens written to `vexa:delegation:revoked:<jti>` with their remaining
lifetime, which identity's `/internal/validate` refuses. A unit id is reused across warm windows, so
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

`workspace_membership.py` is the access layer for shared workspaces. **Single-rank model (owner ruling
2026-07-07):** a shared workspace has ONE member rank — every member is read/write and can share
(mint/revoke invites); the **`owner` is just the CREATOR** (the only one who can unshare / remove
members / change role). The read-only `viewer` role stays in the lattice for back-compat but is **not
invitable** — `INVITABLE_ROLES = ("contributor",)`.

**Two stores, written together (git is authoritative, the index is derived):**
- **Authoritative** — the workspace's OWN git repo at `policy/members.json`
  (`[{subject, role: owner|contributor|viewer, added_by, added_at}]`). Auditable, travels with the
  workspace, survives a DB loss. **Invites are not here** — they are at
  `<store-root>/.invites/<workspace_id>.json` (only the sha256 **hash** of each token, with
  `{id, role, mode, allowed_emails, expires_at, max_uses, uses, revoked, …}`), outside every workspace
  mount; see the write-guard section below for what moved and why.
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
