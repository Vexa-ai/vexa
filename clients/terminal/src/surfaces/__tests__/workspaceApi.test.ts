/** Isolation harness — Workspace data-access. Scoped (no subject, P20) + fail-loud (P18): a backend
 *  error throws, a malformed git body throws (never reaches GitSection as a fake GitState), and a 404
 *  file read is the one legit "empty" → null. */
import { describe, it, expect, vi, afterEach } from "vitest";
import { readWorkspaceFile, listWorkspaceTree, readWorkspaceGit, readAttachedWorkspaces, swapWorkspace, renameWorkspace, publishWorkspace, readActiveSet, activateWorkspace, deactivateWorkspace } from "../workspaceApi";
import { ApiError } from "../apiClient";

let fetchMock: ReturnType<typeof vi.fn>;
const lastUrl = () => String(fetchMock.mock.calls.at(-1)![0]);
const lastBody = () => JSON.parse(String((fetchMock.mock.calls.at(-1)![1] as RequestInit).body));
function mock(ok: boolean, status: number, body: unknown) {
  fetchMock = vi.fn(async () => ({ ok, status, json: async () => body }) as unknown as Response);
  globalThis.fetch = fetchMock as unknown as typeof fetch;
}
afterEach(() => vi.restoreAllMocks());

describe("workspaceApi — scoped (no subject) + fail-loud", () => {
  it("readWorkspaceFile GETs /api/workspace/file?path=… (encoded), no subject", async () => {
    mock(true, 200, { content: "hello" });
    expect(await readWorkspaceFile("kg/a b.md")).toBe("hello");
    expect(lastUrl()).toBe("/api/workspace/file?path=kg%2Fa%20b.md");
    expect(lastUrl()).not.toContain("subject");
  });
  it("listWorkspaceTree GETs /api/workspace/tree (+?hidden=1), no subject", async () => {
    mock(true, 200, { files: ["a.md"] });
    expect(await listWorkspaceTree()).toEqual(["a.md"]);
    expect(lastUrl()).toBe("/api/workspace/tree");
    mock(true, 200, { files: [] });
    await listWorkspaceTree({ hidden: true });
    expect(lastUrl()).toBe("/api/workspace/tree?hidden=1");
  });
  it("Lane A: listWorkspaceTree({slug}) scopes to a shared workspace (?slug=…)", async () => {
    mock(true, 200, { files: ["kg/x.md"] });
    await listWorkspaceTree({ slug: "wsA" });
    expect(lastUrl()).toBe("/api/workspace/tree?slug=wsA");
    mock(true, 200, { files: [] });
    await listWorkspaceTree({ hidden: true, slug: "ws b" });
    expect(lastUrl()).toBe("/api/workspace/tree?hidden=1&slug=ws+b");
  });
  it("Lane A: readWorkspaceFile(path,{slug}) appends &slug=… (shared read)", async () => {
    mock(true, 200, { content: "shared" });
    expect(await readWorkspaceFile("kg/x.md", { slug: "wsA" })).toBe("shared");
    expect(lastUrl()).toBe("/api/workspace/file?path=kg%2Fx.md&slug=wsA");
  });
  it("share flow: createSharedWorkspace / mintInvite / acceptInvite POST the right bodies", async () => {
    const { createSharedWorkspace, mintInvite, acceptInvite } = await import("../workspaceApi");
    mock(true, 201, { workspace_id: "deal-room-ab12cd", role: "owner", name: "Deal Room" });
    expect((await createSharedWorkspace("Deal Room")).workspace_id).toBe("deal-room-ab12cd");
    expect(lastUrl()).toBe("/api/workspace/shared/new");
    expect(lastBody()).toEqual({ name: "Deal Room" });

    mock(true, 201, { id: "i1", token: "TOK", role: "contributor", workspace_id: "deal-room-ab12cd", expires_at: "", max_uses: 50, mode: "open" });
    await mintInvite({ workspace_id: "deal-room-ab12cd", role: "contributor", mode: "open", max_uses: 50, expires_in_sec: 604800 });
    expect(lastUrl()).toBe("/api/workspace/invites");
    expect(lastBody()).toMatchObject({ workspace_id: "deal-room-ab12cd", role: "contributor", mode: "open", max_uses: 50 });

    mock(true, 200, { workspace_id: "deal-room-ab12cd", role: "contributor", already_member: false });
    expect((await acceptInvite("TOK")).already_member).toBe(false);
    expect(lastUrl()).toBe("/api/workspace/invites/accept");
    expect(lastBody()).toEqual({ token: "TOK" });
  });
  it("readWorkspaceGit returns a valid GitState on 200", async () => {
    mock(true, 200, { branch: "main", changes: [], commits: [] });
    expect((await readWorkspaceGit()).branch).toBe("main");
  });
  it("FAIL-LOUD: readWorkspaceGit THROWS on a wrong-shape body (no fake GitState → no GitSection crash)", async () => {
    mock(true, 200, { detail: [{ msg: "Field required" }] });
    await expect(readWorkspaceGit()).rejects.toBeInstanceOf(ApiError);
  });
  it("FAIL-LOUD: a backend error throws (tree + git)", async () => {
    mock(false, 502, { detail: "down" });
    await expect(listWorkspaceTree()).rejects.toBeInstanceOf(ApiError);
    await expect(readWorkspaceGit()).rejects.toBeInstanceOf(ApiError);
  });
  it("readWorkspaceFile: a 404 is legit 'not found' → null (the ONE non-loud case)", async () => {
    mock(false, 404, { detail: "not found" });
    expect(await readWorkspaceFile("missing.md")).toBeNull();
  });
  it("readWorkspaceFile: a NON-404 error throws (loud)", async () => {
    mock(false, 500, { detail: "boom" });
    await expect(readWorkspaceFile("x.md")).rejects.toBeInstanceOf(ApiError);
  });
  it("swapWorkspace POSTs repo/ref/token + slug + fresh (start-fresh & slug-targeting reach the body)", async () => {
    mock(true, 200, { swapped: true });
    await swapWorkspace(undefined, undefined, undefined, true, "seed");   // start fresh, by slug
    expect(lastUrl()).toBe("/api/workspace/swap");
    expect(lastBody()).toEqual({ repo: null, ref: null, slug: "seed", token: null, fresh: true });
    await swapWorkspace("https://h/r.git", "dev", "TOK");                 // attach a repo
    expect(lastBody()).toEqual({ repo: "https://h/r.git", ref: "dev", slug: null, token: "TOK", fresh: false });
  });
  it("publishWorkspace POSTs {repo_name,private,token} to /api/workspace/publish and returns the result", async () => {
    mock(true, 200, { repo_url: "https://github.com/u/w", pushed_ref: "main", head_sha: "abc123", created: true });
    const res = await publishWorkspace("w", true, "TOK");
    expect(lastUrl()).toBe("/api/workspace/publish");
    expect(lastBody()).toEqual({ repo_name: "w", private: true, token: "TOK" });
    expect(res.repo_url).toBe("https://github.com/u/w");
    expect(res.created).toBe(true);
  });
  it("FAIL-LOUD: publishWorkspace throws on a backend error (409 already-exists, 502 push failure)", async () => {
    mock(false, 409, { detail: "a repository named 'w' already exists under your account" });
    await expect(publishWorkspace("w", true, "TOK")).rejects.toBeInstanceOf(ApiError);
    mock(false, 502, { detail: "git push failed" });
    await expect(publishWorkspace("w", false, "TOK")).rejects.toBeInstanceOf(ApiError);
  });
  it("publishWorkspace with a remoteUrl POSTs {remote_url,token} — PUSH UPDATES to the published home, no repo creation", async () => {
    mock(true, 200, { repo_url: "https://github.com/u/w", pushed_ref: "main", head_sha: "def456", created: false });
    const res = await publishWorkspace("ignored", true, "TOK", "https://github.com/u/w");
    expect(lastUrl()).toBe("/api/workspace/publish");
    expect(lastBody()).toEqual({ remote_url: "https://github.com/u/w", token: "TOK" });
    expect(res.created).toBe(false);
  });
  it("readAttachedWorkspaces surfaces published_url (the active workspace's GitHub home, or null)", async () => {
    mock(true, 200, { active: null, slots: {}, published_url: "https://github.com/u/w" });
    expect((await readAttachedWorkspaces()).published_url).toBe("https://github.com/u/w");
    expect(lastUrl()).toBe("/api/workspace/attached");
    mock(true, 200, { active: null, slots: {}, published_url: null });
    expect((await readAttachedWorkspaces()).published_url).toBeNull();
  });
  it("renameWorkspace POSTs {slug,name} to /api/workspace/rename", async () => {
    mock(true, 200, { active: "seed", slots: {} });
    await renameWorkspace("seed", "Home");
    expect(lastUrl()).toBe("/api/workspace/rename");
    expect(lastBody()).toEqual({ slug: "seed", name: "Home" });
  });

  // ── the additive active set (WP-A2.1) ──────────────────────────────────────
  it("readActiveSet GETs /api/workspace/active — the ordered mount set (primary first)", async () => {
    mock(true, 200, { subject: "u1", active: [{ slug: "seed", repo: null, ref: null, role: "private", path: "/w/u1", write: true, primary: true }] });
    const s = await readActiveSet();
    expect(lastUrl()).toBe("/api/workspace/active");
    expect(s.active[0].primary).toBe(true);
  });
  it("activateWorkspace POSTs repo/ref/slug/token to /api/workspace/activate (ADD to the set)", async () => {
    mock(true, 200, { subject: "u1", slug: "shared-x", changed: true, cloned: true, nested: false });
    await activateWorkspace({ repo: "https://h/r.git", ref: "dev", token: "TOK" });
    expect(lastUrl()).toBe("/api/workspace/activate");
    expect(lastBody()).toEqual({ repo: "https://h/r.git", ref: "dev", slug: null, token: "TOK" });
    await activateWorkspace({ slug: "seed" });  // re-activate a parked slot, no repo
    expect(lastBody()).toEqual({ repo: null, ref: null, slug: "seed", token: null });
  });
  it("deactivateWorkspace POSTs {slug} to /api/workspace/deactivate (park)", async () => {
    mock(true, 200, { subject: "u1", slug: "shared-x", changed: true });
    await deactivateWorkspace("shared-x");
    expect(lastUrl()).toBe("/api/workspace/deactivate");
    expect(lastBody()).toEqual({ slug: "shared-x" });
  });
  it("deactivating the private baseline now SUCCEEDS (switched off — 200, changed)", async () => {
    mock(true, 200, { subject: "u1", slug: "seed", changed: true });
    await expect(deactivateWorkspace("seed")).resolves.toEqual({ subject: "u1", slug: "seed", changed: true });
  });
  it("FAIL-LOUD: a genuine backend error still throws (e.g. 400 invalid subject)", async () => {
    mock(false, 400, { detail: "invalid subject" });
    await expect(deactivateWorkspace("seed")).rejects.toBeInstanceOf(ApiError);
  });
});
