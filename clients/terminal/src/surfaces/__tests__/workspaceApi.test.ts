/** Isolation harness — Workspace data-access. Scoped (no subject, P20) + fail-loud (P18): a backend
 *  error throws, a malformed git body throws (never reaches GitSection as a fake GitState), and a 404
 *  file read is the one legit "empty" → null. */
import { describe, it, expect, vi, afterEach } from "vitest";
import { readWorkspaceFile, listWorkspaceTree, readWorkspaceGit, readAttachedWorkspaces, swapWorkspace, renameWorkspace, publishWorkspace } from "../workspaceApi";
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
});
