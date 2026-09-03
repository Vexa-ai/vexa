/** workspaceApi — the Workspace surface's data-access (its clean SoC boundary), isolation-testable.
 *
 *  The user's workspace (git KG) is reached via the ONE gateway edge under /api/workspace/* with NO
 *  `subject`: the gateway injects X-User-Id and agent-api derives the user's single workspace from it
 *  (P20). FAIL-LOUD (P18): a backend/network error THROWS (via apiClient) so the surface can show it —
 *  except a genuine 404 on a file read, which is a legit "no such file" → null. Proven in workspaceApi.test.ts. */
import { ApiError, getJson } from "./apiClient";

// `kind` classifies the committing principal (server-side, D4 attribution): `you` = the caller's own
// agent write · `member` = ANOTHER member's agent push to a shared workspace · `system` = platform/seed
// plumbing. `author` is the principal's display id. Optional so a pre-upgrade agent-api still parses.
export interface GitCommit { sha: string; msg: string; when: string; author?: string; kind?: "you" | "member" | "system"; files?: string[]; ts?: number }
export interface GitState { branch: string; changes: { path: string; kind: string }[]; commits: GitCommit[] }

/** Read a file's content. A 404 → null (legit "not found"); ANY other failure throws (loud).
 *  `slug` (Lane A) targets a SHARED workspace the caller is a member of; omitted → the caller's own ws. */
export async function readWorkspaceFile(path: string, opts?: { slug?: string }): Promise<string | null> {
  const q = opts?.slug ? `&slug=${encodeURIComponent(opts.slug)}` : "";
  try {
    const data = await getJson<{ content?: string }>(`/api/workspace/file?path=${encodeURIComponent(path)}${q}`);
    return data.content ?? "";
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

/** Materialize the user's workspace from the seed template — POST /api/workspace/init (idempotent: an
 *  existing workspace is returned untouched, `seeded:false`). `seeded` is true only on first creation. */
export async function initWorkspace(): Promise<{ workspace: string; seeded: boolean; already_initialized: boolean }> {
  return getJson(`/api/workspace/init`, { method: "POST" });
}

export interface WorkspaceSlot { repo: string | null; ref: string | null; name?: string; nested?: boolean; archived?: boolean }

/** Archive (collapse, keep the data) or un-archive one of your workspaces. */
export async function archiveWorkspace(slug: string, archived: boolean): Promise<{ slug: string; archived: boolean }> {
  return getJson(`/api/workspace/${encodeURIComponent(slug)}/archive`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ archived }),
  });
}

/** WRITE one doc — the in-place page editor. Server enforces mount-rule authorization. */
export async function writeWorkspaceFile(path: string, content: string, opts?: { slug?: string }): Promise<{ path: string; written: boolean }> {
  return getJson(`/api/workspace/file`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ path, content, slug: opts?.slug }) });
}

/** RESET a structural folder (personal baseline, or _global for admins) back to the seed. */
export async function resetWorkspace(target: "personal" | "_global"): Promise<{ target: string; reset: boolean }> {
  return getJson(`/api/workspace/reset`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ target }) });
}

/** DELETE one of your workspaces — removes the data irreversibly. */
export async function deleteWorkspace(slug: string): Promise<{ slug: string; deleted: boolean }> {
  return getJson(`/api/workspace/${encodeURIComponent(slug)}`, { method: "DELETE" });
}
/** `published_url`: where the ACTIVE workspace was published (the token-free URL of its GitHub home),
 *  or null when it never was — a published workspace renders a link instead of the publish action. */
/** `active`: the slug occupying the seed slot (`<root>/<subject>`) — a storage detail, not a rank. Every
 *  workspace is equal-rank; `active_set` (from readActiveSet) is the source of truth for what's mounted. */
export interface AttachedWorkspaces { active: string | null; slots: Record<string, WorkspaceSlot>; published_url?: string | null }
export interface SwapResult { subject: string; active: string; repo: string | null; ref: string | null; swapped: boolean; cloned: boolean; parked: string | null; nested: boolean }

