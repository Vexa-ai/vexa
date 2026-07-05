# agent · control_plane

The agent control plane: the FastAPI app (`api.py`) and orchestration that dispatches work to workers and reconciles routine/meeting lifecycle. Owns request handling, routine bookkeeping, transcription watching, and event relay — distinct from the `worker/` that runs a single agent workload.

## Workspace membership + invites + roles (Lane M — the access layer for shared workspaces)

`workspace_membership.py` is the access layer that lets a workspace **owner** grant another user
**viewer** (read) or **contributor** (read/write) access — the foundation for shared workspaces and
shared meeting sessions.

**Two stores, written together (git is authoritative, the index is derived):**
- **Authoritative** — the workspace's OWN git repo at `policy/members.json`
  (`[{subject, role: owner|contributor|viewer, added_by, added_at}]`) and `policy/invites.json`
  (only the sha256 **hash** of each invite token, with `{id, role, mode, allowed_emails, expires_at,
  max_uses, uses, revoked, …}`). Auditable, travels with the workspace, survives a DB loss.
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
- `POST /invites/accept` (any logged-in user; POST-AUTH redeem, no anonymous/guest) → validate
  (hash lookup, not expired/revoked, uses<max_uses, AND mode==open OR verified email listed) → grant
  membership (both stores) → increment uses. Idempotent per user (double-accept = one membership).
  The token carries no workspace id — it is resolved by hash scan over shareable workspaces.
- `DELETE /invites/{id}` (owner/contributor) revoke · `GET /invites`, `GET /members`
  (owner/contributor) · `DELETE /members/{subject}` (owner) · `POST /members/{subject}/role` (owner,
  the "change read/write permissions" DoD item) · `GET /shared` = the "shared with me" listing.

**Role enforcement** — `require_role(root, workspace_id, subject, min_role)` (owner > contributor >
viewer) is the ONE gate every shared route uses. The SYSTEM workspace + reserved/own-private slugs are
never shareable (`assert_shareable`: dot-prefixed / `sys` / `_system` / `seed` refused).

**`is_member(root, workspace_id, subject) -> role|None`** is the seam Lane A calls for mount-resolution
and transcript-subscribe-by-membership. This lane provides membership DATA + APIs only — it does NOT
touch the mount set / dispatch.

### policy/ is PLATFORM-WRITE-ONLY (the write-guard mechanism, Q3)

`policy/` (members.json, invites.json) is written ONLY by the control plane
(`workspace_membership.policy_commit` — stages + commits just `policy/` with the platform identity as
committer, never sweeping the agent's tree). An **agent turn must never modify `policy/`**. Enforcement
lives in the worker's turn-commit path (`llm/ports.run_harness_turn`): `_revert_policy_writes(work)`
runs right before `git add -A` — it reverts any tracked `policy/` change back to HEAD and deletes any
untracked `policy/` add, emitting `{"type":"policy-reverted","paths":[…]}`. So a turn's legitimate
(non-policy) writes still commit while a policy tamper is reverted before it can land. (Chosen default
per plan Q3: post-turn validation + revert.)
