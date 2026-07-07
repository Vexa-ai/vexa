# Workspaces & live collaboration

The workspace model an agent turn sees, how workspaces are shared, and how members
collaborate live during a meeting. This is the canonical explainer; the code lives in
`core/agent/control_plane/` (membership, mounts, reader), `core/agent/worker/` (the turn +
mount preamble), `core/runtime/` (the binds), and `clients/terminal/src/surfaces/workspace.tsx`
(the UI). Status is marked ✅ done / 🟡 partial / ⬜ planned throughout.

## The mount model — three tiers

Every agent turn mounts an ordered set `[_global?, *normal, _system]`:

| Tier | Slug | Access | Always mounted | Purpose |
|---|---|---|---|---|
| **Global system** | `_global` | **read-only** (fs `:ro`) | yes (when configured) | Platform-owned self-awareness: a synced **Vexa branch** (code + docs) + behaviour/skills. One copy, central. |
| **Normal** | `<id>` / `.attached/<subject>/<slug>` | read-write | the primary baseline is; others are opt-in | The user's own + shared knowledge workspaces (see below). |
| **Private system** | `_system` | **read-write** | yes | Per-user private store — chats/sessions, settings, routines, membership/attachment records. Never shared. |

- `_global` and `_system` are **"system possessions" always attached** to a user's agent. They are
  **invisible** in the workspace lists and **non-sharable** — both are in `RESERVED_SLUGS` and
  `ensure_workspace_shareable` refuses them (✅). `_system` can be **surfaced read-only in the files
  panel via a toggle, hidden by default** (the key icon in the KNOWLEDGE header) (✅).
- `_global` is provisioned from `GLOBAL_SYSTEM_WORKSPACE_PATH` (a host dir / synced branch); the
  runtime gives it its own `:ro` bind. Skips gracefully (logs) when unset/absent. (✅ wired;
  auto-sync of the branch is a ⬜ follow-up.)
- Code: `core/agent/control_plane/system_mounts.py` (`GLOBAL_SLUG`, `SYSTEM_SLUG`, `global_mount`,
  `system_mount`, `system_store_path`), the mount stack in `dispatch.py`, binds in
  `core/runtime/src/runtime_kernel/mounts.py:workspace_binds`.

## Personal + normal workspaces

- **Personal** — the user's private baseline (`<root>/<subject>`), always mounted, **non-detachable
  by design** (deactivate/share/archive/delete all refuse the baseline — it's the guaranteed personal
  RW home). Shown as **"Personal"** in the UI (✅). ⬜ planned: provision it named `personal` on
  **account creation** (identity ↔ agent seam) and retire the legacy `seed` slug.
- **Normal workspaces** are **single rank** (owner ruling): one flat **member** rank. Any member can
  **switch on/off** (activate/deactivate), **share**, and read/write. Created blank + additive; seeded
  from a template with a root `README.md` (✅). ⬜ planned: the default seed becomes a **mock tutorial**
  (not FINOS) + **README pinned on init**.
- ⬜ planned — **per-workspace purpose/policy**: each workspace carries a clear statement of what it's
  for / what the agent should write there, wired into the mount preamble — so an agent with a
  *composition* mounted (Personal + a customer-deal shared ws + a sales-dept ws) knows what belongs
  where. This is the key next capability.

## Sharing model (Lane M — `workspace_membership.py`)

**Single rank + creator (owner ruling 2026-07-07).** A shared workspace has one member rank; the
`owner` is just the **creator**. Roles in the git store are still `owner`/`contributor`, but:

- Invites mint a **read/write MEMBER** only — `INVITABLE_ROLES = ("contributor",)`; the read-only
  `viewer` tier is retained in the lattice for back-compat but is **not invitable** (✅).
- **Any member can share** (mint/revoke invites) and read/write — `POST /api/workspace/invites` +
  `DELETE /api/workspace/invites/{id}` require `contributor`.
- **Only the creator** (`owner`) can **unshare / remove members / change role** (`require_role("owner")`).
  → creator-only unshare/delete.
- **DEFERRED DECISION:** whether to *also* offer an **owner-restricted invite mode** (only the creator
  invites) vs. keeping invites purely single-rank. Not decided; see `vexa-ops` handoff.