/** The subject's attachment view: which workspace is active + the parked ones available to swap back to. */
export async function readAttachedWorkspaces(): Promise<AttachedWorkspaces> {
  return getJson(`/api/workspace/attached`);
}

/** One member of the ADDITIVE active set (the mount stack the next agent turn mounts — WP-A2.1). The
 *  `primary` member is the private baseline (active by default; can be switched off via `baseline_hidden`). */
export interface ActiveMount { slug: string; repo: string | null; ref: string | null; role: string; path: string; write: boolean; primary: boolean; name?: string | null }
export interface ActiveSet { subject: string; active: ActiveMount[] }

/** The subject's ORDERED active set — the workspaces currently MOUNTED into the agent turn (vs the parked
 *  ones in `slots`, which are AVAILABLE to activate). The private baseline is first and always present. */
export async function readActiveSet(): Promise<ActiveSet> {
  return getJson(`/api/workspace/active`);
}

// ── workspace identity + link resolution (PRD decision 26) ──────────────────────────────────────
/** What a workspace IS, addressed by its immutable id. `access` is this reader's, not a property of
 *  the workspace: `not-yours` and `gone` come back 200, because they are answers and not errors. */
export interface WorkspaceIdentity {
  id: string; name: string | null; kind: string | null; slug?: string | null;
  access: "readable" | "not-yours" | "gone";
}

/** Resolve a workspace id → what it is now, for this reader. */
export async function readWorkspaceById(id: string): Promise<WorkspaceIdentity> {
  return getJson(`/api/workspaces/${encodeURIComponent(id)}`);
}

/** Resolve a workspace SLUG → its identity, which is where its display NAME lives.
 *  The whole of F49: the chat header printed `126`, a directory name showing through. */
export async function readWorkspaceBySlug(slug: string): Promise<WorkspaceIdentity> {
  return getJson(`/api/workspaces/by-slug/${encodeURIComponent(slug)}`);
}

export interface ResolvedLinkRow {
  ref: string; title: string; url: string | null;
  access: "readable" | "not-yours" | "gone";
  workspace?: string | null; path?: string; slug?: string; missing?: boolean;
}

/** A page's worth of link refs → what each points at NOW. One round trip per document: the panel
 *  renders a doc at once, and a request per link is a burst against the same three directories. */
export async function resolveLinks(refs: string[], slug?: string): Promise<ResolvedLinkRow[]> {
  const data = await getJson<{ results?: ResolvedLinkRow[] }>(`/api/links/resolve`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(slug ? { refs, slug } : { refs }),
  });
  return data.results ?? [];
}

/** Report that this reader OPENED one page — the ranking signal behind the desk README (founder,
 *  2026-09-02: it is "mostly links to the other cards in different workspaces", and a list of links
 *  is only useful if the ones they use are at the top). 202: the caller has nothing to do with the
 *  answer, and a panel must never wait on bookkeeping to render a document. */
export async function touchDeskPage(workspace: string, path: string): Promise<{ recorded: boolean }> {
  return getJson(`/api/desk/touch`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace, path }),
  });
}

// ── sharing (Lane M/Lane A): create a shared workspace, mint/redeem invites ──────────────────────
export interface Membership { workspace_id: string; role: string; added_at?: string }
export interface MintedInvite { id: string; token: string; role: string; workspace_id: string; expires_at: string; max_uses: number; mode: string }

/** CREATE a new shared workspace and make the caller its OWNER (the bootstrap that makes a workspace
 *  shareable). Returns the fresh workspace_id — invites can then be minted against it. */
export async function createSharedWorkspace(name: string): Promise<{ workspace_id: string; role: string; name: string }> {
  return getJson(`/api/workspace/shared/new`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
  });
}

/** The caller's SAVED reusable GitHub token — server-side only. `masked` is `••••abcd` (never the clear
 *  value); `set` says whether one is stored. Used as the fallback credential for every git op. */
export interface SavedGitToken { set: boolean; masked: string | null }

/** Read whether a reusable GitHub token is saved (masked preview only — the clear value never leaves the server). */
export async function getGitToken(): Promise<SavedGitToken> {
  return getJson(`/api/workspace/git-token`);
}

