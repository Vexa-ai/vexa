/** A FOLDER BECOMES A WORKSPACE — the browser's half (folderDrop.ts, FolderImport.tsx).
 *
 *  The rules are the server's; these pin what the browser does with them: it walks a drop without
 *  entering dependency folders, reads the git remote, asks for the plan, shows it (count, size, what
 *  is skipped and why, a refusal), sends the bytes in batches, and says the outcome in the chat. A
 *  folder dropped on the chat opens the sheet; files dropped on it attach exactly as before. The
 *  phone's way in is a folder picker on the workspace menu. The wire is faked; nothing else is. */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor, act } from "@testing-library/react";
import {
  batches, folderFromPicked, parseGitRemote, walkDirectory, droppedDirectories, skippedGroups,
  FOLDER_IMPORT_EVENT, FOLDER_IMPORTED_EVENT, type EntryLike, type DroppedFolder, type FolderPlan,
} from "../folderDrop";
import { FolderImportHost, FolderImportSheet } from "../FolderImport";
import { FileDropZone } from "../attachments";

const file = (name: string, body = "x", type = "") => new File([body], name, { type });
const picked = (rel: string, body = "x") => {
  const f = file(rel.split("/").pop()!, body);
  Object.defineProperty(f, "webkitRelativePath", { value: rel });
  return f;
};

function fileEntry(name: string, body = "x"): EntryLike {
  return { isFile: true, isDirectory: false, name, file: (ok) => ok(file(name, body)) };
}
function dirEntry(name: string, children: EntryLike[]): EntryLike {
  return {
    isFile: false, isDirectory: true, name,
    createReader: () => { let done = false; return { readEntries: (ok) => { const out = done ? [] : children; done = true; ok(out); } }; },
  };
}

const PLAN: FolderPlan = {
  plan_id: "a".repeat(32), name: "notes", target: null,
  accepted: [{ path: "README.md", size: 10 }, { path: "kg/a.md", size: 2048 }],
  skipped: [{ path: ".env", reason: ".env file: never imported" }, { path: "node_modules/", reason: "dependency or build folder" }],
  counts: { ".env file: never imported": 1, "dependency or build folder": 1 },
  total_bytes: 2058, files: 2, blocked: null,
  limits: { max_bytes: 200 * 1024 * 1024, max_files: 10000, file_max_bytes: 25 * 1024 * 1024 },
};

const folder = (over: Partial<DroppedFolder> = {}): DroppedFolder => ({
  name: "notes", files: [{ path: "README.md", file: file("README.md", "# hi") }, { path: "kg/a.md", file: file("a.md") }],
  pruned: ["node_modules"], gitRemote: null, ...over,
});

type Call = { url: string; init?: RequestInit };
let calls: Call[] = [];
function fakeFetch(answers: Record<string, unknown>) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const key = Object.keys(answers).find((k) => url.includes(k));
    const body = key ? answers[key] : { detail: "unexpected" };
    const status = (body as { __status?: number })?.__status ?? (key ? 200 : 404);
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  });
}

