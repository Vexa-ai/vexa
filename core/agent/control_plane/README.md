# agent · control_plane

The agent control plane: the FastAPI app (`api.py`) and orchestration that dispatches work to workers and reconciles routine/meeting lifecycle. Owns request handling, routine bookkeeping, transcription watching, and event relay — distinct from the `worker/` that runs a single agent workload.

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