/** Save (non-empty) or CLEAR (empty/null) the caller's reusable GitHub token. Returns the masked state. */
export async function setGitToken(token: string | null): Promise<SavedGitToken> {
  return getJson(`/api/workspace/git-token`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: token ?? "" }),
  });
}

/** MINT a scoped invite for a shared workspace (owner/contributor). The token is returned ONCE. */
export async function mintInvite(opts: { workspace_id: string; role?: string; mode?: string; expires_in_sec?: number; max_uses?: number; allowed_emails?: string[] }): Promise<MintedInvite> {
  return getJson(`/api/workspace/invites`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      workspace_id: opts.workspace_id, role: opts.role ?? "contributor", mode: opts.mode ?? "open",
      expires_in_sec: opts.expires_in_sec ?? 604800, max_uses: opts.max_uses ?? 1,
      allowed_emails: opts.allowed_emails ?? null,
    }),
  });
}

/** A read-only PREVIEW of an invite (the pre-join consent screen). `valid=false` ⇒ `reason` says why
 *  (revoked/expired/used_up). `shared_by` is the sharer's email when known, else their subject id. */
export interface InvitePreview {
  workspace_id: string; name: string; purpose: string; role: string; mode: string;
  expires_at?: number | null; shared_by?: string | null; valid: boolean; reason?: string | null;
}

/** PREVIEW an invite token → what workspace it is + the terms, WITHOUT joining. Works before login
 *  (the proxy's fallback service key reaches agent-api; the token itself is the capability). */
export async function previewInvite(token: string): Promise<InvitePreview> {
  return getJson(`/api/workspace/invites/preview?token=${encodeURIComponent(token)}`);
}

/** REDEEM an invite token (any logged-in user) → membership. Idempotent per user. */
export async function acceptInvite(token: string): Promise<{ workspace_id: string; role: string; already_member: boolean }> {
  return getJson(`/api/workspace/invites/accept`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }),
  });
}

/** The "workspaces shared with me" listing (the derived index — users.data.memberships[]). */
export async function listSharedMemberships(): Promise<Membership[]> {
  const data = await getJson<{ memberships?: Membership[] }>(`/api/workspace/shared`);
  return data.memberships ?? [];
}

/** Redeem an INDEPENDENT transcript share token (any logged-in user) → subscribe access to that
 *  meeting's live feed. Decoupled from workspaces. Used post-auth alongside acceptInvite for a bundle. */
export async function acceptTranscriptShare(token: string): Promise<{ meeting_id: number; ok: boolean }> {
  return getJson(`/api/transcripts/share/accept`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }),
  });
}

/** MINT an independent transcript share link for a meeting (owner). Token returned once. */
export async function mintTranscriptShare(opts: { platform: string; native_meeting_id: string; mode?: string; allowed_emails?: string[]; expires_in_sec?: number }): Promise<{ id: string; token: string; mode: string; expires_at: string }> {
  return getJson(`/api/meetings/${encodeURIComponent(opts.platform)}/${encodeURIComponent(opts.native_meeting_id)}/share`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: opts.mode ?? "open", allowed_emails: opts.allowed_emails ?? [], expires_in_sec: opts.expires_in_sec ?? 86400 }),
  });
}

/** Make one of YOUR workspaces shareable (promote a private one to shared if needed) → returns the
 *  shareable workspace_id. Lets ANY workspace be shared after creation — no share-vs-not at create time. */
/** Set a workspace's PURPOSE line — the one-liner the mount preamble declares to the agent. */
export async function setWorkspacePurpose(slug: string, purpose: string): Promise<{ purpose: string }> {
  return getJson(`/api/workspace/purpose`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ slug, purpose }),
  });
}

export async function shareEnableWorkspace(slug: string): Promise<{ workspace_id: string; promoted: boolean }> {
  return getJson(`/api/workspace/${encodeURIComponent(slug)}/share-enable`, { method: "POST" });
}

/** UN-SHARE a workspace (owner only) — move it back to your private store; other members lose access. */
export async function unshareWorkspace(workspaceId: string): Promise<{ slug: string }> {
  return getJson(`/api/workspace/${encodeURIComponent(workspaceId)}/unshare`, { method: "POST" });
}