**Two stores, written together** (git authoritative, index derived):
- Authoritative: the workspace's own git repo — `policy/members.json`
  (`[{subject, role, added_by, added_at}]`) + `policy/invites.json` (sha256 **hash** of each token +
  `{id, role, mode, allowed_emails, expires_at, max_uses, uses, revoked}`).
- Index: `users.data.memberships[]` for "shared with me" — via the injected `MembershipIndex`
  (admin-api `/internal/users/{id}/memberships`; in-memory fake in tests).

**Access modes:** `open` (anyone-with-link, authenticated) or `restricted` (verified `X-User-Email`
in `allowed_emails`). Redeem is **post-auth, no guest** — `POST /api/workspace/invites/accept`.

**`policy/` is PLATFORM-WRITE-ONLY:** an agent turn may never write `policy/`; the worker's turn-commit
reverts any `policy/` change (`_revert_policy_writes` → `{"type":"policy-reverted"}`). Membership is
written only by `workspace_membership.policy_commit` (platform git identity).

## Live collaboration (during a meeting) — all ✅

A member's edits in a shared workspace surface live to the other members:

- **One aggregated activity feed** — the SOURCE CONTROL panel merges commits across ALL active
  workspaces, recency-sorted, each labeled with its workspace (no per-workspace strips). Changed files
  are **clickable links** that open the doc.
- **"New updates" badge on the Knowledge nav** — counts OTHER members' commits since Knowledge was
  last opened; polled always (even on Meetings/Sessions); clears on opening Knowledge.
  (`clients/terminal/src/surfaces/updatesBadge.ts` + the Workbench poll.)
- **Live doc auto-reload** — an OPEN doc reloads (5 s poll) when a member edits it; an "Updated just
  now" banner + one-click **Changes** panel showing that file's latest highlighted diff.
- **Attribution by EMAIL** — commits are authored as the human editor's email (`X-User-Email` stamped
  as the git author name; the synthetic `<subject>@vexa.local` stays for the you/member classification).
  `git_state_at(viewer)` classifies each commit `you` / `member` / `system`.
- **Highlighted diffs** — `GET /api/workspace/git/show` returns a commit's unified diff; the UI renders
  `+`/`−` line highlighting.
- **Cross-workspace file search** — Find-file spans every active workspace, not just the primary; hits
  are tagged with their workspace and open against the right mount.
- **README auto-pinned** when a shared workspace connects — collaborators land on the doc.
- **The 6 s poll is the accepted change-feed** (owner ruling) — no SSE push needed.

Delivery mechanics: **Lane W** serialises the attributed writer per shared repo
(`core/agent/shared/adapters.py workspace_write_lock`); note the flock is not yet on the live commit
path (`dispatch.py` comment) — drive concurrent shared writes **sequentially** for now (⬜ to wire).

## Deferred / planned (documented, not built)
- ⬜ **`_global` branch auto-sync** (currently a one-time clone).
- ⬜ **Agent-proposed GitHub issues** — since `_global` carries the real repo, an agent that notices a
  user's feature request / bug should be able to **propose an issue** to the main repo (governed
  propose→approve→submit, author = principal).
- ⬜ **Filesystem isolation** — today the whole store is bound once at `/workspaces`, so a turn can
  `cd ..` to other workspaces; per-mount binds are the fix (`workspace_binds`), decoupled as a
  presentation remap. (Security hardening for multi-tenant ship.)
- ⬜ **Per-workspace purpose/policy**, **mock-tutorial seed + README-on-init**, **provision `personal`
  on account-create**, **owner-restricted invite mode (deferred decision)** — see above.
- ⬜ **Routine can send a bot** (`routine.v1` gains a `target: agent|meeting`).
- ⬜ **Chat migration (M1)** into `_system` (today `_system` holds only a README marker).

## Related docs
- `core/agent/control_plane/README.md` — Lane M membership/invites + the policy write-guard.
- `core/agent/README.md` — the execution domain (dispatch, worker, contracts).
- `docs/CONTROL-PLANE.md` — control-plane boundary.
- `core/agent/contracts/workspace.v1/` — the workspace git-repo contract.
