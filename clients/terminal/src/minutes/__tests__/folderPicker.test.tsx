/** "NEW WORKSPACE FROM FOLDER…" — the way in where a folder cannot be dragged (a phone).
 *
 *  The workspace menu carries the entry; it opens the system folder picker (`webkitdirectory`), and
 *  the picked folder goes to the same sheet a drop opens. The sheet and the server are pinned in
 *  `surfaces/__tests__/folderDrop.test.tsx` and `core/agent/tests/test_folder_import.py`. */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, fireEvent, act, screen, waitFor } from "@testing-library/react";
import { ContextBar } from "../ContextBar";

vi.mock("../../ui-kit/WsLink", () => ({ WorkspaceName: ({ slug }: { slug: string }) => <span>{slug}</span> }));
vi.mock("../../ui-kit/wsLinks", () => ({
  workspaceBySlug: async (slug: string) => ({ id: "aaaaaaaaaa", name: slug, kind: "group", slug, access: "readable", writable: true }),
}));

afterEach(() => { cleanup(); });
const settle = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });
const sel = { kind: "chat" as const, chatId: "c1", label: "A chat", workspaces: ["personal"], target: undefined };

const picked = (rel: string) => {
  const f = new File(["x"], rel.split("/").pop()!);
  Object.defineProperty(f, "webkitRelativePath", { value: rel });
  return f;
};

describe("the folder picker", () => {
  it("is on the workspace menu, opens a directory picker, and hands the folder on", async () => {
    const onFolderPicked = vi.fn();
    render(<ContextBar sel={sel} flavor="chat" memberships={[]} onAddWorkspace={vi.fn()}
      onRemoveWorkspace={vi.fn()} onFolderPicked={onFolderPicked} />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Workspaces for this chat" }));
    const item = document.querySelector('[data-ctx="folder"]') as HTMLElement;
    expect(item.textContent).toMatch(/New workspace from folder/);

    const input = document.querySelector("[data-folder-picker]") as HTMLInputElement;
    expect(input.type).toBe("file");
    expect(input.hasAttribute("webkitdirectory")).toBe(true);
    const click = vi.spyOn(input, "click").mockImplementation(() => {});
    fireEvent.click(item);
    expect(click).toHaveBeenCalled();

    fireEvent.change(input, { target: { files: [picked("notes/README.md"), picked("notes/node_modules/x.js")] } });
    await waitFor(() => expect(onFolderPicked).toHaveBeenCalled());
    const folder = onFolderPicked.mock.calls[0][0];
    expect(folder.name).toBe("notes");
    expect(folder.files.map((f: { path: string }) => f.path)).toEqual(["README.md"]);
  });

  it("is absent where nobody wired it", async () => {
    render(<ContextBar sel={sel} flavor="chat" memberships={[]} onAddWorkspace={vi.fn()} onRemoveWorkspace={vi.fn()} />);
    await settle();
    expect(document.querySelector("[data-folder-picker]")).toBeNull();
  });
});
