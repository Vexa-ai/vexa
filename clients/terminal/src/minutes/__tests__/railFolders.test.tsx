import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, within, act } from "@testing-library/react";
import { Rail, SECTION_CAP } from "../Rail";
import { applyFolders, folderOf, foldersFromMarks, moveRows, fetchFolders, FoldersUnavailable, sections, type RailFolders } from "../railFolders";
vi.mock("../AccountBadge", () => ({ AccountBadge: () => null }));
vi.mock("../../surfaces/chatActivity", () => ({ useChatActive: () => false }));

// CHAT FOLDERS (#1783) — founder 2026-10-13: "we need folders like we have here in claude; i should
// be able to move chats between, drag and drop"; and "hidden could be just a folder".
const F: RailFolders = {
  served: true,
  folders: [
    { id: "pinned", name: "Pinned", builtin: true, collapsed: false },
    { id: "routines", name: "Routines", builtin: false, collapsed: false },
    { id: "f-aaaaaaaaaaaa", name: "Clients", builtin: false, collapsed: false },
    { id: "f-bbbbbbbbbbbb", name: "Board", builtin: false, collapsed: false },
    { id: "ungrouped", name: "Ungrouped", builtin: true, collapsed: false },
    { id: "hidden", name: "Hidden", builtin: true, collapsed: true },
  ],
  filings: { "c:p2": "pinned", "c:p1": "pinned", "c:c1": "f-aaaaaaaaaaaa", "c:h": "hidden", "c:gone": "f-000000000000", "c:rx": "ungrouped" },
  order: { pinned: ["c:p1", "c:p2"] },
};
const row = (key: string, extra: Record<string, unknown> = {}) => ({ key, chatId: key.slice(2), label: `Chat ${key}`, whenLabel: "1:00 PM", ...extra }) as any;

describe("railFolders", () => {
  it("files each row: its filing, a routine's default, else Ungrouped", () => {
    expect(folderOf(row("c:c1"), F)).toBe("f-aaaaaaaaaaaa");
    expect(folderOf(row("c:r", { routine: { flow: "brief" } }), F)).toBe("routines");
    expect(folderOf(row("c:rx", { routine: { flow: "brief" } }), F)).toBe("ungrouped"); // moved out on purpose
    expect(folderOf(row("c:gone"), F)).toBe("ungrouped");                               // its folder was deleted
    expect(folderOf(row("c:r", { routine: { flow: "x" } }), { ...F, folders: F.folders.filter((f) => f.id !== "routines") })).toBe("ungrouped");
    const [p] = applyFolders([row("c:h")], F);
    expect(p).toMatchObject({ folder: "hidden", hidden: true, pinned: false });
  });

  it("draws sections in order, Pinned in its manual order, the rest as they arrive", () => {
    const rows = applyFolders([row("c:p2"), row("c:u1"), row("c:p1"), row("c:c1"), row("c:u2")], F);
    const got = sections(rows, F).map((s) => [s.folder.id, s.rows.map((r) => r.key)]);
    expect(got).toEqual([["pinned", ["c:p1", "c:p2"]], ["routines", []], ["f-aaaaaaaaaaaa", ["c:c1"]],
      ["f-bbbbbbbbbbbb", []], ["ungrouped", ["c:u1", "c:u2"]], ["hidden", []]]);
  });

  it("without folders on the server, the marks are Pinned and Hidden", () => {
    const f = foldersFromMarks({ pinned: ["c:a"], hidden: ["c:b"] });
    expect(f.served).toBe(false);
    expect(f.folders.map((x) => x.id)).toEqual(["pinned", "ungrouped", "hidden"]);
    expect(f.filings).toEqual({ "c:a": "pinned", "c:b": "hidden" });
  });

  it("a server with no session store is told apart from a failure", async () => {
    const f503 = vi.fn(async () => new Response("{}", { status: 503 }));
    await expect(fetchFolders(f503 as any)).rejects.toBeInstanceOf(FoldersUnavailable);
    const ok = vi.fn(async () => new Response(JSON.stringify({ folders: F.folders, filings: { "c:a": "hidden" }, order: {} }), { status: 200 }));
    const got = await moveRows(["c:a"], "hidden", undefined, ok as any);
    expect(got.filings).toEqual({ "c:a": "hidden" });
    expect(JSON.parse((ok.mock.calls[0] as any)[1].body)).toEqual({ rows: ["c:a"], to: "hidden" });
  });
});