beforeEach(() => { calls = []; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("reading a folder", () => {
  it("finds the origin remote in a .git/config", () => {
    expect(parseGitRemote('[core]\n\tbare = false\n[remote "upstream"]\n\turl = https://example.org/u.git\n[remote "origin"]\n\turl = git@example.org:me/notes.git\n'))
      .toBe("git@example.org:me/notes.git");
    expect(parseGitRemote("[core]\n")).toBeNull();
  });

  it("walks a dropped folder without entering dependency folders, and reads its remote", async () => {
    const root = dirEntry("notes", [
      fileEntry("README.md"),
      dirEntry("kg", [fileEntry("a.md")]),
      dirEntry("node_modules", [fileEntry("never-read.js")]),
      dirEntry(".git", [fileEntry("config", '[remote "origin"]\n url = https://example.org/me/notes.git\n'), fileEntry("HEAD")]),
    ]);
    const f = await walkDirectory(root);
    expect(f.name).toBe("notes");
    expect(f.files.map((x) => x.path).sort()).toEqual(["README.md", "kg/a.md"]);
    expect(f.pruned.sort()).toEqual([".git", "node_modules"]);
    expect(f.gitRemote).toBe("https://example.org/me/notes.git");
  });

  it("reads a picked folder the same way (the phone's path)", async () => {
    const f = await folderFromPicked([
      picked("notes/README.md"), picked("notes/web/node_modules/x/i.js"), picked("notes/.git/config", '[remote "origin"]\nurl = https://example.org/r.git\n'),
    ]);
    expect(f!.name).toBe("notes");
    expect(f!.files.map((x) => x.path)).toEqual(["README.md"]);
    expect(f!.pruned.sort()).toEqual([".git", "web/node_modules"]);
    expect(f!.gitRemote).toBe("https://example.org/r.git");
  });

  it("tells folders from files in a drop", () => {
    const items = [
      { kind: "file", webkitGetAsEntry: () => dirEntry("notes", []) },
      { kind: "file", webkitGetAsEntry: () => fileEntry("a.png") },
    ];
    const dt = { items: Object.assign(items, { length: 2 }) } as unknown as DataTransfer;
    expect(droppedDirectories(dt).map((d) => d.name)).toEqual(["notes"]);
  });

  it("sends the bytes in request-sized batches", () => {
    const f = (n: number) => ({ file: { size: n } });
    expect(batches([f(5), f(5), f(5)], 10, 100).map((b) => b.length)).toEqual([2, 1]);
    expect(batches([f(50), f(1)], 10, 100).map((b) => b.length)).toEqual([1, 1]);
    expect(batches([f(1), f(1), f(1)], 100, 2).map((b) => b.length)).toEqual([2, 1]);
  });

  it("groups what was skipped by reason", () => {
    expect(skippedGroups(PLAN).map(([r, n]) => [r, n])).toEqual([[".env file: never imported", 1], ["dependency or build folder", 1]]);
  });
});

describe("the confirmation sheet", () => {
  it("shows the name, the count and size, and what is skipped — the .env note visibly", async () => {
    vi.stubGlobal("fetch", fakeFetch({ "/folder/plan": PLAN }));
    render(<FolderImportSheet folder={folder()} onClose={() => {}} />);
    expect(await screen.findByText(/2 files · 2 KB/)).toBeTruthy();
    expect((screen.getByLabelText("Workspace name") as HTMLInputElement).value).toBe("notes");
    expect(screen.getByText(/\.env file: never imported/)).toBeTruthy();
    expect(screen.getByText(/Secrets are never imported/)).toBeTruthy();
    expect(screen.getByText(/Private — only you/)).toBeTruthy();
    const plan = JSON.parse(String(calls[0].init!.body));
    expect(plan.files).toEqual([{ path: "README.md", size: 4 }, { path: "kg/a.md", size: 1 }]);
    expect(plan.pruned).toEqual(["node_modules"]);
  });

  it("a refused plan says why and cannot be imported", async () => {
    vi.stubGlobal("fetch", fakeFetch({ "/folder/plan": { ...PLAN, blocked: "12000 files to import; the limit is 10000." } }));
    render(<FolderImportSheet folder={folder()} onClose={() => {}} />);
    expect(await screen.findByText(/the limit is 10000/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Import" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("imports with the edited name, then says what it made, with a link, and tells the chat", async () => {
    vi.stubGlobal("fetch", fakeFetch({
      "/folder/plan": PLAN,
      "/files": { received: ["README.md", "kg/a.md"], skipped: [] },
      "/finish": { slug: "workspace-3", workspace: "workspace-3", name: "Field notes", path: "", files: 2, bytes: 2058,
        skipped: PLAN.skipped, skipped_total: 2, commit: "abc", new: true },
    }));
    const heard: unknown[] = [];
    const on = (e: Event) => heard.push((e as CustomEvent).detail);
    window.addEventListener(FOLDER_IMPORTED_EVENT, on);
    const done = vi.fn();
    render(<FolderImportSheet folder={folder()} onClose={() => {}} onImported={done} />);
    const name = await screen.findByLabelText("Workspace name");
    fireEvent.change(name, { target: { value: "Field notes" } });
    fireEvent.click(screen.getByRole("button", { name: "Import" }));
    expect(await screen.findByText(/Created/)).toBeTruthy();
    const link = screen.getByRole("link", { name: /Field notes/ }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toContain("workspace-3");
    const replan = calls.filter((c) => c.url.includes("/folder/plan")).pop()!;
    expect(JSON.parse(String(replan.init!.body)).name).toBe("Field notes");
    const upload = calls.find((c) => c.url.includes("/files"))!;
    expect((upload.init!.body as FormData).getAll("paths")).toEqual(["README.md", "kg/a.md"]);
    expect(done).toHaveBeenCalledWith(expect.objectContaining({ slug: "workspace-3" }));
    expect(heard).toHaveLength(1);
    window.removeEventListener(FOLDER_IMPORTED_EVENT, on);
  });

  it("a repository with a remote offers linking it first, through the existing import", async () => {
    vi.stubGlobal("fetch", fakeFetch({
      "/folder/plan": PLAN,
      "/api/workspace/import": { operation_id: "op", status: "completed", result: { workspace: "notes-abc", cloned: true, changed: true, repo: "https://example.org/me/notes.git", ref: "main" } },
    }));
    render(<FolderImportSheet folder={folder({ gitRemote: "https://example.org/me/notes.git" })} onClose={() => {}} />);
    const buttons = await screen.findAllByRole("button");
    const link = screen.getByRole("button", { name: /Link to https:\/\/example.org\/me\/notes.git/ });
    expect(buttons.indexOf(link)).toBeLessThan(buttons.indexOf(screen.getByRole("button", { name: "Import" })));
    fireEvent.click(link);
    await waitFor(() => expect(calls.some((c) => c.url === "/api/workspace/import")).toBe(true));
    const sent = JSON.parse(String(calls.find((c) => c.url === "/api/workspace/import")!.init!.body));
    expect(sent.repo).toBe("https://example.org/me/notes.git");
  });

  it("dropped onto a workspace, it offers adding into that workspace instead", async () => {
    vi.stubGlobal("fetch", fakeFetch({ "/folder/plan": { ...PLAN, target: { slug: "team", into: "notes" } } }));
    render(<FolderImportSheet folder={folder()} into={{ slug: "team", label: "Team" }} onClose={() => {}} />);
    expect(await screen.findByRole("radio", { name: /Add into Team\/notes/ })).toBeTruthy();
    expect((screen.getByRole("radio", { name: /Add into Team\/notes/ }) as HTMLInputElement).checked).toBe(true);
    const plan = JSON.parse(String(calls[0].init!.body));
    expect([plan.slug, plan.into]).toEqual(["team", "notes"]);
  });
});

describe("where a folder can land", () => {
  it("a folder dropped on the chat zone opens the import; plain files still attach", () => {
    const onFiles = vi.fn(), onFolders = vi.fn();
    render(<FileDropZone onFiles={onFiles} onFolders={onFolders}><div data-testid="inside">chat</div></FileDropZone>);
    const png = file("a.png", "x", "image/png");
    const folderStub = file("notes");   // a browser lists a folder in `files` too, with a size
    const items = [
      { kind: "file", webkitGetAsEntry: () => dirEntry("notes", []), getAsFile: () => folderStub },
      { kind: "file", webkitGetAsEntry: () => fileEntry("a.png"), getAsFile: () => png },
    ];
    fireEvent.drop(screen.getByTestId("inside"), {
      dataTransfer: { types: ["Files"], files: [folderStub, png], items: Object.assign(items, { length: 2 }) },
    });
    expect(onFolders).toHaveBeenCalledTimes(1);
    expect(onFolders.mock.calls[0][0].map((e: EntryLike) => e.name)).toEqual(["notes"]);
    expect(onFiles).toHaveBeenCalledWith([png]);
  });

  it("the host opens the sheet for a folder dropped anywhere else on the app", async () => {
    vi.stubGlobal("fetch", fakeFetch({ "/folder/plan": PLAN }));
    render(<FolderImportHost />);
    await act(async () => {
      window.dispatchEvent(new CustomEvent(FOLDER_IMPORT_EVENT, { detail: { kind: "folder", folder: folder() } }));
    });
    expect(await screen.findByRole("dialog", { name: "New workspace from folder" })).toBeTruthy();
  });

  it("the host also answers a window-level drop of a folder (rail, empty state)", async () => {
    vi.stubGlobal("fetch", fakeFetch({ "/folder/plan": PLAN }));
    render(<FolderImportHost />);
    const items = [{ kind: "file", webkitGetAsEntry: () => dirEntry("notes", [fileEntry("a.md")]) }];
    const ev = new Event("drop", { bubbles: true, cancelable: true }) as Event & { dataTransfer: unknown };
    ev.dataTransfer = { types: ["Files"], files: [], items: Object.assign(items, { length: 1 }) };
    await act(async () => { window.dispatchEvent(ev); });
    expect(await screen.findByRole("dialog", { name: "New workspace from folder" })).toBeTruthy();
    expect(ev.defaultPrevented).toBe(true);
  });
});
