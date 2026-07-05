/** workspaceApi — the Workspace surface's data-access (its clean SoC boundary), isolation-testable.
 *
 *  The user's workspace (git KG) is reached via the ONE gateway edge under /api/workspace/* with NO
 *  `subject`: the gateway injects X-User-Id and agent-api derives the user's single workspace from it
 *  (P20). FAIL-LOUD (P18): a backend/network error THROWS (via apiClient) so the surface can show it —
 *  except a genuine 404 on a file read, which is a legit "no such file" → null. Proven in workspaceApi.test.ts. */
import { ApiError, getJson } from "./apiClient";

export interface GitState { branch: string; changes: { path: string; kind: string }[]; commits: { sha: string; msg: string; when: string }[] }

/** Read a file's content. A 404 → null (legit "not found"); ANY other failure throws (loud). */
export async function readWorkspaceFile(path: string): Promise<string | null> {
  try {
    const data = await getJson<{ content?: string }>(`/api/workspace/file?path=${encodeURIComponent(path)}`);
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

export async function listWorkspaceTree(opts?: { hidden?: boolean }): Promise<string[]> {
  const data = await getJson<{ files?: string[] }>(`/api/workspace/tree${opts?.hidden ? "?hidden=1" : ""}`);
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
