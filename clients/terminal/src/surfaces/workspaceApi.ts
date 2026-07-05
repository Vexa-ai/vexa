/** workspaceApi — the Workspace surface's data-access (its clean SoC boundary), isolation-testable.
 *
 *  The user's workspace (git KG) is reached via the ONE gateway edge under /api/workspace/* with NO
 *  `subject`: the gateway injects X-User-Id and agent-api derives the user's single workspace from it
 *  (P20). FAIL-LOUD (P18): a backend/network error THROWS (via apiClient) so the surface can show it —
 *  except a genuine 404 on a file read, which is a legit "no such file" → null. Proven in workspaceApi.test.ts. */
import { ApiError, getJson } from "./apiClient";

export interface GitState { branch: string; changes: { path: string; kind: string }[]; commits: { sha: string; msg: string; when: string }[] }

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

export interface WorkspaceSlot { repo: string | null; ref: string | null; name?: string; nested?: boolean }
/** `published_url`: where the ACTIVE workspace was published (the token-free URL of its GitHub home),
 *  or null when it never was — a published workspace renders a link instead of the publish action. */
export interface AttachedWorkspaces { active: string | null; slots: Record<string, WorkspaceSlot>; published_url?: string | null }
export interface SwapResult { subject: string; active: string; repo: string | null; ref: string | null; swapped: boolean; cloned: boolean; parked: string | null; nested: boolean }

/** The subject's attachment view: which workspace is active + the parked ones available to swap back to. */
export async function readAttachedWorkspaces(): Promise<AttachedWorkspaces> {
  return getJson(`/api/workspace/attached`);
}

/** One member of the ADDITIVE active set (the mount stack the next agent turn mounts — WP-A2.1). The
 *  `primary` member is the private baseline (always active, never deactivatable). */
export interface ActiveMount { slug: string; repo: string | null; ref: string | null; role: string; path: string; write: boolean; primary: boolean }
export interface ActiveSet { subject: string; active: ActiveMount[] }

/** The subject's ORDERED active set — the workspaces currently MOUNTED into the agent turn (vs the parked
 *  ones in `slots`, which are AVAILABLE to activate). The private baseline is first and always present. */
export async function readActiveSet(): Promise<ActiveSet> {
  return getJson(`/api/workspace/active`);
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
 *  The private baseline cannot be deactivated (the server answers 409). Idempotent — a not-active slug is a no-op. */
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
export async function publishWorkspace(repoName: string, priv: boolean, token: string, remoteUrl?: string): Promise<PublishResult> {
  return getJson(`/api/workspace/publish`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(remoteUrl ? { remote_url: remoteUrl, token } : { repo_name: repoName, private: priv, token }),
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

export async function readWorkspaceGit(): Promise<GitState> {
  const g = await getJson<GitState>(`/api/workspace/git`);
  // A 200 with the WRONG shape (an error/degraded body) is still a failure — throw loud rather than
  // hand the surface a malformed GitState (the GitSection crash). The surface shows it; never crashes.
  if (!g || !Array.isArray(g.changes) || !Array.isArray(g.commits)) {
    throw new ApiError(200, "malformed git state (missing changes/commits)", "/api/workspace/git");
  }
  return g;
}