/** Switch a shared workspace ON (mount) or OFF (hide) in your active set — membership is unchanged. */
export async function setSharedActive(workspace_id: string, active: boolean): Promise<{ workspace_id: string; active: boolean }> {
  return getJson(`/api/workspace/shared/${encodeURIComponent(workspace_id)}/active`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active }),
  });
}

/** ADD a workspace to the active set WITHOUT parking the others (the additive counterpart of swap): the
 *  private baseline and any other active workspaces stay mounted. Pass `repo` to clone/restore a git repo,
 *  or `slug` to activate an already-parked slot. Idempotent — an already-active workspace is a no-op.
 *  `token` (optional) authenticates a PRIVATE repo's clone — used server-side only, never stored (P15). */
export async function activateWorkspace(opts: { repo?: string; ref?: string; slug?: string; token?: string }): Promise<{ subject: string; slug: string; changed: boolean; cloned: boolean; nested: boolean }> {
  return getJson(`/api/workspace/activate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo: opts.repo ?? null, ref: opts.ref ?? null, slug: opts.slug ?? null, token: opts.token ?? null }),
  });
}

/** CREATE a brand-new BLANK workspace (seeded from the template) at a fresh slug and ADD it to the active
 *  set — the additive-model "new workspace" action. NOT a swap: nothing is parked, rebuilt, or backed up;
 *  the private baseline and every other active workspace stay exactly as they were. `name` (optional) sets
 *  the new workspace's display label (default a unique "New workspace"). The new row appears CHECKED. */
export async function createWorkspace(name?: string): Promise<{ subject: string; slug: string; changed: boolean; added: boolean }> {
  return getJson(`/api/workspace/new`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name ?? null }),
  });
}

/** REMOVE a workspace from the active set (park it — never destroyed; the tree stays, ready to re-activate).
 *  The private baseline can be switched off too (server sets `baseline_hidden`; re-activate to switch back on).
 *  Idempotent — an already-off / not-active slug is a no-op. */
export async function deactivateWorkspace(slug: string): Promise<{ subject: string; slug: string; changed: boolean }> {
  return getJson(`/api/workspace/deactivate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slug }),
  });
}

/** Attach a custom external git repo as the active workspace (swap). The current workspace is PARKED
 *  (kept, never destroyed) so it can be swapped back to. Omit `repo` to swap back to the seeded default.
 *  `fresh` (seed only) rebuilds the default from the template instead of restoring your parked seed —
 *  the displaced default is kept under a recoverable backup slot. `token` (optional) authenticates a
 *  PRIVATE repo's clone — used server-side for the clone only, never stored (P15). */
export async function swapWorkspace(repo?: string, ref?: string, token?: string, fresh?: boolean, slug?: string): Promise<SwapResult> {
  return getJson(`/api/workspace/swap`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo: repo ?? null, ref: ref ?? null, slug: slug ?? null, token: token ?? null, fresh: fresh ?? false }),
  });
}

export interface PublishResult { repo_url: string; pushed_ref: string; head_sha: string; created: boolean }

/** Publish the vexa-born ACTIVE workspace to GitHub — the counterpart of attach: create the repo under
 *  the caller's account (private by default) and push the current branch's FULL history. `token` is the
 *  caller's PAT — used server-side for this one call (repo creation + push), NEVER stored (P15).
 *  Re-publish to the same repo is a plain push (fast-forward, or a clear error — never a force push):
 *  pass `remoteUrl` (the workspace's published home) to PUSH UPDATES there instead of creating a repo. */
export async function publishWorkspace(repoName: string, priv: boolean, token?: string, remoteUrl?: string, slug?: string): Promise<PublishResult> {
  return getJson(`/api/workspace/publish`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // `slug` targets ANY workspace the caller can manage (own slot or shared membership); omitted = seed.
    body: JSON.stringify(remoteUrl ? { remote_url: remoteUrl, token, slug } : { repo_name: repoName, private: priv, token, slug }),
  });
}

