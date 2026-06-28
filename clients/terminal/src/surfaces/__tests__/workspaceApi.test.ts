/** Isolation harness — Workspace data-access. Scoped (no subject, P20) + fail-loud (P18): a backend
 *  error throws, a malformed git body throws (never reaches GitSection as a fake GitState), and a 404
 *  file read is the one legit "empty" → null. */
import { describe, it, expect, vi, afterEach } from "vitest";
import { readWorkspaceFile, listWorkspaceTree, readWorkspaceGit } from "../workspaceApi";
import { ApiError } from "../apiClient";

let fetchMock: ReturnType<typeof vi.fn>;
const lastUrl = () => String(fetchMock.mock.calls.at(-1)![0]);
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
});
