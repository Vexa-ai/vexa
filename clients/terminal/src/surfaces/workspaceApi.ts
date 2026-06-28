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