/** Rename a workspace slot — a DISPLAY label only (the slug + parked tree are unchanged, so swap-back and
 *  repo re-attach keep matching). Pass an empty `name` to clear the label. Returns the updated view. */
export async function renameWorkspace(slug: string, name: string): Promise<AttachedWorkspaces> {
  return getJson(`/api/workspace/rename`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slug, name }),
  });
}

/** List a workspace's files. `slug` (Lane A) targets a SHARED workspace the caller is a member of;
 *  omitted → the caller's own (primary) workspace. */
export async function listWorkspaceTree(opts?: { hidden?: boolean; slug?: string }): Promise<string[]> {
  const params = new URLSearchParams();
  if (opts?.hidden) params.set("hidden", "1");
  if (opts?.slug) params.set("slug", opts.slug);
  const qs = params.toString();
  const data = await getJson<{ files?: string[] }>(`/api/workspace/tree${qs ? `?${qs}` : ""}`);
  return data.files ?? [];
}

/** Source-control state of a workspace. No `slug` → the caller's own primary; a `slug` → a SHARED
 *  workspace the caller is a member of (its commits carry author + kind for the activity feed). */
export async function readWorkspaceGit(opts?: { slug?: string }): Promise<GitState> {
  const qs = opts?.slug ? `?slug=${encodeURIComponent(opts.slug)}` : "";
  const g = await getJson<GitState>(`/api/workspace/git${qs}`);
  // A 200 with the WRONG shape (an error/degraded body) is still a failure — throw loud rather than
  // hand the surface a malformed GitState (the GitSection crash). The surface shows it; never crashes.
  if (!g || !Array.isArray(g.changes) || !Array.isArray(g.commits)) {
    throw new ApiError(200, "malformed git state (missing changes/commits)", "/api/workspace/git");
  }
  return g;
}

// ── GitHub sync (any workspace with a home remote) — push · pull · ahead/behind status ────────────
/** The GitHub-sync state of a workspace: its home remote (origin for attached clones, vexa-publish for
 *  published vexa-born), the branch, and ahead/behind counts vs the last-fetched tracking ref. No token. */
export interface GitRemoteStatus { has_home: boolean; remote: string | null; url: string | null; branch: string | null; tracked: boolean; ahead: number; behind: number }
export async function gitRemoteStatus(opts?: { slug?: string }): Promise<GitRemoteStatus> {
  const qs = opts?.slug ? `?slug=${encodeURIComponent(opts.slug)}` : "";
  return getJson<GitRemoteStatus>(`/api/workspace/git-remote-status${qs}`);
}

export interface PushSyncResult { remote: string; url: string; branch: string; head_sha: string }
/** PUSH a workspace's current branch to its GitHub home (fast-forward only, never force). `token` is the
 *  caller's PAT — used for this push only, never stored (P15). A diverged remote fails loud (pull first). */
export async function pushWorkspace(opts: { slug?: string; token?: string }): Promise<PushSyncResult> {
  return getJson(`/api/workspace/push`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slug: opts.slug ?? null, token: opts.token }),
  });
}

export interface PullSyncResult { remote: string; url: string; branch: string; head_sha: string; updated: boolean; behind_before: number }
/** PULL a workspace from its GitHub home — fetch + fast-forward only. `token` (optional for public repos)
 *  is used for the fetch only, never stored (P15). A divergence is refused (no merge/rebase/force). */
export async function pullWorkspace(opts?: { slug?: string; token?: string }): Promise<PullSyncResult> {
  return getJson(`/api/workspace/pull`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slug: opts?.slug ?? null, token: opts?.token ?? null }),
  });
}

// ── per-workspace purpose (travels when shared; feeds the agent's mount preamble) ─────────────────
/** Read a workspace's PURPOSE one-liner ("" when unset). `slug` omitted → the caller's primary. */
export async function readWorkspacePurpose(opts?: { slug?: string }): Promise<string> {
  const qs = opts?.slug ? `?slug=${encodeURIComponent(opts.slug)}` : "";
  const data = await getJson<{ purpose?: string }>(`/api/workspace/purpose${qs}`);
  return data.purpose ?? "";
}
/** Set (or clear, with "") a workspace's PURPOSE. Returns the normalized purpose actually stored. */
export async function writeWorkspacePurpose(purpose: string, opts?: { slug?: string }): Promise<string> {
  const data = await getJson<{ purpose?: string }>(`/api/workspace/purpose`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slug: opts?.slug ?? null, purpose }),
  });
  return data.purpose ?? "";
}