function setup(over: Record<string, unknown> = {}, rows = [row("c:p1"), row("c:c1"), row("c:u1"), row("c:h")]) {
  const h = { move: vi.fn(), create: vi.fn(), rename: vi.fn(), del: vi.fn(), collapse: vi.fn(), fmove: vi.fn() };
  const props: any = { rows: applyFolders(rows, F), folders: F, selKey: null, onSelect: () => {}, onNewChat: () => {}, onDeleteChat: () => {},
    onPin: () => {}, onHide: () => {}, onMoveRows: h.move, onFolderCreate: h.create, onFolderRename: h.rename,
    onFolderDelete: h.del, onFolderCollapse: h.collapse, onFolderMove: h.fmove, ...over };
  const view = render(<Rail {...props} />);
  return { ...h, view, props };
}
const sectionEl = (id: string) => document.querySelector(`[data-section="${id}"]`) as HTMLElement;

describe("the rail's sections", () => {
  afterEach(() => { cleanup(); vi.useRealTimers(); });

  it("has no Active/All/Hidden tabs; Hidden is a folded section at the bottom; an empty Routines is not drawn", () => {
    setup();
    expect(screen.queryByRole("tab")).toBeNull();
    const ids = [...document.querySelectorAll("[data-section]")].map((e) => e.getAttribute("data-section"));
    expect(ids).toEqual(["pinned", "f-aaaaaaaaaaaa", "f-bbbbbbbbbbbb", "ungrouped", "hidden"]);
    expect(within(sectionEl("f-bbbbbbbbbbbb")).getByText("Drag chats here")).toBeTruthy();
    const hidden = within(sectionEl("hidden"));
    expect(hidden.getByRole("button", { name: /Hidden/ }).getAttribute("aria-expanded")).toBe("false");
    expect(hidden.queryByText("Chat c:h")).toBeNull();
  });

  it("folds and opens a section, and saves the choice", () => {
    const { collapse } = setup();
    fireEvent.click(within(sectionEl("hidden")).getByRole("button", { name: /Hidden/ }));
    expect(collapse).toHaveBeenCalledWith("hidden", false);
    expect(within(sectionEl("hidden")).getByText("Chat c:h")).toBeTruthy();
    fireEvent.click(within(sectionEl("f-aaaaaaaaaaaa")).getByRole("button", { name: /^Clients/ }));
    expect(collapse).toHaveBeenLastCalledWith("f-aaaaaaaaaaaa", true);
    expect(screen.queryByText("Chat c:c1")).toBeNull();
  });

  it("caps a long section with Show N more, and keeps the selected row in view", () => {
    const many = Array.from({ length: SECTION_CAP + 3 }, (_, i) => row(`c:u${i}`));
    setup({ selKey: `c:u${SECTION_CAP + 2}` }, many);
    const sec = within(sectionEl("ungrouped"));
    expect(sec.getAllByRole("button", { name: /^Chat / }).length).toBe(SECTION_CAP + 1);
    expect(sec.getByText(`Chat c:u${SECTION_CAP + 2}`)).toBeTruthy();
    fireEvent.click(sec.getByRole("button", { name: "Show 2 more" }));
    expect(sec.getAllByRole("button", { name: /^Chat / }).length).toBe(SECTION_CAP + 3);
    expect(sec.getByRole("button", { name: "Show less" })).toBeTruthy();
  });

  it("the sections drawn do not change while something is dragged", () => {
    setup({}, [row("c:u1")]);
    const ids = () => [...document.querySelectorAll("[data-section]")].map((e) => e.getAttribute("data-section"));
    const before = ids();
    fireEvent.dragStart(document.querySelector('[data-row-key="c:u1"]') as HTMLElement, { dataTransfer: { setData: () => {}, effectAllowed: "", dropEffect: "" } });
    expect(ids()).toEqual(before);
    expect(before).toEqual(["pinned", "f-aaaaaaaaaaaa", "f-bbbbbbbbbbbb", "ungrouped", "hidden"]);
  });

  it("drags a chat onto a folder, and onto a pinned row to place it", () => {
    const { move } = setup();
    const dt = { setData: () => {}, effectAllowed: "", dropEffect: "" };
    const u1 = document.querySelector('[data-row-key="c:u1"]') as HTMLElement;
    fireEvent.dragStart(u1, { dataTransfer: dt });
    fireEvent.dragOver(sectionEl("f-aaaaaaaaaaaa"), { dataTransfer: dt });
    fireEvent.drop(sectionEl("f-aaaaaaaaaaaa"), { dataTransfer: dt });
    expect(move).toHaveBeenCalledWith(["c:u1"], "f-aaaaaaaaaaaa", undefined);
    fireEvent.dragStart(u1, { dataTransfer: dt });
    fireEvent.drop(document.querySelector('[data-row-key="c:p1"]') as HTMLElement, { dataTransfer: dt });
    expect(move).toHaveBeenLastCalledWith(["c:u1"], "pinned", "c:p1");
  });

  it("drags a folder onto another to reorder", () => {
    const { fmove } = setup();
    const dt = { setData: () => {}, effectAllowed: "", dropEffect: "" };
    const head = sectionEl("f-aaaaaaaaaaaa").querySelector(".vx-rail-section-head") as HTMLElement;
    fireEvent.dragStart(head, { dataTransfer: dt });
    fireEvent.drop(sectionEl("f-bbbbbbbbbbbb"), { dataTransfer: dt });
    expect(fmove).toHaveBeenCalledWith("f-aaaaaaaaaaaa", 2);
  });

  it("on a phone, a long press lifts the chat and the finger drops it", () => {
    vi.useFakeTimers();
    const { move } = setup();
    const u1 = document.querySelector('[data-row-key="c:u1"]') as HTMLElement;
    const target = sectionEl("pinned") ?? sectionEl("f-aaaaaaaaaaaa");
    const prev = document.elementFromPoint;
    document.elementFromPoint = () => sectionEl("f-aaaaaaaaaaaa");
    fireEvent.touchStart(u1, { touches: [{ clientX: 10, clientY: 10 }] });
    act(() => { vi.advanceTimersByTime(500); });
    fireEvent.touchMove(u1, { touches: [{ clientX: 10, clientY: 80 }] });
    fireEvent.touchEnd(u1, { changedTouches: [{ clientX: 10, clientY: 80 }] });
    document.elementFromPoint = prev;
    expect(target).toBeTruthy();
    expect(move).toHaveBeenCalledWith(["c:u1"], "f-aaaaaaaaaaaa", undefined);
  });

  it("a finger that moves before the long press is a scroll and lifts nothing", () => {
    vi.useFakeTimers();
    const { move } = setup();
    const u1 = document.querySelector('[data-row-key="c:u1"]') as HTMLElement;
    fireEvent.touchStart(u1, { touches: [{ clientX: 10, clientY: 10 }] });
    fireEvent.touchMove(u1, { touches: [{ clientX: 10, clientY: 60 }] });
    act(() => { vi.advanceTimersByTime(500); });
    fireEvent.touchEnd(u1, { changedTouches: [{ clientX: 10, clientY: 60 }] });
    expect(move).not.toHaveBeenCalled();
  });

  it("Move to… files a chat from the keyboard, Hidden included, or into a new folder", () => {
    const { move, create } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Actions for Chat c:u1" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Move to/ }));
    const dlg = screen.getByRole("dialog");
    expect(within(dlg).queryByRole("button", { name: "Ungrouped" })).toBeNull();   // where it already is
    fireEvent.click(within(dlg).getByRole("button", { name: "Hidden" }));
    expect(move).toHaveBeenCalledWith(["c:u1"], "hidden", undefined);
    fireEvent.click(screen.getByRole("button", { name: "Actions for Chat c:u1" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Move to/ }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "New folder…" }));
    fireEvent.change(screen.getByLabelText("Folder name"), { target: { value: "Hiring" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(create).toHaveBeenCalledWith("Hiring", ["c:u1"]);
  });

  it("Hide and Unhide in the row menu are moves to and from Hidden", () => {
    const { move } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Actions for Chat c:u1" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^Hide/ }));
    expect(move).toHaveBeenCalledWith(["c:u1"], "hidden", undefined);
  });

  it("renames a folder and deletes it with a confirm that says its chats stay", () => {
    const { rename, del } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Actions for folder Clients" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Rename/ }));
    fireEvent.change(screen.getByLabelText("Folder name"), { target: { value: "Customers" } });
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    expect(rename).toHaveBeenCalledWith("f-aaaaaaaaaaaa", "Customers");
    fireEvent.click(screen.getByRole("button", { name: "Actions for folder Clients" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Delete folder/ }));
    expect(screen.getByText(/Its chats move to Recent. No chat is deleted./)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Delete folder" }));
    expect(del).toHaveBeenCalledWith("f-aaaaaaaaaaaa");
  });

  it("New folder in the header makes one", () => {
    const { create } = setup();
    fireEvent.click(screen.getByRole("button", { name: "New folder" }));
    fireEvent.change(screen.getByLabelText("Folder name"), { target: { value: "Board" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(create).toHaveBeenCalledWith("Board", undefined);
  });
});