// ── participants (shared workspace membership) — list · role · remove · leave · invites ───────────
/** One member of a shared workspace (authoritative policy/members.json). `role` owner = the CREATOR;
 *  contributor = a read/write member (the single member rank). `subject` is the synthetic user id;
 *  `email` is their verified email when known (stamped at grant / on first manage-panel view) — the
 *  human label the roster prefers over the opaque subject. */
export interface WorkspaceMember { subject: string; role: string; email?: string; added_by?: string; added_at?: string }
export async function listWorkspaceMembers(workspaceId: string): Promise<WorkspaceMember[]> {
  const data = await getJson<{ members?: WorkspaceMember[] }>(`/api/workspace/members?workspace_id=${encodeURIComponent(workspaceId)}`);
  return data.members ?? [];
}
/** Remove a member (owner only). */
export async function removeWorkspaceMember(workspaceId: string, memberSubject: string): Promise<{ ok: boolean }> {
  return getJson(`/api/workspace/members/${encodeURIComponent(memberSubject)}?workspace_id=${encodeURIComponent(workspaceId)}`, { method: "DELETE" });
}
/** Flip a member's role (owner only) — contributor ↔ owner. */
export async function setWorkspaceMemberRole(workspaceId: string, memberSubject: string, role: string): Promise<WorkspaceMember> {
  return getJson(`/api/workspace/members/${encodeURIComponent(memberSubject)}/role?workspace_id=${encodeURIComponent(workspaceId)}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role }),
  });
}
/** LEAVE a shared workspace — remove yourself (any role; a sole creator is refused, must unshare). */
export async function leaveWorkspace(workspaceId: string): Promise<{ ok: boolean; left: string }> {
  return getJson(`/api/workspace/${encodeURIComponent(workspaceId)}/leave`, { method: "POST" });
}
/** A live invite for a shared workspace (owner/contributor). Hashes are never surfaced. */
export interface WorkspaceInvite { id: string; role: string; mode: string; expires_at?: string; max_uses?: number; uses?: number; revoked?: boolean; allowed_emails?: string[] }
export async function listWorkspaceInvites(workspaceId: string): Promise<WorkspaceInvite[]> {
  const data = await getJson<{ invites?: WorkspaceInvite[] }>(`/api/workspace/invites?workspace_id=${encodeURIComponent(workspaceId)}`);
  return data.invites ?? [];
}
/** Revoke an invite (owner/contributor). */
export async function revokeWorkspaceInvite(workspaceId: string, inviteId: string): Promise<{ ok: boolean }> {
  return getJson(`/api/workspace/invites/${encodeURIComponent(inviteId)}?workspace_id=${encodeURIComponent(workspaceId)}`, { method: "DELETE" });
}

export interface GitDiff { sha: string; path?: string; diff: string; truncated?: boolean }
/** Unified diff of a single commit (optionally one file) — for highlighting exactly what changed.
 *  `slug` targets a shared workspace the caller is a member of; omitted → the caller's own primary. */
export async function readWorkspaceGitDiff(opts: { sha: string; slug?: string; path?: string }): Promise<GitDiff> {
  const qs = new URLSearchParams({ sha: opts.sha });
  if (opts.slug) qs.set("slug", opts.slug);
  if (opts.path) qs.set("path", opts.path);
  return getJson<GitDiff>(`/api/workspace/git/show?${qs.toString()}`);
}

// ── LOADING AN EXISTING REPO ("we already have our workspace on GitHub") ───────────────────────────
//
// Two lanes, one mechanic. A person's own desk swaps through `swapWorkspace` above; a GROUP workspace
// goes through the routes below. They are separate routes only because the AUTHORIZATION differs — a
// desk belongs to its subject, a group belongs to a member list, and replacing a group's tree is a
// write, so the server refuses a viewer with 403.
//
// The credential model is deliberately NOT "paste a token". The primary path is a per-workspace
// ed25519 DEPLOY KEY whose private half never leaves the server: we hand out our PUBLIC half, the
// person adds it to THEIR repository, and nothing of theirs travels to us. The saved PAT
// (`getGitToken`/`setGitToken`) stays as the fallback for `https://` remotes.

/** What the server actually did — and `state` is the whole answer, in the server's own vocabulary:
 *  `cloned` (fetched from the remote), `restored` (a copy was already here, swapped back to with no
 *  re-clone) or `already attached` (nothing changed). `parked` names the tree set aside, which is what
 *  makes the swap reversible. Callers must repeat these three back rather than collapsing them into
 *  "done": a person deciding whether their data is where they think it is needs them told apart. */
export interface SharedAttachResult {
  workspace_id: string;
  active: string;
  repo: string | null;
  ref: string | null;
  attached: boolean;
  cloned: boolean;
  parked: string | null;
  nested: boolean;
  state: "cloned" | "restored" | "already attached";
}

/** A shared workspace's attachment view — what is mounted, what is parked and available to swap back
 *  to, and where its GitHub home is. `credential` is a CAPABILITY line ("origin https://…, deploy key
 *  set") — whether a credential exists and of what kind, NEVER its value; it is safe to render. */
export interface SharedAttached {
  workspace_id: string;
  active: string | null;
  slots: Record<string, WorkspaceSlot>;
  home: { remote: string | null; url: string | null; branch: string | null; ahead: number; behind: number };
  credential: string;
}

/** A workspace's deploy key, PUBLIC half only. `add_at` is GitHub's deploy-keys settings URL for the
 *  repo and is null when no repo was named — surfaces must not invent one. `add_as` · `then` · `message`
 *  are the server's own composed instruction; the GET (state-only) shape carries just `add_as`. */
export interface DeployKey {
  slug: string;
  public_key: string | null;
  fingerprint: string | null;
  add_at?: string | null;
  add_as: string;
  then?: string;
  message?: string;
}

/** Attach an existing git repo as a SHARED workspace's tree (contributor or owner — a viewer gets 403).
 *  Park-and-clone like the desk swap: the current tree is kept and can be swapped back to by `slug` with
 *  no re-clone. `token` is a one-off used server-side for this clone only and never stored (P15) — the
 *  normal credential is the workspace's deploy key, which needs nothing passed here. */
export async function attachSharedWorkspace(
  workspaceId: string,
  opts: { repo?: string; ref?: string; slug?: string; token?: string },
): Promise<SharedAttachResult> {
  return getJson(`/api/workspace/shared/${encodeURIComponent(workspaceId)}/attach`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo: opts.repo ?? null, ref: opts.ref ?? null, slug: opts.slug ?? null, token: opts.token ?? null }),
  });
}

/** Read a shared workspace's attachment view. Any member may — it says WHAT is mounted, never a secret. */
export async function readSharedAttached(workspaceId: string): Promise<SharedAttached> {
  return getJson(`/api/workspace/shared/${encodeURIComponent(workspaceId)}/attached`);
}

/** Generate-or-return this workspace's deploy key and say where to add it. IDEMPOTENT by design: a
 *  second call returns the SAME key, so a key the person already pasted into their repo is never
 *  invalidated by clicking twice. Pass `repo` to get the exact GitHub settings URL back in `add_at`. */
export async function ensureDeployKey(slug: string, repo?: string): Promise<DeployKey> {
  return getJson(`/api/workspace/${encodeURIComponent(slug)}/deploy-key`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo: repo ?? null }),
  });
}

/** This workspace's public deploy key, or `public_key: null` when none has been generated. A pure read:
 *  it never creates one, so a surface can show the state before the person commits to anything. */
export async function readDeployKey(slug: string): Promise<DeployKey> {
  return getJson(`/api/workspace/${encodeURIComponent(slug)}/deploy-key`);
}
